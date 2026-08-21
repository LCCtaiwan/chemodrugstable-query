#!/usr/bin/env python3
"""下載健保署最新開放資料與整份給付規定，產生 GitHub Pages 查詢頁。"""

from __future__ import annotations

import argparse
import html
import http.client
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path


HERE = Path(__file__).resolve().parent
BUILD_SCRIPT = HERE / "build_nhi_lookup.py"
ALLOWED_HOSTS = {"www.nhi.gov.tw", "info.nhi.gov.tw"}
USER_AGENT = "ChemoDrugStable-NHI-Updater/1.0 (+GitHub-Actions)"


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href") or ""
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = ""
            self._text = []


def checked_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise RuntimeError(f"不允許的下載網址：{value}")
    return value


def download(url: str, target: Path, timeout: int = 240, attempts: int = 5) -> tuple[str, str]:
    checked = checked_url(url)
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(checked, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = checked_url(response.geturl())
                content_disposition = response.headers.get("Content-Disposition", "")
                with target.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
            if not target.stat().st_size:
                raise RuntimeError(f"下載結果為空：{final_url}")
            return final_url, content_disposition
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            target.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            delay = min(5 * (2 ** (attempt - 1)), 60)
            print(f"下載中斷，第 {attempt}/{attempts} 次；{delay} 秒後重試：{exc}", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError(f"下載失敗：{checked}")


def find_whole_rules_pdf(page_html: str, page_url: str) -> str:
    parser = LinkParser()
    parser.feed(page_html)
    candidates: list[tuple[int, str]] = []
    for href, label in parser.links:
        absolute = urllib.parse.urljoin(page_url, html.unescape(href))
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            continue
        if not parsed.path.lower().endswith(".pdf"):
            continue
        normalized = re.sub(r"\s+", "", label)
        score = 2 if "整份給付規定內容" in normalized else 1 if "整份" in normalized else 0
        if score:
            candidates.append((score, absolute))
    if not candidates:
        raise RuntimeError("找不到健保署「整份給付規定內容」PDF，可能是官網版型已變更")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def normalize_version_match(match: tuple[str, str, str]) -> str:
    year, month, day = match
    return f"{int(year)}.{int(month)}.{int(day)}"


def detect_rules_version(pdf_path: Path, source_name: str = "") -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "5", "-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 pdftotext；GitHub Actions 需安裝 poppler-utils") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("PDF 無法轉成文字") from exc
    text = result.stdout.decode("utf-8", "replace")
    versions = re.findall(
        r"[\(（]\s*(\d{3})\s*[.\uff0e]\s*(\d{1,2})\s*[.\uff0e]\s*(\d{1,2})\s*更新\s*[\)）]",
        text,
    )
    if not versions:
        versions = re.findall(
            r"(?<!\d)(\d{3})\s*[.\uff0e]\s*(\d{1,2})\s*[.\uff0e]\s*(\d{1,2})(?:\s*更新)?",
            text,
        )
    if versions:
        return normalize_version_match(versions[0])
    decoded_name = urllib.parse.unquote(source_name)
    compact = re.search(r"(?<!\d)(\d{3})(\d{2})(\d{2})(?!\d)", decoded_name)
    if compact:
        return normalize_version_match(compact.groups())
    raise RuntimeError("無法從給付規定 PDF 或官方下載檔名辨識版本日期")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_nhi_lookup", BUILD_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError("無法載入健保資料建置程式")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", help="有效品項基準日 YYYY-MM-DD；預設為執行當日")
    parser.add_argument("--items-url", default=None)
    parser.add_argument("--rules-page", default=None)
    parser.add_argument("--rules-pdf-url", help="指定健保署整份給付規定 PDF；排程建議使用")
    parser.add_argument("--expected-rules-version", help="預期規定版本，例如 115.7.23；不符時停止發布")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = load_builder()
    items_url = args.items_url or builder.OFFICIAL_ITEMS_URL
    rules_page = args.rules_page or builder.OFFICIAL_RULES_PAGE
    with tempfile.TemporaryDirectory(prefix="nhi-lookup-") as temp_dir:
        temp = Path(temp_dir)
        csv_path = temp / "items.csv"
        pdf_path = temp / "rules.pdf"
        if args.rules_pdf_url:
            pdf_url = checked_url(args.rules_pdf_url)
        else:
            page_path = temp / "rules.html"
            download(rules_page, page_path)
            pdf_url = find_whole_rules_pdf(
                page_path.read_text(encoding="utf-8", errors="replace"), rules_page
            )
        download(items_url, csv_path)
        final_pdf_url, pdf_source_name = download(pdf_url, pdf_path)
        version = detect_rules_version(pdf_path, pdf_source_name)
        if args.expected_rules_version and version != args.expected_rules_version:
            raise RuntimeError(
                f"給付規定版本不符：PDF 為 {version}，設定為 {args.expected_rules_version}；停止發布"
            )
        build_args = argparse.Namespace(
            csv=csv_path,
            rules_pdf=pdf_path,
            rules_text=None,
            rules_version=version,
            as_of=args.as_of or date.today().isoformat(),
            template=builder.DEFAULT_TEMPLATE,
            output=args.output,
        )
        report = builder.build(build_args)
        report["sources"] = {"items": items_url, "rulesPage": rules_page, "rulesPdf": final_pdf_url}
        version_file = args.output.with_name("data_version.json")
        version_file.write_text(
            json.dumps({"meta": report["meta"], "sources": report["sources"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["versionFile"] = str(version_file)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
