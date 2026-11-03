"""Create an offline Korean cost dashboard from declared accounting records.

Run ``python -m beauty_ai.cost_reporting --dataset DATA.json --output DIR``.
The optional comparison enables evidence-gated unit costs. Provider, model and
W&B modules are never imported for local reports. No generated images are read.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .cost_accounting import canonical_bytes, latest_records, normalize_dataset, usd_string
from .cost_savings import COMPARISON_SCHEMA_VERSION, compare_costs
from .generation_costs import compare_generations, normalize_generation_manifest
from .errors import GatewayError
from .work_contracts import MAX_JSON_BYTES as _MAX_WORK_JSON_BYTES, normalize_work_dataset, parse_work_json
from .work_metrics import summarize_work_dataset


REPORT_SCHEMA_VERSION = "beautyai-cost-report-v1"
CONTRACT_VERSION = "beautyai-finops-v1"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_REASONS = {
    "COMPARISON_NOT_PROVIDED": "비교 조건이 등록되지 않았습니다.",
    "ACCEPTANCE_NOT_RECORDED": "채택 기록이 없어 채택 수를 확인할 수 없습니다.",
    "NO_ACCEPTED_ADS": "양쪽 제품군에 품질 기준을 통과한 채택 광고가 필요합니다.",
    "NO_COST_ITEMS": "비용 기록이 없습니다.",
    "ESTIMATED_COST_MISSING": "단가 근거가 있는 추정 비용이 일부 없습니다.",
    "ACTUAL_COST_MISSING": "실제 청구 금액이 일부 확인되지 않았습니다.",
    "ACTUAL_COST_UNRECONCILED": "미정산 또는 부분 청구 항목이 있습니다.",
    "BILLING_EVIDENCE_MISSING": "청구 근거가 연결되지 않은 항목이 있습니다.",
    "ESTIMATE_RATE_MISMATCH": "같은 단가 기준으로 비교할 수 없습니다.",
    "COST_SCOPE_MISMATCH": "선언된 비용 범위와 기록이 일치하지 않습니다.",
    "QUALITY_POLICY_MISMATCH": "채택 품질 기준이 일치하지 않습니다.",
    "ARTIFACT_PRODUCT_MISMATCH": "동일 결과물의 제품 귀속이 충돌합니다.",
    "PRODUCT_MIX_MISMATCH": "제품별 채택 비중이 달라 감소율을 비교할 수 없습니다.",
    "BASELINE_ZERO_COST": "기준군 비용이 0이므로 감소율을 계산할 수 없습니다.",
}
_GENERATION_REASONS = {
    "COST_MISSING": "추정 비용이 확인되지 않았습니다.",
    "RATE_MISMATCH": "선택한 단가 기준과 다릅니다.",
    "NOT_SUCCEEDED": "성공한 생성 기록이 아닙니다.",
    "NOT_IMAGE": "이미지 생성 비용 기록이 아닙니다.",
    "PAIRED_RECORD_EXCLUDED": "연결된 상대 기록이 조건을 충족하지 않아 함께 제외했습니다.",
    "UNPAIRED_RECORD": "비교 쌍에 배정되지 않았습니다.",
    "NO_COMPARABLE_PAIRS": "조건을 충족한 비교 쌍이 없습니다.",
    "BASELINE_ZERO_COST": "기준군 평균 비용이 0이므로 감소율을 계산할 수 없습니다.",
}


def _digest(value) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def _coverage(items: list[dict], basis: str) -> dict:
    amounts = [item[f"{basis}_cost_usd"] for item in items]
    known = sum(value is not None for value in amounts)
    complete = sum(value is not None and (
        basis == "estimated" or (item["billing_status"] == "RECONCILED"
                                 and item["billing_evidence_ref"] is not None))
        for item, value in zip(items, amounts))
    reasons = set()
    if not items:
        reasons.add("NO_COST_ITEMS")
    if known < len(items):
        reasons.add(f"{basis.upper()}_COST_MISSING")
    if basis == "actual":
        if any(item["billing_status"] != "RECONCILED" for item in items):
            reasons.add("ACTUAL_COST_UNRECONCILED")
        if any(item["billing_evidence_ref"] is None for item in items):
            reasons.add("BILLING_EVIDENCE_MISSING")
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        subtotal = sum((Decimal(value) for value in amounts if value is not None), Decimal(0))
        return {
            "total_cost_usd": usd_string(subtotal) if items and complete == len(items) else None,
            "known_partial_usd": usd_string(subtotal),
            "cost_item_count": len(items), "known_cost_count": known,
            "complete_cost_count": complete, "missing_cost_count": len(items) - complete,
            "unknown_cost_count": len(items) - known,
            "partial_cost_count": sum(item["billing_status"] == "PARTIAL"
                                      and item["actual_cost_usd"] is not None for item in items)
                                  if basis == "actual" else 0,
            "coverage_pct": usd_string(Decimal(complete) / len(items) * 100) if items else None,
            "reason_codes": sorted(reasons),
        }


def _overview(items: list[dict], history: dict) -> dict:
    acceptances = latest_records(history["acceptances"], "ad_id")
    accepted = {item["artifact_sha256"] for item in acceptances
                if item["action"] == "accept" and item["quality_pass"]}
    result = {
        "cost_item_count": len(items), "cost_revision_count": len(history["cost_items"]),
        "batch_count": len(history["batches"]),
        "acceptance_revision_count": len(history["acceptances"]),
        "latest_acceptance_count": len(acceptances),
        "accepted_ads": len(accepted) if acceptances else None,
        "acceptance_status": "DECLARED" if acceptances else "NOT_RECORDED",
        "reason_codes": [] if acceptances else ["ACCEPTANCE_NOT_RECORDED"],
        "state_counts": dict(sorted(Counter(item["state"] for item in items).items())),
        "billing_status_counts": dict(sorted(Counter(item["billing_status"] for item in items).items())),
        "categories": {},
    }
    for basis in ("estimated", "actual"):
        result[basis] = _coverage(items, basis)
    for category in ("production", "research"):
        subset = [item for item in items if item["category"] == category]
        result["categories"][category] = {
            "cost_item_count": len(subset),
            **{basis: _coverage(subset, basis) for basis in ("estimated", "actual")},
        }
    return result


def _empty_comparison(overview: dict, scope: dict) -> dict:
    reasons = ["COMPARISON_NOT_PROVIDED", *overview["reason_codes"]]
    result = {
        "schema_version": "beautyai-cost-savings-v1", "comparison_id": None,
        **{key: scope[key] for key in ("accounting_window_id", "cost_scope", "quality_policy_version",
                                       "estimate_rate_version", "scope_authority", "quality_authority")},
        "counterfactual_is_actual_cash_difference": False, "per_product_counts": [],
        "research": overview["categories"]["research"],
    }
    for basis in ("estimated", "actual"):
        result[basis] = {
            arm: {"total_cost_usd": None, "known_partial_usd": "0.000000000",
                  "accepted_ads": None, "unit_cost_usd": None, "cost_item_count": 0,
                  "known_cost_count": 0, "missing_cost_count": 0, "missing_cost_ids": [],
                  "missing_batch_count": 0, "missing_batch_ids": [],
                  "reason_codes": sorted(reasons)}
            for arm in ("baseline", "candidate")
        }
        result[basis].update(unit_cost_reduction_pct=None, counterfactual_difference_usd=None,
                             reason_codes=sorted(reasons))
    return result


def build_report(dataset: Mapping, comparison: Mapping | None = None, *,
                 generation_comparison: Mapping | None = None,
                 work_dataset: Mapping | None = None) -> dict:
    """Build a deterministic report without IO or inferred acceptance/billing.

    Identity hashes canonical normalized input history + comparison + contract.
    Corrections affect identity even when the latest totals happen to be equal.
    ``synthetic-``/``fixture-`` comparison IDs explicitly mark test examples.
    """
    normalized = normalize_dataset(dataset)
    items = latest_records(normalized["cost_items"], "cost_id")
    result = compare_costs(normalized, comparison) if comparison is not None else None
    manifest = None
    if result is not None:
        manifest = {
            "schema_version": COMPARISON_SCHEMA_VERSION,
            **{key: result[key] for key in ("comparison_id", "accounting_window_id", "cost_scope",
                                           "quality_policy_version", "estimate_rate_version")},
            "pairs": [{key: pair[key] for key in ("product_id", "baseline_batch_id", "candidate_batch_id")}
                      for pair in result["per_product_counts"]],
        }
    scope = {
        "cost_scope": result["cost_scope"] if result else sorted({item["kind"] for item in items}),
        "accounting_window_id": result["accounting_window_id"] if result else None,
        "quality_policy_version": result["quality_policy_version"] if result else None,
        "estimate_rate_version": result["estimate_rate_version"] if result else None,
        "observed_rate_versions": sorted({item["rate_version"] for item in items
                                           if item["rate_version"] is not None}),
        "scope_authority": "DECLARED_SCOPE", "quality_authority": "DECLARED_QUALITY",
        "complete_account_coverage_verified": False,
        "human_review_verified": False,
    }
    overview = _overview(items, normalized)
    synthetic = bool(manifest and re.match(r"^(synthetic|fixture)[-:./]", manifest["comparison_id"], re.I))
    if manifest is None and items:
        synthetic = all(re.match(r"^(synthetic|fixture)[-:./]", item["source_ref"], re.I) for item in items)
    identity = {"contract_version": CONTRACT_VERSION, "dataset": normalized, "comparison": manifest}
    generation_manifest = (normalize_generation_manifest(generation_comparison)
                           if generation_comparison is not None else None)
    generation_result = (compare_generations(normalized, generation_manifest)
                         if generation_manifest is not None else None)
    if generation_manifest is not None:
        identity["generation_comparison"] = generation_manifest
    normalized_work = normalize_work_dataset(work_dataset) if work_dataset is not None else None
    if normalized_work is not None:
        identity["work_dataset"] = normalized_work
        has_existing_data = any(normalized[key] for key in ("cost_items", "batches", "acceptances"))
        has_existing_data = has_existing_data or manifest is not None or generation_manifest is not None
        work_synthetic = normalized_work["data_kind"] == "SYNTHETIC_FIXTURE"
        existing_synthetic = synthetic and all(
            re.match(r"^(synthetic|fixture)[-:./]", item["source_ref"], re.I) for item in items)
        synthetic = work_synthetic and (existing_synthetic or not has_existing_data)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION, "contract_version": CONTRACT_VERSION,
        "report_id": _digest(identity), "mode": "COMPARISON" if manifest else "OVERVIEW",
        "data_kind": "SYNTHETIC_FIXTURE" if synthetic else "DECLARED_DATA",
        "inputs": {"dataset_sha256": _digest(normalized),
                   "comparison_sha256": _digest(manifest) if manifest else None},
        "scope": scope, "overview": overview,
        "comparison": result if result is not None else _empty_comparison(overview, scope),
        "sources": [{key: item[key] for key in (
            "cost_id", "source_ref", "source_sha256", "category", "kind", "state",
            "estimated_cost_usd", "rate_version", "actual_cost_usd", "billing_status", "billing_evidence_ref",
        )} for item in items],
    }
    if generation_manifest is not None:
        report["inputs"]["generation_comparison_sha256"] = _digest(generation_manifest)
        report["generation_comparison"] = generation_result
    if normalized_work is not None:
        report["inputs"]["work_dataset_sha256"] = _digest(normalized_work)
        report["work_costs"] = summarize_work_dataset(normalized_work)
    return report


def _e(value) -> str:
    return escape(str(value), quote=True)


def _number(value) -> str:
    return "미확인" if value is None else _e(value)


def _decimal_text(value) -> str:
    # Keep all nine significant fractional places when necessary; rendering
    # must not turn a tiny positive nanodollar amount into an apparent zero.
    amount = Decimal(value)
    text = format(amount, ",.9f").rstrip("0").rstrip(".")
    return text if "." in text else text + ".00"


def _money(value) -> str:
    return "미확인" if value is None else "$" + _e(_decimal_text(value))


def _partial(section: Mapping) -> str:
    return _money(section["known_partial_usd"]) if section["known_cost_count"] else "미확인"


def _reason_list(reasons: list[str], success: str = "등록된 비교 조건을 충족했습니다.") -> str:
    if not reasons:
        return f'<p class="good">{_e(success)}</p>'
    return '<ul class="reasons">' + "".join(
        f'<li>{_e(_REASONS.get(reason, reason))} <code>{_e(reason)}</code></li>' for reason in reasons
    ) + "</ul>"


_CSS = """
:root{color-scheme:light;--ink:#172c33;--muted:#5c6e73;--line:#dce4df;--paper:#f3f6f2;--green:#155d49;--soft:#e6f0e8;--amber:#825719}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;line-height:1.65}a{color:inherit}header,main,footer{max-width:1180px;margin:auto;padding:26px 30px}header{display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line)}.brand{font-size:14px;font-weight:800;letter-spacing:.09em}.brand small{display:block;font-size:11px;color:var(--muted);font-weight:500;letter-spacing:.12em}nav{display:flex;gap:24px;font-size:13px}nav a{text-decoration:none}.download{padding:9px 16px;border:1px solid var(--line);border-radius:8px;background:white;font-size:12px;text-decoration:none;white-space:nowrap}h1{font-size:clamp(29px,4.5vw,44px);letter-spacing:-.06em;line-height:1.22;margin:15px 0}h2{font-size:22px;letter-spacing:-.04em;margin:0}h3{font-size:17px;margin:0}p{margin:8px 0}.muted,.sub{color:var(--muted);font-size:13px}.eyebrow{color:var(--green);font-weight:750;font-size:12px;letter-spacing:.08em}.hero{padding:24px 0 20px}.hero p{max-width:760px}.pill{display:inline-block;background:var(--soft);color:var(--green);padding:4px 10px;border-radius:30px;font-size:11px;font-weight:700}.notice{border:1px solid #d7d3e4;border-left:4px solid #73658c;border-radius:9px;padding:14px 18px;background:#f4f1f9;font-size:13px;margin:16px 0 25px}.notice strong{display:block}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.stat,.panel{border:1px solid var(--line);border-radius:13px;background:#fff;padding:21px}.stat .label{font-size:12px;color:var(--muted)}.stat .value{font-size:clamp(21px,3vw,29px);font-weight:750;letter-spacing:-.045em;margin:5px 0;overflow-wrap:anywhere}.stat small{font-size:11px;color:var(--muted)}section{margin:34px 0}.section-head{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-bottom:15px}.grid-two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.basis-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.metric{margin:18px 0 14px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.metric strong{font-size:38px;line-height:1.2;letter-spacing:-.05em;color:var(--green)}.metric strong.wait{font-size:29px;color:var(--amber)}.metric strong.increase{color:#a04330}.metric span{font-size:12px;color:var(--muted)}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid #edf0ed;text-align:right;vertical-align:top}th:first-child,td:first-child{text-align:left}thead th{color:var(--muted);font-weight:500;background:#f7f9f6}tbody tr:last-child th,tbody tr:last-child td{border-bottom:0}.difference{background:var(--paper);padding:12px 14px;border-radius:8px;margin:16px 0 6px;display:flex;justify-content:space-between;gap:12px;font-size:12px}.difference strong{font-size:16px}.cash-note{font-size:11px;color:var(--muted)}.reasons{padding-left:18px;color:var(--amber);font-size:12px;margin:14px 0 0}.reasons code{display:block;color:var(--muted);font-size:10px;overflow-wrap:anywhere}.good{font-size:12px;color:var(--green);margin-top:14px}.scope{display:flex;gap:8px 20px;flex-wrap:wrap;font-size:12px;margin-top:20px;color:var(--muted)}.scope b{font-weight:600;color:var(--ink)}.coverage-line{display:flex;justify-content:space-between;gap:12px;font-size:12px;margin:15px 0 6px}.meter{height:7px;border-radius:4px;background:#e8eeea;overflow:hidden}.meter span{display:block;height:100%;background:#4d9277}.coverage-meta{display:flex;gap:8px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:10px}.coverage-meta span{padding:3px 7px;background:#f5f7f4;border-radius:4px}.source-table{overflow:auto}.source-table table{min-width:800px}.source-table td:first-child{max-width:340px;overflow-wrap:anywhere}.source-table code{display:block;font-size:10px;color:var(--muted);overflow-wrap:anywhere}.source-table th,.source-table td{white-space:nowrap}.source-table td:first-child{white-space:normal}.source-table small{display:block;color:var(--muted);font-size:10px}.research{background:#eef2ed}.research .panel{background:#fbfcfa}.empty{font-size:13px;color:var(--muted);padding:12px 0}details{font-size:12px;color:var(--muted);margin-top:15px}summary{cursor:pointer}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}footer{font-size:11px;color:var(--muted);border-top:1px solid var(--line);overflow-wrap:anywhere;padding-bottom:35px}.ids{font-size:10px;word-break:break-all}.rule{font-size:12px;color:var(--muted);margin:20px 0}
@media(max-width:760px){header,main,footer{padding:20px 18px}header{flex-wrap:wrap;gap:14px}nav{order:3;width:100%;justify-content:space-between;gap:8px}.stats{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.grid-two{grid-template-columns:1fr}.stat,.panel{padding:17px}.section-head{display:block}.section-head p{margin:5px 0}.hero{padding-top:14px}.stat .value{font-size:25px}.metric strong{font-size:34px}.source-table{margin:0 -4px}.scope{display:block}.scope span{display:block;margin:3px 0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:white}nav,.download{display:none}.panel,.stat{break-inside:avoid}header,main,footer{max-width:none}.source-table{overflow:visible}}
"""


def _comparison_panel(section: Mapping, title: str, badge: str) -> str:
    pct = section["unit_cost_reduction_pct"]
    if pct is None:
        metric, state, caption = "산출 보류", "wait", "채택 광고 한 장당 비용 감소율"
    else:
        increased = Decimal(pct) < 0
        metric = _e(_decimal_text(abs(Decimal(pct))).removesuffix(".00")) + "%"
        state, caption = ("increase", "채택 광고 한 장당 비용 증가율") if increased else ("", "채택 광고 한 장당 비용 감소율")
    rows = []
    for label, key in (("총생산비", "total_cost_usd"), ("확인된 부분합", "known_partial_usd"),
                       ("채택 광고", "accepted_ads"), ("한 장당 원가", "unit_cost_usd"),
                       ("비용 확인 / 전체", "coverage"), ("미완결 비용", "missing_cost_count")):
        cells = []
        for arm in ("baseline", "candidate"):
            value = section[arm]
            if key == "known_partial_usd":
                shown = _partial(value)
            elif key == "coverage":
                shown = f'{_number(value["known_cost_count"])} / {_number(value["cost_item_count"])}'
            elif key in ("accepted_ads", "missing_cost_count"):
                shown = _number(value[key])
            else:
                shown = _money(value[key])
            cells.append(f"<td>{shown}</td>")
        rows.append(f"<tr><th scope=\"row\">{label}</th>{''.join(cells)}</tr>")
    missing_batches = "".join(
        f'<p class="cash-note">{label}: 비용 기록이 없는 묶음 {_number(section[arm]["missing_batch_count"])}개 '
        f'({_e(", ".join(section[arm]["missing_batch_ids"]))}). 누락된 비용 항목 수는 알 수 없습니다.</p>'
        for arm, label in (("baseline", "기준군"), ("candidate", "개선군"))
        if section[arm]["missing_batch_count"]
    )
    return f'''<article class="panel"><div class="basis-head"><h3>{title}</h3><span class="pill">{badge}</span></div>
<div class="metric"><strong class="{state}">{metric}</strong><span>{caption}</span></div>
<table><thead><tr><th scope="col">비교 항목</th><th scope="col">기준군</th><th scope="col">개선군</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="difference"><span>기준 원가 적용 시 환산 차이</span><strong>{_money(section['counterfactual_difference_usd'])}</strong></div>
<p class="cash-note">실제 청구 감소액이 아닙니다. (기준 원가 − 개선 원가) × 개선군 채택 수</p>{missing_batches}{_reason_list(section['reason_codes'])}</article>'''


def _coverage_panel(section: Mapping, title: str) -> str:
    percent = section["coverage_pct"]
    width = min(Decimal(100), max(Decimal(0), Decimal(percent))) if percent is not None else Decimal(0)
    return f'''<article class="panel"><h3>{title}</h3><div class="metric"><strong>{_partial(section)}</strong><span>확인된 부분합</span></div>
<p class="sub">완전한 총액: {_money(section['total_cost_usd'])}</p>
<div class="coverage-line"><span>비용 완결성</span><b>{_number(section['complete_cost_count'])} / {_number(section['cost_item_count'])}건</b></div>
<div class="meter" role="img" aria-label="비용 완결성 {_e(width)}퍼센트"><span style="width:{_e(width)}%"></span></div>
<div class="coverage-meta"><span>금액 확인 {_number(section['known_cost_count'])}건</span><span>금액 미확인 {_number(section['unknown_cost_count'])}건</span><span>부분 청구 {_number(section['partial_cost_count'])}건</span></div>
{_reason_list(section['reason_codes'], '등록된 항목의 비용 근거가 모두 있습니다.')}</article>'''


def _generation_reason_list(reasons: list[str]) -> str:
    if not reasons:
        return '<p class="good">모든 기록이 생성 비교 조건을 충족했습니다.</p>'
    return '<ul class="reasons">' + ''.join(
        f'<li>{_e(_GENERATION_REASONS.get(reason, reason))} <code>{_e(reason)}</code></li>'
        for reason in reasons
    ) + '</ul>'


def _generation_section(section: Mapping, *, has_work: bool = False) -> str:
    pct = section["estimated_cost_reduction_pct"]
    if pct is None:
        metric, state, caption = "산출 보류", "wait", "생성당 추정비용 감소율"
    else:
        increased = Decimal(pct) < 0
        metric = _e(_decimal_text(Decimal(pct).copy_abs()).removesuffix(".00")) + "%"
        state = "increase" if increased else ""
        caption = "생성당 추정비용 증가율" if increased else "생성당 추정비용 감소율"
    rows = []
    for label, key in (("비교에 포함된 생성", "generation_count"),
                       ("생성 추정비용 합계", "total_estimated_cost_usd"),
                       ("생성당 추정비용 평균", "estimated_cost_per_generation_usd")):
        cells = ''.join(f'<td>{_number(section[arm][key]) if key == "generation_count" else _money(section[arm][key])}</td>'
                        for arm in ("baseline", "candidate"))
        rows.append(f'<tr><th scope="row">{label}</th>{cells}</tr>')
    coverage = section["coverage_pct"]
    coverage_text = "미확인" if coverage is None else _e(_decimal_text(coverage).removesuffix(".00")) + "%"
    width = min(Decimal(100), max(Decimal(0), Decimal(coverage))) if coverage is not None else Decimal(0)
    excluded_rows = ''.join(
        f'<tr><th scope="row">{_e(record["cost_id"])}</th><td>{_generation_reason_list(record["reason_codes"])}</td></tr>'
        for record in section["excluded_records"]
    )
    excluded_details = (f'<details><summary>제외 기록 {_number(section["excluded_record_count"])}건의 사유</summary>'
                        '<div class="source-table"><table><thead><tr><th scope="col">비용 기록 ID</th>'
                        f'<th scope="col">제외 사유</th></tr></thead><tbody>{excluded_rows}</tbody></table></div></details>') if excluded_rows else ''
    work_notice = ('<div class="notice"><strong>최종 광고 제작 비용은 작업별 누적에서 확인합니다.</strong>이 생성 비교와 작업 원장은 별도 입력입니다. 생성당 수치에 검수·수정·완료 비용을 더한 것으로 해석하지 않습니다.</div>'
                   if has_work else '<div class="notice"><strong>생 프롬프트 → 최종 광고 완료 비용: 미측정</strong>수정·실패·검수까지 포함한 전체 제작 비용과 토큰은 아직 측정하지 않았습니다. 이 수치는 연결된 생성 결과의 관측 비교입니다. 채택 광고 한 장당 원가와 별개입니다.</div>')
    return f'''<section id="generation-comparison"><div class="section-head"><h2>생성당 추정비용 비교</h2><p class="muted">관측 비교 · 품질 미검증</p></div>
<div class="grid-two"><article class="panel"><div class="basis-head"><h3>이미지 생성 1건의 추정비용</h3><span class="pill">단가표 기반 추정</span></div>
<div class="metric"><strong class="{state}">{metric}</strong><span>{caption}</span></div>
<table><thead><tr><th scope="col">비교 항목</th><th scope="col">기준군</th><th scope="col">개선군</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="difference"><span>포함된 생성의 추정비용 합계 차이</span><strong>{_money(section['estimated_cost_difference_usd'])}</strong></div>
<p class="cash-note">생성당 평균 차이: {_money(section['per_generation_difference_usd'])}. 실제 청구 감소액이나 최적화의 인과 효과가 아닙니다.</p></article>
<article class="panel"><h3>비교 범위와 제외 기록</h3>
<div class="metric"><strong>{_number(section['compared_record_count'])}건</strong><span>비교 포함 · 제외 {_number(section['excluded_record_count'])}건</span></div>
<div class="coverage-line"><span>전체 기록 대비 비교 coverage</span><b>{coverage_text}</b></div>
<div class="meter" role="img" aria-label="생성 비교 coverage {_e(width)}퍼센트"><span style="width:{_e(width)}%"></span></div>
<div class="coverage-meta"><span>전체 {_number(section['input_record_count'])}건</span><span>비교 {_number(section['compared_pair_count'])}쌍 / 계획 {_number(section['planned_pair_count'])}쌍</span><span>제외 {_number(section['excluded_pair_count'])}쌍</span></div>
{_generation_reason_list(section['reason_codes'])}{excluded_details}</article></div>
{work_notice}
<p class="rule">동일 품질을 검증한 대조 실험이 아닙니다. 한쪽 기록이 조건을 충족하지 않으면 연결된 양쪽 기록을 함께 제외합니다. 연구 자료도 이 생성 비교에 포함될 수 있습니다.</p>
<p class="ids">비교 ID: {_e(section['comparison_id'])} · 단가 기준: {_e(section['estimate_rate_version'])}<br>근거 수준: {_e(section['assurance'])}</p></section>'''


_WORK_REASONS = {
    "UNPAIRED_WORK": "비교 상대 또는 기준군·개선군 배정이 없습니다.",
    "NOT_COMPLETED": "양쪽 작업의 명시적 완료가 필요합니다.",
    "PRODUCT_MISMATCH": "제품이 다릅니다.", "QUALITY_MISMATCH": "품질 기준이 다릅니다.",
    "STAGES_MISMATCH": "필수 제작 단계가 다릅니다.", "COST_MISSING": "비용이 일부 미확인입니다.",
    "RATE_MISMATCH": "적용 단가 정책이 다르거나 미확인입니다.",
    "DUPLICATE_FINAL_ARTIFACT": "동일 비교군의 최종 파일이 중복됐습니다.",
    "NO_COMPARABLE_PAIRS": "비교 조건을 충족한 완료 쌍이 없습니다.",
    "BASELINE_ZERO_COST": "기준군 비용이 0이므로 감소율을 계산할 수 없습니다.",
}
_WORK_STATES = {"OPEN": "진행 중", "COMPLETED": "완료", "CANCELLED": "취소"}
_WORK_STAGES = {"prompt_build": "기획·프롬프트", "image_generation": "이미지 생성",
                "review": "검수", "revision": "수정", "composition": "합성"}


def _work_reasons(codes) -> str:
    return '<ul class="reasons">' + ''.join(
        f'<li>{_e(_WORK_REASONS.get(code, code))} <code>{_e(code)}</code></li>' for code in codes
    ) + '</ul>' if codes else '<p class="good">같은 제품·품질·단계·단가 정책의 완료 쌍입니다.</p>'


def _work_coverage(row: Mapping) -> str:
    known, missing = row["known_usage_count"], row["missing_usage_count"]
    denominator = known + missing
    if not denominator:
        return "미확인 · 관측 대상 API 호출 없음"
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        pct = usd_string(Decimal(known) * 100 / denominator)
    return f'{_e(_decimal_text(pct).removesuffix(".00"))}% · {_number(known)} / {_number(denominator)}건'


def _work_amount(total, known) -> str:
    return f'{_money(total)}<small>확인된 부분합 {_money(known)}</small>'


def _work_stage_table(stages) -> str:
    rows = ''.join(
        f'<tr><th scope="row">{_e(_WORK_STAGES.get(stage["stage"], stage["stage"]))}<small>{_e(stage["provider"])} / {_e(stage["model"])}</small></th>'
        f'<td>{_number(stage["attempt_count"])}</td><td>{_work_coverage(stage)}</td>'
        f'<td>{_number(stage["input_tokens"])} / {_number(stage["output_tokens"])}<small>부분합 {_number(stage["known_input_tokens"])} / {_number(stage["known_output_tokens"])}</small></td>'
        f'<td>{_number(stage["cached_input_tokens"])} / {_number(stage["reasoning_tokens"])}</td>'
        f'<td>{_work_amount(stage["estimated_total_usd"], stage["known_partial_estimated_usd"])}<small>미확인 {_number(stage["missing_cost_count"])}건</small></td></tr>'
        for stage in stages)
    return '<div class="source-table"><table><thead><tr><th scope="col">단계 / 공급자 / 모델</th><th scope="col">시도</th><th scope="col">토큰 관측률</th><th scope="col">입력 / 출력 토큰</th><th scope="col">캐시 / 추론 토큰</th><th scope="col">추정 비용</th></tr></thead><tbody>' + rows + '</tbody></table></div>'


def _work_section(section: Mapping) -> str:
    summary, comparison = section["summary"], section["comparison"]
    cards = [
        ("전체 작업의 누적 API 추정비용", _money(summary["estimated_total_usd"]),
         "확인된 부분합 " + _money(summary["known_partial_estimated_usd"]) + f' · 비용 미확인 {_number(summary["missing_cost_count"])}건'),
        ("실제 청구 금액", _money(summary["actual_total_usd"]),
         "확인된 부분합 " + _money(summary["known_partial_actual_usd"])),
        ("완료 광고 1건당 전체 생산비", _money(summary["cost_per_completed_ad_usd"]),
         f'고유 최종 파일 {_number(summary["completed_ad_count"])}건 · 실패·취소·진행 비용 포함'),
        ("작업 상태", f'완료 {_number(summary["completed_work_count"])}건',
         f'진행 {_number(summary["open_work_count"])} / 취소 {_number(summary["cancelled_work_count"])} / 전체 {_number(summary["work_count"])}건'),
    ]
    stats = ''.join(f'<article class="stat"><div class="label">{label}</div><div class="value">{value}</div><small>{note}</small></article>'
                    for label, value, note in cards)
    work_rows, details = [], []
    for work in section["works"]:
        work_rows.append(
            f'<tr><th scope="row">{_e(work["product_ref"])}<small>{_e(work["work_id"])}</small><small>쌍 {_number(work["task_pair_id"])} / {_number(work["arm"])}</small></th>'
            f'<td>{_e(_WORK_STATES.get(work["status"], work["status"]))}<small>{_e(work["status"])}</small></td>'
            f'<td>{_work_amount(work["estimated_total_usd"], work["known_partial_estimated_usd"])}</td>'
            f'<td>{_work_amount(work["actual_total_usd"], work["known_partial_actual_usd"])}</td>'
            f'<td>{_number(work["input_tokens"])} / {_number(work["output_tokens"])}<small>부분합 {_number(work["known_input_tokens"])} / {_number(work["known_output_tokens"])}</small></td>'
            f'<td>{_work_coverage(work)}</td><td>{_number(work["failed_count"])} / {_number(work["revision_attempt_count"])}<small>대기 {_number(work["pending_count"])} · 미관측 이미지 {_number(work["unobserved_image_job_count"])}</small></td>'
            f'<td><code>{_number(work["final_artifact_sha256"])}</code></td></tr>')
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            elapsed = sum((Decimal(str(item["receipt"]["elapsed_seconds"])) for item in work["attempts"]
                           if item["receipt"] is not None and item["receipt"]["elapsed_seconds"] is not None), Decimal(0))
        details.append(f'<details class="panel"><summary>{_e(work["product_ref"])} · 단계와 모델별 내역</summary>'
            f'<p class="ids">작업 {_e(work["work_id"])} · 품질 기준 {_number(work["quality_policy_version"])}<br>단가 정책 {_e(", ".join(work["rate_versions"])) or "미확인"}</p>'
            f'<p class="muted">관측된 호출 경과 시간 합계 {_e(format(elapsed, ".3f"))}초 · 작업의 실제 완료 시간과 다릅니다.</p>'
            + _work_stage_table(work["stages"]) + '</details>')
    pct = comparison["estimated_cost_reduction_pct"]
    if pct is None:
        metric, label, state = "산출 보류", "완료쌍 비용 감소율", "wait"
    else:
        increase = Decimal(pct) < 0
        metric = _e(_decimal_text(Decimal(pct).copy_abs()).removesuffix(".00")) + "%"
        label, state = ("완료쌍 비용 증가율", "increase") if increase else ("완료쌍 비용 감소율", "")
    pair_rows = ''.join(
        f'<tr><th scope="row">{label}</th>' + ''.join(
            f'<td>{_number(comparison[arm][key]) if key == "completed_work_count" else _money(comparison[arm][key])}</td>'
            for arm in ("baseline", "candidate")) + '</tr>'
        for label, key in (("비교에 포함된 완료 작업", "completed_work_count"),
                           ("포함된 작업의 추정비용", "total_estimated_cost_usd"),
                           ("비교군의 완료 광고 1건당 비용", "estimated_cost_per_completed_ad_usd")))
    excluded = ''.join(f'<tr><th scope="row">{_number(pair["task_pair_id"])}<small>{_e(", ".join(pair["work_ids"]))}</small></th><td>{_work_reasons(pair["reason_codes"])}</td></tr>'
                       for pair in comparison["excluded_pairs"])
    return f'''<section id="work-costs"><style>#work-costs .grid-two>.panel{{min-width:0}}#work-costs .source-table{{max-width:100%}}#work-costs .work-pair-table{{min-width:0;width:100%;table-layout:fixed}}#work-costs .work-pair-table th,#work-costs .work-pair-table td{{white-space:normal;overflow-wrap:anywhere}}</style><div class="section-head"><h2>광고 작업별 누적 비용</h2><p class="muted">기획부터 최종 파일까지 · 모델 API 비용 범위</p></div>
<div class="stats">{stats}</div><p class="rule">취소·진행 중·실패·수정 비용을 전체 생산비에 포함합니다. 로컬 합성의 CPU·저장공간 가격과 실제 청구를 추정하지 않습니다. 캐시 할인은 반영하지 않은 단가표 추정입니다.</p>
<p class="muted">누적 입력 {_number(summary['input_tokens'])} / 출력 {_number(summary['output_tokens'])} 토큰 · 확인된 부분합 {_number(summary['known_input_tokens'])} / {_number(summary['known_output_tokens'])}. 모델별 사용량은 아래 내역에서 확인하며 다른 모델을 같은 효율 단위로 비교하지 않습니다.</p>
<div class="panel source-table"><table id="work-cost-table"><thead><tr><th scope="col">제품 / 작업</th><th scope="col">상태</th><th scope="col">누적 추정비용</th><th scope="col">실제 청구</th><th scope="col">입력 / 출력 토큰</th><th scope="col">토큰 관측률</th><th scope="col">실패 / 수정</th><th scope="col">최종 파일 SHA-256</th></tr></thead><tbody>{''.join(work_rows)}</tbody></table></div>
{''.join(details)}<div class="section-head"><h3>조건이 같은 완료쌍 비교</h3><p class="muted">전체 생산비와 구분한 진단 비교</p></div><div class="grid-two"><article class="panel"><div class="metric"><strong class="{state}">{metric}</strong><span>{label}</span></div><div class="source-table"><table class="work-pair-table"><colgroup><col style="width:44%"><col style="width:28%"><col style="width:28%"></colgroup><thead><tr><th scope="col">비교 항목</th><th scope="col">기준군</th><th scope="col">개선군</th></tr></thead><tbody>{pair_rows}</tbody></table></div><p class="cash-note">포함된 완료쌍의 추정비용 차이 {_money(comparison['estimated_cost_difference_usd'])}. 제외 작업까지 포함한 전체 생산비 절감이나 실제 현금 절감은 아닙니다.</p></article>
<article class="panel"><h3>비교 범위와 제외 사유</h3><p>비교 {_number(comparison['compared_pair_count'])}쌍 / 계획 {_number(comparison['planned_pair_count'])}쌍 · 제외 {_number(comparison['excluded_pair_count'])}쌍, {_number(comparison['excluded_work_count'])}작업</p>{_work_reasons(comparison['reason_codes'])}<div class="source-table"><table><thead><tr><th scope="col">작업 쌍 / 작업 ID</th><th scope="col">제외 사유</th></tr></thead><tbody>{excluded}</tbody></table></div></article></div>
<p class="rule">완료는 최종 파일 hash·검수자·품질 정책의 명시적 선언입니다. 모델 검수 성공을 법적 승인이나 실제 팀 채택으로 해석하지 않습니다. 상세 영수증과 정확한 금액은 JSON에 함께 보존됩니다.</p></section>'''


def render_report(report: Mapping) -> str:
    """Render a self-contained, script-free HTML page; escape every input label."""
    overview, comparison, scope = report["overview"], report["comparison"], report["scope"]
    synthetic = report["data_kind"] == "SYNTHETIC_FIXTURE"
    marker = "합성 예제 · 계산 검증용" if synthetic else "선언된 입력 자료"
    notice = ("금액·청구 근거·채택 기록은 계산 검증을 위해 만든 자료입니다. 실제 비용 절감 실험 결과가 아닙니다."
              if synthetic else "등록된 자료 범위의 보고서입니다. 전체 계정의 비용 완전성이나 사람 검수의 진위를 자동 인증하지 않습니다.")
    stats = [
        ("수집된 비용 기록", f'{_number(overview["cost_item_count"])}건', f'원본 이력 {_number(overview["cost_revision_count"])}건 · 최신 항목 기준'),
        ("추정 비용 · 확인된 부분합", _partial(overview["estimated"]), f'금액 확인 {_number(overview["estimated"]["known_cost_count"])} / {_number(overview["cost_item_count"])}건'),
        ("실제 청구 · 전체 금액", _money(overview["actual"]["total_cost_usd"]), f'완결 {_number(overview["actual"]["complete_cost_count"])} / {_number(overview["cost_item_count"])}건'),
        ("기록된 고유 채택", _number(overview["accepted_ads"]) + ("건" if overview["accepted_ads"] is not None else ""), "채택 기록 미등록" if overview["accepted_ads"] is None else "최신 승인 · 품질 통과 · 결과물 중복 제외"),
    ]
    cards = "".join(f'<article class="stat"><div class="label">{label}</div><div class="value">{value}</div><small>{note}</small></article>' for label, value, note in stats)
    source_rows = []
    for source in report["sources"]:
        source_rows.append(f'''<tr><td>{_e(source['source_ref'])}<small>{_e(source['cost_id'])}</small><code>{_e(source['source_sha256'])}</code></td>
<td>{_e(source['category'])}<small>{_e(source['kind'])}</small></td><td>{_e(source['state'])}</td><td>{_money(source['estimated_cost_usd'])}<small>단가: {_number(source['rate_version'])}</small></td><td>{_money(source['actual_cost_usd'])}<small>{_e(source['billing_status'])}</small><small>근거: {_number(source['billing_evidence_ref'])}</small></td></tr>''')
    sources = ('<div class="source-table"><table><thead><tr><th scope="col">출처 별칭 / SHA-256</th><th scope="col">범위</th><th scope="col">관측 상태</th><th scope="col">추정 비용</th><th scope="col">청구 금액 / 상태</th></tr></thead><tbody>'
               + "".join(source_rows) + "</tbody></table></div>") if source_rows else '<p class="empty">등록된 비용 출처가 없습니다.</p>'
    research = overview["categories"]["research"]
    mode = "비교 보고서" if report["mode"] == "COMPARISON" else "수집 현황 · 비교 조건 미등록"
    generation = report.get("generation_comparison")
    work = report.get("work_costs")
    work_section = _work_section(work) if work is not None else ""
    work_nav = '<a href="#work-costs">작업별 누적</a>' if work is not None else ""
    work_hash = f'<br>Work dataset SHA-256: {_e(report["inputs"]["work_dataset_sha256"])}' if work is not None else ""
    generation_section = _generation_section(generation, has_work=work is not None) if generation is not None else ""
    generation_nav = '<a href="#generation-comparison">생성 추정비용</a>' if generation is not None else ""
    generation_hash = (f"<br>Generation comparison SHA-256: {_e(report['inputs']['generation_comparison_sha256'])}"
                       if generation is not None else "")
    generation_footer = ("<p>생성당 추정비용은 관측 비교이며 최종 광고 완료까지의 비용은 미측정입니다. 채택 광고 지표의 산출 여부와 별도로 확인합니다.</p>"
                         if generation is not None else "")
    if work is not None and generation is not None:
        generation_footer = "<p>생성당 비교와 작업별 누적은 서로 다른 선언 자료의 지표입니다. 금액을 중복 합산하지 않습니다.</p>"
    generation_mode = "생성당 추정 비교 · 채택 광고 비교 미등록" if report["mode"] == "OVERVIEW" else "생성당 추정 비교 · 채택 광고 비교"
    hero = (f"<span class=\"eyebrow\">{generation_mode}</span><h1>생성 한 건의 추정비용</h1><p class=\"muted\">연결된 이미지 생성의 추정비용을 비교하고, 제외 기록과 근거 범위를 함께 확인합니다. 실제 청구와 채택 광고 원가는 별도 지표입니다.</p>"
            if generation is not None else f'<span class="eyebrow">{mode}</span><h1>광고 한 장의 비용</h1><p class="muted">추정 비용과 확인된 청구를 나누고, 같은 품질 기준에서 채택한 광고의 원가를 비교합니다. 미확인 비용과 채택은 0으로 채우지 않습니다.</p>')
    if work is not None:
        hero = '<span class="eyebrow">광고 제작 원장</span><h1>광고 한 건, 완료까지의 비용</h1><p class="muted">기획·이미지·검수·반복 수정·합성을 한 작업으로 묶어 봅니다. 빠진 사용량과 비용은 미확인으로 남기고, 완료쌍 비교와 전체 생산비를 함께 확인합니다.</p>'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src 'none'; connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'">
<title>BeautyAI · 광고 비용 보고서</title><style>{_CSS}</style></head><body>
<header><div class="brand">BEAUTYAI<small>COST OBSERVATORY</small></div><nav aria-label="보고서 탐색">{work_nav}{generation_nav}<a href="#overview">수집 현황</a><a href="#comparison">원가 비교</a><a href="#research">연구비</a><a href="#sources">출처</a></nav><a class="download" href="report.json" download>JSON 내려받기 ↗</a></header>
<main><div class="hero">{hero}</div>
<div class="notice"><strong>{marker}</strong>{notice}</div>{work_section}<div class="stats">{cards}</div>
<div class="scope"><span>비용 범위 <b>{_e(', '.join(scope['cost_scope'])) or '기록 없음'}</b></span><span>기간 <b>{_number(scope['accounting_window_id'])}</b></span><span>품질 기준 <b>{_number(scope['quality_policy_version'])}</b></span><span>비교 단가 기준 <b>{_number(scope['estimate_rate_version'])}</b></span></div>
{generation_section}<section id="overview"><div class="section-head"><h2>어디까지 확인됐나요?</h2><p class="muted">전체 수집 자료 · 생산비와 연구비 포함</p></div><div class="grid-two">{_coverage_panel(overview['estimated'], '추정 비용')}{_coverage_panel(overview['actual'], '실제 청구')}</div><p class="rule">관측 단가 기준: {_e(', '.join(scope['observed_rate_versions'])) or '미확인'}. 각 항목의 단가 근거는 아래 출처에서 확인할 수 있습니다.</p><p class="rule">금액이 있어도 부분 청구는 완결 비용이 아닙니다. 알려진 부분합은 전체 비용을 뜻하지 않습니다.</p></section>
<section id="comparison"><div class="section-head"><h2>채택 광고 한 장당 원가</h2><p class="muted">선언된 생산비 범위 · 연구비 제외</p></div><div class="grid-two">{_comparison_panel(comparison['estimated'], '추정 비용 기준', '단가표 기반 추정')}{_comparison_panel(comparison['actual'], '실제 청구 기준', '청구 근거 연결')}</div><p class="rule">채택 수, 품질 기준, 비용 완전성, 제품별 채택 비중이 비교 조건을 충족할 때만 감소율을 표시합니다. 재사용 횟수는 절감액으로 더하지 않습니다.</p></section>
<section id="research"><div class="section-head"><h2>연구비는 따로 봅니다</h2><p class="muted">{_number(research['cost_item_count'])}건 · 생산 원가와 감소율에 포함하지 않음</p></div><div class="grid-two research">{_coverage_panel(research['estimated'], '연구비 · 추정')}{_coverage_panel(research['actual'], '연구비 · 실제 청구')}</div></section>
<section id="sources"><div class="section-head"><h2>기록의 출처</h2><p class="muted">원본 별칭과 해시 · 최신 비용 이력</p></div><div class="panel">{sources}<details><summary>입력 식별 정보</summary><p class="ids">Dataset SHA-256: {_e(report['inputs']['dataset_sha256'])}<br>Comparison SHA-256: {_number(report['inputs']['comparison_sha256'])}{generation_hash}{work_hash}</p></details></div></section>
</main><footer>{generation_footer}<p>범위 권위: {_e(scope['scope_authority'])} · 품질 권위: {_e(scope['quality_authority'])}. 선언된 근거이며 계정 전체의 청구나 검수 진위를 인증하지 않습니다.</p><p>환산 차이는 실제 현금 절감액이나 연구비를 포함한 순이익이 아닙니다.</p><p class="ids">Report ID: {_e(report['report_id'])}<br>{_e(report['schema_version'])} · {_e(report['contract_version'])}</p></footer></body></html>'''


def _read_json(path: Path, *, work: bool = False) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("COST_REPORT_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("COST_REPORT_JSON_INVALID")

    limit = _MAX_WORK_JSON_BYTES if work else _MAX_JSON_BYTES
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("WORK_INPUT_TOO_LARGE" if work else "COST_REPORT_INPUT_INVALID")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("WORK_INPUT_TOO_LARGE" if work else "COST_REPORT_INPUT_INVALID")
    if work:
        return parse_work_json(raw)
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("COST_REPORT_OUTPUT_INVALID")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cost-report-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_report(dataset: Mapping, comparison: Mapping | None = None,
                 output: str | Path = "outputs/finops-report", *,
                 generation_comparison: Mapping | None = None,
                 work_dataset: Mapping | None = None) -> dict:
    """Write deterministic JSON and HTML locally; return paths and report ID."""
    report = build_report(dataset, comparison, generation_comparison=generation_comparison,
                          work_dataset=work_dataset)
    content, page = canonical_bytes(report), render_report(report).encode("utf-8")
    root = Path(output).expanduser()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("COST_REPORT_OUTPUT_INVALID")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    targets = (root / "report.json", root / "index.html")
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in targets):
        raise ValueError("COST_REPORT_OUTPUT_INVALID")
    _write_atomic(targets[0], content)
    _write_atomic(targets[1], page)
    return {"report_id": report["report_id"], "report_path": str(targets[0]), "html_path": str(targets[1])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--generation-comparison", type=Path)
    work_source = parser.add_mutually_exclusive_group()
    work_source.add_argument("--work-dataset", type=Path)
    work_source.add_argument("--work-ledger", type=Path)
    parser.add_argument("--work-project")
    parser.add_argument("--work-rates", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wandb", action="store_true", help="Explicitly publish the cost report to private W&B")
    parser.add_argument("--wandb-entity")
    parser.add_argument("--wandb-project")
    args = parser.parse_args(argv)
    if args.dataset is None and args.work_dataset is None and args.work_ledger is None:
        parser.error("the following arguments are required: --dataset")
    if args.work_ledger is not None and args.work_project is None:
        parser.error("--work-ledger requires --work-project")
    if args.work_ledger is None and (args.work_project is not None or args.work_rates is not None):
        parser.error("--work-project/--work-rates require --work-ledger")
    if args.wandb and not (args.wandb_entity and args.wandb_project):
        parser.error("--wandb requires --wandb-entity and --wandb-project")
    if not args.wandb and (args.wandb_entity or args.wandb_project):
        parser.error("--wandb-entity/--wandb-project require --wandb")
    try:
        inputs = [path for path in (args.dataset, args.comparison, args.generation_comparison,
                                   args.work_dataset, args.work_ledger, args.work_rates) if path is not None]
        for target in (args.output / "report.json", args.output / "index.html"):
            for source in inputs:
                if target.resolve() == source.resolve() or (target.exists() and os.path.samefile(target, source)):
                    raise ValueError("COST_REPORT_INPUT_OVERWRITE")
        dataset = (_read_json(args.dataset) if args.dataset is not None else
                   {"schema_version": "beautyai-cost-dataset-v1", "cost_items": [], "batches": [], "acceptances": []})
        comparison = _read_json(args.comparison) if args.comparison else None
        generation_comparison = _read_json(args.generation_comparison) if args.generation_comparison else None
        work_dataset = _read_json(args.work_dataset, work=True) if args.work_dataset else None
        if args.work_ledger is not None:
            from .work_ledger import read_work_dataset
            rates = _read_json(args.work_rates, work=True) if args.work_rates else None
            work_dataset = read_work_dataset(args.work_ledger, args.work_project, rates)
        paths = write_report(dataset, comparison, args.output, generation_comparison=generation_comparison,
                             work_dataset=work_dataset)
    except (ValueError, OSError, UnicodeError, RecursionError) as exc:
        reason = str(exc)
        code = reason if re.fullmatch(r"(?:COST|WORK)_[A-Z_]+", reason) else "COST_REPORT_INVALID"
        parser.exit(2, code + "\n")
    except GatewayError as exc:
        parser.exit(2, (exc.code if re.fullmatch(r"WORK_[A-Z_]+", exc.code) else "COST_REPORT_INVALID") + "\n")
    response = {"status": "LOCAL_REPORT_WRITTEN", **paths, "new_provider_calls": 0}
    exit_code = 0
    if args.wandb:
        try:
            from .cost_wandb import sync_cost_report
            receipt = sync_cost_report(paths["report_path"], entity=args.wandb_entity,
                                       project=args.wandb_project, publish=True)
            status = receipt["status"]
            response["wandb_status"] = status if status in {
                "PREPARED", "UPLOADED", "READBACK_VERIFIED", "INCOMPLETE",
            } else "INCOMPLETE"
            if response["wandb_status"] != "READBACK_VERIFIED":
                exit_code = 2
                code = receipt.get("error_code", "WANDB_COST_READBACK_INCOMPLETE")
                response["wandb_error_code"] = (code if isinstance(code, str) and (re.fullmatch(r"WANDB_[A-Z_]+", code)
                                                                                 or code == "WORK_WANDB_COUNT_RANGE")
                                                else "WANDB_COST_READBACK_INCOMPLETE")
        except Exception as exc:
            # Optional integration failures must not expose SDK/path/credential
            # exception text or obscure the already-written local report.
            code = str(exc)
            response["wandb_status"] = "INCOMPLETE"
            response["wandb_error_code"] = (code if re.fullmatch(r"WANDB_[A-Z_]+", code) or code == "WORK_WANDB_COUNT_RANGE"
                                            else "WANDB_COST_SYNC_FAILED")
            exit_code = 2
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
