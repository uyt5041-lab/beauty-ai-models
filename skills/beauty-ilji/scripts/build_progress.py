#!/usr/bin/env python3
"""Read an immutable Git TODO and render a date-scoped, complete plan."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import date
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess

CHECK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)")
OUTPUTS = ("full-todo.md", "full-todo.html", "progress.svg", "progress.json")


def scheduled(text: str, year: int) -> str | None:
    match = DATE.search(text)
    return date(year, int(match[1]), int(match[2])).isoformat() if match else None


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace(chr(96), "").strip()


def collect(text: str, cutoff: date, mapping: dict | None = None) -> dict:
    """The numerator applies the reporting date; total includes the whole plan."""
    groups: OrderedDict[str, dict] = OrderedDict()
    current = "기타 계획"
    groups[current] = {"title": current, "scheduled": None, "items": []}
    fence: str | None = None
    mapping = mapping or {}
    sequence = 0
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith((chr(96) * 3, "~~~")):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        heading = HEADING.match(line)
        if heading:
            title = heading[2]
            current = f"{title} [L{number}]"
            groups[current] = {
                "title": title,
                "scheduled": scheduled(title, cutoff.year),
                "items": [],
            }
            continue
        if line.startswith("**일정:"):
            groups[current]["scheduled"] = scheduled(line, cutoff.year)
        check = CHECK.match(line)
        if check:
            sequence += 1
            groups[current]["items"].append({
                "id": f"T{sequence:03}", "line": number,
                "raw_checked": check[1].lower() == "x",
                "raw_text": check[2],
            })
    result_groups = []
    for group in groups.values():
        if not group["items"]:
            continue
        title = group["title"]
        items = []
        for item in group["items"]:
            planned = mapping.get(item["id"], mapping.get(title, group["scheduled"]))
            if planned is not None:
                date.fromisoformat(planned)
            scope = (
                "undated" if planned is None else
                "future" if planned > cutoff.isoformat() else "by_report_date"
            )
            completed = re.search(r"완료일:\s*(\d{4}-\d{2}-\d{2})", item["raw_text"])
            completed_date = completed[1] if completed else None
            if completed_date is not None:
                date.fromisoformat(completed_date)
            checked = bool(
                item["raw_checked"] and scope == "by_report_date"
                and (completed_date is None or completed_date <= cutoff.isoformat())
            )
            # Later source completion notes are not part of this journal.
            body = item["raw_text"].split("**완료일:", 1)[0].strip()
            items.append({
                "id": item["id"], "line": item["line"], "text": plain(body),
                "planned": planned, "scope": scope, "checked": checked,
                "completed_date": completed_date if checked else None,
            })
        result_groups.append({
            "title": title, "total": len(items),
            "completed": sum(i["checked"] for i in items), "items": items,
        })
    total = sum(g["total"] for g in result_groups)
    completed = sum(g["completed"] for g in result_groups)
    return {
        "report_date": cutoff.isoformat(),
        "metric": "journal checks within the reporting date, not product completion",
        "schedule_basis": "first planned date in each section; undated items uncounted",
        "total": total, "completed": completed, "remaining": total - completed,
        "percent": round(completed / total * 100, 1) if total else None,
        "groups": result_groups,
    }


def summary(data: dict) -> str:
    value = "산정 불가" if data["percent"] is None else f'{data["percent"]:.1f}%'
    return f'{data["completed"]}/{data["total"]}개 · {value}'


def svg(data: dict) -> str:
    width = 680 * (data["completed"] / data["total"]) if data["total"] else 0
    label = html.escape(summary(data))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="110" '
        'viewBox="0 0 760 110" role="img" aria-labelledby="title desc">'
        f'<title id="title">전체 TODO 진행률 {label}</title>'
        '<desc id="desc">보고일 기준 일지 체크 진행률. 이후 계획은 미리 체크하지 않음.</desc>'
        '<rect width="760" height="110" rx="14" fill="#f1f5fb"/>'
        '<text x="40" y="31" font-family="sans-serif" font-size="16" fill="#172033">'
        f'전체 TODO · {data["report_date"]}</text>'
        f'<text x="720" y="31" text-anchor="end" font-family="sans-serif" '
        f'font-size="16" fill="#172033">{label}</text>'
        '<rect x="40" y="48" width="680" height="22" rx="11" fill="#dce5f2"/>'
        f'<rect x="40" y="48" width="{width:.2f}" height="22" rx="11" fill="#2563eb"/>'
        '<text x="40" y="94" font-family="sans-serif" font-size="12" fill="#475569">'
        '일지 체크 기준 · 전체 목록 포함 · 이후 예정 항목은 미체크</text></svg>\n'
    )


def render(data: dict) -> tuple[str, str]:
    origin = data["source_url"]
    md = [
        "# 전체 이미지 TODO와 진행률", "",
        f'기준일: {data["report_date"]} · 원본 커밋: {data["source_commit"]}', "",
        f'**일지 체크 진행률: {summary(data)}**', "",
        f'![전체 TODO 진행률 {summary(data)}](progress.svg)', "",
        "분모는 원본 문서의 전체 체크 항목이다. 분자는 보고일까지 계획된 항목 중 "
        "완료 근거가 있는 체크다. 이후 계획도 모두 보여주지만 미리 체크하지 않는다. "
        "이후의 빈 체크는 구현 미완료를 단정하는 표시가 아니다. "
        "일정 미정은 완료 수에 넣지 않으며 이 수치는 제품 완성률이 아니다.", "",
        "[원본 TODO](" + origin + ") · [진행률 데이터](progress.json)", "",
        "## 묶음별 진행률", "",
        "| 계획 묶음 | 일지 체크 / 전체 |", "| --- | --- |",
    ]
    rendered = [
        "<h1>전체 이미지 TODO와 진행률</h1>",
        f'<p>{data["report_date"]} · 일지 체크 {html.escape(summary(data))}</p>',
        f'<progress value="{data["completed"]}" max="{max(data["total"], 1)}" '
        f'aria-label="전체 TODO 일지 체크 진행률">{html.escape(summary(data))}</progress>',
        "<p>이후 계획도 표시하되 미리 체크하지 않습니다. 제품 완성률과 구분합니다.</p>",
    ]
    for group in data["groups"]:
        md.append(f'| {group["title"]} | {group["completed"]} / {group["total"]} |')
    md.append("")
    for group in data["groups"]:
        md.extend(["## " + group["title"], ""])
        rendered.append(f'<h2>{html.escape(group["title"])}</h2><ul>')
        for item in group["items"]:
            mark = "x" if item["checked"] else " "
            scope = (
                f'예정 {item["planned"]}' if item["scope"] == "future" else
                "일정 미정" if item["scope"] == "undated" else
                f'계획 시작 {item["planned"]}'
            )
            line = f'{item["text"]} ({scope})'.replace("~", r"\~")
            md.append(
                f'- [{mark}] {item["id"]} · {line} '
                f'([원본 L{item["line"]}]({origin}#L{item["line"]}))'
            )
            checked = " checked" if item["checked"] else ""
            rendered.append(
                f'<li><input type="checkbox" disabled{checked}> '
                f'{item["id"]} · {html.escape(item["text"])} '
                f'<small>({html.escape(scope)})</small></li>'
            )
        md.append("")
        rendered.append("</ul>")
    css = (
        "body{font:16px/1.7 system-ui,sans-serif;margin:0;background:#f4f7fb;color:#172033}"
        "main{max-width:1100px;margin:24px auto;padding:28px;background:white;border-radius:14px}"
        "progress{width:100%;height:28px;accent-color:#2563eb}li{margin:12px 0}"
        "ul{list-style:none;padding:0}small{color:#526175}h2{margin-top:2em}"
        "@media(max-width:700px){main{margin:0;padding:16px}body{font-size:14px}}"
    )
    page = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>전체 TODO 진행률</title><style>" + css +
        "</style></head><body><main>" + "".join(rendered) + "</main></body></html>\n"
    )
    return "\n".join(md), page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--source", default="docs/imageTodo.md")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schedule-map", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    def git(*values: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(args.repo), *values], text=True
        )
    commit = git("rev-parse", "--verify", args.ref + "^{commit}").strip()
    source = git("show", f"{commit}:{args.source}")
    mapping = json.loads(args.schedule_map.read_text()) if args.schedule_map else None
    data = collect(source, args.date, mapping)
    data.update({
        "source_commit": commit, "source_path": args.source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_url": (
            "https://github.com/uyt5041-lab/beauty-ai-models/blob/"
            + commit + "/" + args.source
        ),
    })
    md, page = render(data)
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.overwrite and any((args.out / name).exists() for name in OUTPUTS):
        parser.error("Known output files already exist; use a fresh folder or --overwrite")
    (args.out / "full-todo.md").write_text(md)
    (args.out / "full-todo.html").write_text(page)
    (args.out / "progress.svg").write_text(svg(data))
    (args.out / "progress.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(summary(data))


if __name__ == "__main__":
    main()
