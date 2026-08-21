#!/usr/bin/env python3
"""將健保署藥品品項 CSV 與給付規定 PDF 清洗成單一靜態查詢 HTML。"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = Path(__file__).with_name("template.html")
DEFAULT_OUTPUT = ROOT / "_site" / "nhi" / "index.html"

OFFICIAL_ITEMS_URL = (
    "https://info.nhi.gov.tw/api/iode0000s01/Dataset?"
    "rId=A21030000I-E41001-001"
)
OFFICIAL_RULES_PAGE = "https://www.nhi.gov.tw/ch/np-2508-1.html"
OFFICIAL_ITEM_SEARCH = "https://info.nhi.gov.tw/INAE3000/INAE3000S01?type=app"

REQUIRED_COLUMNS = {
    "藥品代號",
    "藥品英文名稱",
    "藥品中文名稱",
    "成分",
    "支付價",
    "有效起日",
    "有效迄日",
    "藥商",
    "劑型",
    "ATC代碼",
    "給付規定章節",
    "藥品代碼超連結",
    "給付規定章節連結",
}


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def parse_roc_date(value: str) -> date | None:
    raw = clean(value)
    if not raw:
        return None
    if not raw.isdigit() or len(raw) not in (6, 7):
        raise ValueError(raw)
    year_digits = len(raw) - 4
    return date(int(raw[:year_digits]) + 1911, int(raw[year_digits:year_digits + 2]), int(raw[-2:]))


def format_roc_date(value: str) -> str:
    raw = clean(value)
    if not raw:
        return ""
    try:
        parsed = parse_roc_date(raw)
    except ValueError:
        return raw
    if parsed is None:
        return ""
    return parsed.isoformat()


def is_current(row: dict[str, str], as_of: date) -> tuple[bool, bool]:
    invalid = False
    try:
        start = parse_roc_date(row.get("有效起日", ""))
    except ValueError:
        start = None
        invalid = True
    try:
        end = parse_roc_date(row.get("有效迄日", ""))
    except ValueError:
        end = None
        invalid = True
    if invalid:
        return False, True
    if start and start > as_of:
        return False, False
    if end and end < as_of:
        return False, False
    return True, False


def compact_record(row: dict[str, str]) -> dict[str, str]:
    return {
        "code": clean(row.get("藥品代號")),
        "en": clean(row.get("藥品英文名稱")),
        "zh": clean(row.get("藥品中文名稱")),
        "ingredient": clean(row.get("成分")),
        "spec": " ".join(
            part for part in (
                clean(row.get("規格量")),
                clean(row.get("規格單位")),
            ) if part
        ),
        "price": clean(row.get("支付價")),
        "start": format_roc_date(row.get("有效起日", "")),
        "end": format_roc_date(row.get("有效迄日", "")),
        "company": clean(row.get("藥商")),
        "form": clean(row.get("劑型")),
        "atc": clean(row.get("ATC代碼")),
        "chapter": clean(row.get("給付規定章節")),
        "drugUrl": clean(row.get("藥品代碼超連結")),
        "ruleUrl": clean(row.get("給付規定章節連結")),
    }


def load_current_records(csv_path: Path, as_of: date) -> tuple[list[dict[str, str]], dict[str, int]]:
    by_code: dict[str, tuple[str, dict[str, str]]] = {}
    stats = Counter()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError("健保 CSV 缺少欄位：" + "、".join(sorted(missing)))
        for row in reader:
            stats["source"] += 1
            current, invalid_date = is_current(row, as_of)
            if invalid_date:
                stats["invalid_date"] += 1
                continue
            if not current:
                stats["historical_or_future"] += 1
                continue
            code = clean(row.get("藥品代號"))
            if not code:
                stats["missing_code"] += 1
                continue
            compact = compact_record(row)
            start_key = clean(row.get("有效起日"))
            previous = by_code.get(code)
            if previous is None or start_key >= previous[0]:
                by_code[code] = (start_key, compact)

    if stats["source"] == 0:
        raise RuntimeError("健保 CSV 沒有資料")
    if stats["invalid_date"] / stats["source"] > 0.01:
        raise RuntimeError(
            f"日期解析失敗 {stats['invalid_date']}/{stats['source']}，疑似官方欄位格式已變更"
        )
    records = sorted((item[1] for item in by_code.values()), key=lambda r: r["code"])
    stats["current_unique"] = len(records)
    stats["with_chapter"] = sum(bool(r["chapter"]) for r in records)
    return records, dict(stats)


def chapter_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.split(r"[,;，；、]", value or ""):
        token = re.sub(r"\s+", "", raw).rstrip(".")
        if re.fullmatch(r"\d{1,2}(?:\.\d{1,3}){1,5}", token):
            tokens.append(token)
    return tokens


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 pdftotext；請先安裝 Poppler") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"pdftotext 失敗：{detail}") from exc
    return result.stdout.decode("utf-8", "replace")


def normalize_pdf_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\f", "\n")
    text = re.sub(r"(?m)^\s*\(\d{2,3}\.\d{1,2}\.\d{1,2}更新\)\s*$", "", text)
    text = re.sub(r"(?m)^\s*\d{1,4}\s*$", "", text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def heading_pattern(chapter: str) -> re.Pattern[str]:
    # 官方 PDF 的章節號格式並不一致，例如「1.3.2.6 Carbamazepine」、
    # 「7.1 .消化性潰瘍用藥」與「9.26.Pemetrexed」都實際存在。
    return re.compile(rf"(?m)^\s*{re.escape(chapter)}\s*\.?\s*(?=\S)")


def next_sibling_start(text: str, chapter: str, after: int) -> int | None:
    parts = chapter.split(".")
    if len(parts) < 2:
        return None
    parent = re.escape(".".join(parts[:-1]))
    pattern = re.compile(rf"(?m)^\s*({parent}\.\d{{1,3}})\s*\.?\s*(?=\S)")
    for match in pattern.finditer(text, after):
        if match.group(1) != chapter:
            return match.start()
    return None


def extract_chapters(text: str, expected: set[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    normalized = normalize_pdf_text(text)
    source_for: dict[str, str] = {}
    start_for_source: dict[str, int] = {}
    missing: list[str] = []
    for chapter in sorted(expected, key=lambda item: [int(x) for x in item.split(".")]):
        candidate = chapter
        match = None
        while "." in candidate:
            match = heading_pattern(candidate).search(normalized)
            if match:
                source_for[chapter] = candidate
                start_for_source.setdefault(candidate, match.start())
                break
            candidate = candidate.rsplit(".", 1)[0]
        if not match:
            missing.append(chapter)

    starts = sorted((start, chapter) for chapter, start in start_for_source.items())
    source_content: dict[str, dict[str, str]] = {}
    for index, (start, source_chapter) in enumerate(starts):
        end_candidates = [len(normalized)]
        if index + 1 < len(starts):
            end_candidates.append(starts[index + 1][0])
        sibling = next_sibling_start(normalized, source_chapter, start + len(source_chapter))
        if sibling is not None:
            end_candidates.append(sibling)
        end = min(end_candidates)
        content = normalized[start:end].strip()
        if not heading_pattern(source_chapter).match(content):
            missing.extend(chapter for chapter, source in source_for.items() if source == source_chapter)
            continue
        first_line = content.splitlines()[0]
        title = re.sub(rf"^\s*{re.escape(source_chapter)}\s*\.?\s*", "", first_line).strip()
        source_content[source_chapter] = {
            "title": title[:240],
            "text": content,
        }
    chapters: dict[str, dict[str, str]] = {}
    for chapter, source_chapter in source_for.items():
        source = source_content.get(source_chapter)
        if not source:
            continue
        chapters[chapter] = {
            **source,
            "matchedChapter": source_chapter if source_chapter != chapter else "",
        }
    return chapters, sorted(set(missing))


def safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def render_html(
    template_path: Path,
    output_path: Path,
    records: list[dict[str, str]],
    chapters: dict[str, dict[str, str]],
    meta: dict[str, object],
) -> None:
    template = template_path.read_text(encoding="utf-8")
    required = ("__META_JSON__", "__RECORDS_JSON__", "__RULES_JSON__")
    missing = [key for key in required if key not in template]
    if missing:
        raise RuntimeError("HTML 模板缺少標記：" + "、".join(missing))
    output = (
        template.replace("__META_JSON__", safe_json(meta))
        .replace("__RECORDS_JSON__", safe_json(records))
        .replace("__RULES_JSON__", safe_json(chapters))
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8", newline="\n")


def build(args: argparse.Namespace) -> dict[str, object]:
    as_of = date.fromisoformat(args.as_of)
    records, stats = load_current_records(args.csv, as_of)
    expected = {token for row in records for token in chapter_tokens(row["chapter"])}
    pdf_text = args.rules_text.read_text(encoding="utf-8") if args.rules_text else extract_pdf_text(args.rules_pdf)
    chapters, missing = extract_chapters(pdf_text, expected)
    meta: dict[str, object] = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "asOf": as_of.isoformat(),
        "rulesVersion": args.rules_version,
        "recordCount": len(records),
        "chapterCount": len(chapters),
        "missingChapterCount": len(missing),
        "officialItemsUrl": OFFICIAL_ITEMS_URL,
        "officialRulesPage": OFFICIAL_RULES_PAGE,
        "officialItemSearch": OFFICIAL_ITEM_SEARCH,
        "stats": stats,
    }
    render_html(args.template, args.output, records, chapters, meta)
    return {"output": str(args.output), "meta": meta, "missingChapters": missing}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True, help="健保署藥品品項 CSV")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rules-pdf", type=Path, help="健保署最新版藥品給付規定 PDF")
    source.add_argument("--rules-text", type=Path, help="測試用 pdftotext 純文字")
    parser.add_argument("--rules-version", required=True, help="例：115.7.23")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="有效品項基準日 YYYY-MM-DD")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    try:
        report = build(parse_args())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
