#!/usr/bin/env python3
"""將健保署藥品品項 CSV 與給付規定 Word／PDF 清洗成單一靜態查詢 HTML。"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import sys
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
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
OFFICIAL_TFDA_LICENSES_URL = "https://data.fda.gov.tw/data/opendata/export/37/json"
OFFICIAL_TFDA_INSERT_BASE = "https://mcp.fda.gov.tw/im_detail_1/"
MIN_TFDA_RECORDS = 10000
MIN_RULES_TEXT_CHARS = 500_000

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

TFDA_REQUIRED_COLUMNS = {
    "許可證字號",
    "註銷狀態",
    "中文品名",
    "英文品名",
    "主成分略述",
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


def read_tfda_json(source_path: Path) -> list[dict[str, object]]:
    if zipfile.is_zipfile(source_path):
        with zipfile.ZipFile(source_path) as archive:
            candidates = [
                info for info in archive.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".json")
            ]
            if len(candidates) != 1:
                raise RuntimeError(f"TFDA 壓縮檔應只有一個 JSON，實際為 {len(candidates)} 個")
            info = candidates[0]
            if info.file_size > 200 * 1024 * 1024:
                raise RuntimeError("TFDA JSON 解壓後超過 200 MB，停止建置")
            raw = archive.read(info).decode("utf-8-sig")
    else:
        raw = source_path.read_text(encoding="utf-8-sig")
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("TFDA 許可證資料不是非空陣列")
    if not isinstance(payload[0], dict):
        raise RuntimeError("TFDA 許可證資料列格式不正確")
    missing = TFDA_REQUIRED_COLUMNS - set(payload[0])
    if missing:
        raise RuntimeError("TFDA 許可證資料缺少欄位：" + "、".join(sorted(missing)))
    return payload


def load_tfda_labels(source_path: Path) -> tuple[list[dict[str, str]], dict[str, int]]:
    rows = read_tfda_json(source_path)
    by_license: dict[str, dict[str, str]] = {}
    stats = Counter(source=len(rows))
    for row in rows:
        license_number = clean(row.get("許可證字號"))
        if not license_number:
            stats["missing_license"] += 1
            continue
        if clean(row.get("註銷狀態")):
            stats["cancelled"] += 1
            continue
        record = {
            "license": license_number,
            "zh": clean(row.get("中文品名")),
            "en": clean(row.get("英文品名")),
            "ingredient": clean(row.get("主成分略述")),
            "form": clean(row.get("劑型")),
            "validUntil": clean(row.get("有效日期")),
            "url": OFFICIAL_TFDA_INSERT_BASE + urllib.parse.quote(license_number, safe=""),
        }
        previous = by_license.get(license_number)
        if previous is None:
            by_license[license_number] = record
            continue
        stats["duplicate"] += 1
        for field in ("zh", "en", "ingredient", "form", "validUntil"):
            if not previous[field] and record[field]:
                previous[field] = record[field]

    labels = sorted(by_license.values(), key=lambda item: item["license"])
    if len(labels) < MIN_TFDA_RECORDS:
        raise RuntimeError(f"TFDA 未註銷許可證只有 {len(labels)} 筆，疑似來源異常")
    stats["current_unique"] = len(labels)
    return labels, dict(stats)


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


def extract_docx_text(docx_path: Path) -> str:
    try:
        with zipfile.ZipFile(docx_path) as archive:
            document = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RuntimeError("健保署 Word 檔結構異常，無法讀取條文") from exc
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise RuntimeError("健保署 Word 內文 XML 損壞") from exc
    word_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(f"{word_ns}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{word_ns}t":
                parts.append(node.text or "")
            elif node.tag == f"{word_ns}tab":
                parts.append("\t")
            elif node.tag in {f"{word_ns}br", f"{word_ns}cr"}:
                parts.append("\n")
        value = "".join(parts).strip()
        if value:
            lines.extend(part.strip() for part in value.splitlines() if part.strip())
    text = "\n".join(lines).strip()
    if len(text) < MIN_RULES_TEXT_CHARS:
        raise RuntimeError(f"健保署 Word 條文文字過少：{len(text):,} 字元")
    return text


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
    labels: list[dict[str, str]],
    meta: dict[str, object],
) -> None:
    template = template_path.read_text(encoding="utf-8")
    required = ("__META_JSON__", "__RECORDS_JSON__", "__RULES_JSON__", "__LABELS_JSON__")
    missing = [key for key in required if key not in template]
    if missing:
        raise RuntimeError("HTML 模板缺少標記：" + "、".join(missing))
    output = (
        template.replace("__META_JSON__", safe_json(meta))
        .replace("__RECORDS_JSON__", safe_json(records))
        .replace("__RULES_JSON__", safe_json(chapters))
        .replace("__LABELS_JSON__", safe_json(labels))
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8", newline="\n")


def build(args: argparse.Namespace) -> dict[str, object]:
    as_of = date.fromisoformat(args.as_of)
    records, stats = load_current_records(args.csv, as_of)
    labels, tfda_stats = load_tfda_labels(args.tfda_licenses)
    expected = {token for row in records for token in chapter_tokens(row["chapter"])}
    if args.rules_text:
        rules_text = args.rules_text.read_text(encoding="utf-8")
    elif getattr(args, "rules_docx", None):
        rules_text = extract_docx_text(args.rules_docx)
    else:
        rules_text = extract_pdf_text(args.rules_pdf)
    chapters, missing = extract_chapters(rules_text, expected)
    meta: dict[str, object] = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "asOf": as_of.isoformat(),
        "rulesVersion": args.rules_version,
        "recordCount": len(records),
        "chapterCount": len(chapters),
        "missingChapterCount": len(missing),
        "tfdaRecordCount": len(labels),
        "officialItemsUrl": OFFICIAL_ITEMS_URL,
        "officialRulesPage": OFFICIAL_RULES_PAGE,
        "officialItemSearch": OFFICIAL_ITEM_SEARCH,
        "officialTfdaLicensesUrl": OFFICIAL_TFDA_LICENSES_URL,
        "stats": stats,
        "tfdaStats": tfda_stats,
    }
    render_html(args.template, args.output, records, chapters, labels, meta)
    return {"output": str(args.output), "meta": meta, "missingChapters": missing}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True, help="健保署藥品品項 CSV")
    parser.add_argument("--tfda-licenses", type=Path, required=True, help="TFDA 第 37 號許可證 JSON 或官方 ZIP")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rules-pdf", type=Path, help="健保署最新版藥品給付規定 PDF")
    source.add_argument("--rules-docx", type=Path, help="健保署最新版藥品給付規定 Word")
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
