#!/usr/bin/env python3
"""Run fictional records through the public offline evaluation functions."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from beauty_ai.ad_evaluation import evaluate_finops, score_image_case  # noqa: E402

NOTICE = "설명용 가상 자료입니다. 실제 실험·사람·청구·승인 기록이 아닙니다."
STATUS_KO = {"BLOCKED": "보류", "PASS_PROVISIONAL": "임시 기준 통과", "FAIL": "실패"}


def run_examples() -> dict:
    source = Path(__file__).with_name("worked-examples.json")
    examples = json.loads(source.read_text(encoding="utf-8"))
    if examples["evidence_status"] != "FICTIONAL_EXAMPLES_ONLY":
        raise ValueError("이 실행기는 설명용 가상 자료만 처리합니다.")
    evaluators = {"finops": evaluate_finops, "image_review": score_image_case}
    rows = []
    for example in examples["examples"]:
        if example["fictional"] is not True:
            raise ValueError("가상 자료 표시가 없는 예시는 실행하지 않습니다.")
        result = evaluators[example["kind"]](example["input_record"])
        for key, expected in example["expected_result"].items():
            if result.get(key) != expected:
                raise ValueError(f"{example['id']}: 예상 결과와 다릅니다: {key}")
        if result.get("new_paid_calls_allowed_by_this_report", False):
            raise ValueError("예시 결과는 유료 호출을 허가할 수 없습니다.")
        if result.get("asset_approval", "NOT_GRANTED") != "NOT_GRANTED":
            raise ValueError("예시 결과는 광고를 승인할 수 없습니다.")
        rows.append({"id": example["id"], "title": example["title"],
            "fictional": True, "goal": example["goal"],
            "input_summary": example["input_summary"],
            "execution": example["execution"],
            "status_ko": STATUS_KO[result["status"]],
            "expected_explanation": example["expected_explanation"],
            "next_action": example["next_action"],
            "expected_result_verified": True, "result": result})
    return {"example_version": examples["example_version"],
        "evidence_status": "FICTIONAL_EXAMPLES_ONLY", "notice": NOTICE,
        "model_api_called": False, "wandb_uploaded": False,
        "real_experiment_count": 0, "automatic_approval": False,
        "passed_example_assertions": len(rows), "examples": rows}


def render_html(report: dict) -> str:
    cards = []
    for row in report["examples"]:
        fields = [("목표", row["goal"]), ("입력", row["input_summary"]),
            ("실행", row["execution"]), ("예상 결과", row["expected_explanation"]),
            ("다음 행동", row["next_action"])]
        body = "".join(f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>"
                       for label, value in fields)
        score = row["result"]["score_100"]
        score_label = "미확인" if score is None else f"{score:g}/100"
        raw = escape(json.dumps(row["result"], ensure_ascii=False, indent=2))
        cards.append(f"<section><h2>{escape(row['title'])}</h2>"
            f"<p class='status'>{escape(row['status_ko'])} · 점수 {score_label}</p>"
            f"<dl>{body}</dl><details><summary>오프라인 계산 결과</summary>"
            f"<pre>{raw}</pre></details></section>")
    return """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BeautyAI 비용·이미지 검수 작업 예시</title>
<style>body{font:17px/1.7 system-ui,sans-serif;color:#17233b;background:#f2f6fc;
margin:auto;max-width:980px;padding:28px}h1{line-height:1.3}section{background:white;
border:1px solid #cfdaed;border-radius:12px;padding:24px;margin:20px 0}
.notice{background:#fff0b5;border:2px solid #b58000;padding:18px;border-radius:10px}
.status{font-weight:700;color:#134ca2}dt{font-weight:700;margin-top:14px}
dd{margin:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
summary{cursor:pointer;margin-top:18px}footer{font-size:15px}</style>
<h1>비용·이미지 검수 작업 예시</h1><p class="notice">""" + escape(NOTICE) + """
<br>API 호출 0회 · W&amp;B 업로드 없음 · 자동 승인 없음</p>
<p>가상 입력으로 공개 평가 함수를 실행했습니다. 실제 20개 실험 기록과 별개입니다.
각 점수는 해당 영역의 점수입니다. 서비스 전체 점수가 아닙니다.</p>
""" + "".join(cards) + """<footer>통과는 임시 수치 조건의 충족을 뜻합니다.
실제 증거 확인, 담당자 검토와 팀 승인은 별도입니다. 이 파일은 W&amp;B에 올리지 않습니다.</footer></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=NOTICE)
    parser.add_argument("--output-dir", type=Path,
        default=ROOT / "outputs" / "worked-examples",
        help="결과 폴더. 경로에 outputs 폴더가 있어야 합니다.")
    args = parser.parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    if "outputs" not in output_dir.parts:
        parser.error("생성 결과는 outputs/ 아래에 저장하세요.")
    targets = [output_dir / "worked-examples.results.json",
               output_dir / "worked-examples.results.html"]
    if any(path.exists() for path in targets):
        parser.error("같은 결과 파일이 있습니다. 새로운 --output-dir를 지정하세요.")
    report = run_examples()
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in zip(targets, [json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                       render_html(report)]):
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
    print(NOTICE)
    for row in report["examples"]:
        print(f"{row['title']}: {row['status_ko']} — 예상 결과 확인")
    print(f"가상 예시 {report['passed_example_assertions']}개 검증 완료. API 호출 0회.")
    for path in targets:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
