#!/usr/bin/env python3
"""下載健保署最新開放資料與整份給付規定，產生 GitHub Pages 查詢頁。"""

from __future__ import annotations

import argparse
import hashlib
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
import zipfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path


HERE = Path(__file__).resolve().parent
BUILD_SCRIPT = HERE / "build_nhi_lookup.py"
ALLOWED_HOSTS = {
    "www.nhi.gov.tw",
    "info.nhi.gov.tw",
    "data.fda.gov.tw",
    "lcctaiwan.github.io",
}
USER_AGENT = "ChemoDrugStable-NHI-Updater/1.0 (+GitHub-Actions)"
MIN_RULES_PDF_BYTES = 2_000_000
MIN_RULES_PDF_PAGES = 300


class SourcePendingError(RuntimeError):
    """健保署官方文件已公告新版，但 PDF 尚未同步完成。"""


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


def version_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*(\d{3})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*", value)
    if not match:
        raise RuntimeError(f"無法辨識民國日期版本：{value}")
    return tuple(int(part) for part in match.groups())


def detect_page_version(page_html: str) -> str:
    visible = html.unescape(re.sub(r"<[^>]+>", " ", page_html))
    visible = re.sub(r"\s+", " ", visible)
    candidates: list[tuple[int, int, int]] = []
    patterns = (
        r"最新版藥品給付規定內容[^0-9]{0,45}(\d{3})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*更新",
        r"更新日期\s*(\d{3})[.\-/](\d{1,2})[.\-/](\d{1,2})",
    )
    for pattern in patterns:
        candidates.extend(tuple(int(part) for part in match) for match in re.findall(pattern, visible))
    if not candidates:
        raise RuntimeError("無法從健保署下載頁辨識最新版日期")
    year, month, day = max(candidates)
    return f"{year}.{month}.{day}"


def detect_docx_modified_version(docx_path: Path) -> str:
    try:
        with zipfile.ZipFile(docx_path) as archive:
            core = archive.read("docProps/core.xml").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RuntimeError("官方 Word 檔結構異常，無法辨識最新版日期") from exc
    match = re.search(
        r"<dcterms:modified[^>]*>(\d{4})-(\d{2})-(\d{2})T", core
    )
    if not match:
        raise RuntimeError("官方 Word 檔缺少修改日期，無法辨識最新版日期")
    year, month, day = (int(part) for part in match.groups())
    if year <= 1911:
        raise RuntimeError(f"官方 Word 檔修改年份異常：{year}")
    return f"{year - 1911}.{month}.{day}"


def normalize_version_match(match: tuple[str, str, str]) -> str:
    year, month, day = match
    return f"{int(year)}.{int(month)}.{int(day)}"


def detect_rules_version(pdf_path: Path, source_name: str = "") -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "60", "-layout", str(pdf_path), "-"],
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
    if versions:
        return normalize_version_match(versions[0])
    decoded_name = urllib.parse.unquote(source_name)
    compact = re.search(r"(?<!\d)(\d{3})(\d{2})(\d{2})(?!\d)", decoded_name)
    if compact:
        return normalize_version_match(compact.groups())
    versions = re.findall(
        r"(?<!\d)(\d{3})\s*[.\uff0e]\s*(\d{1,2})\s*[.\uff0e]\s*(\d{1,2})(?:\s*更新)?",
        text,
    )
    if versions:
        return normalize_version_match(versions[0])
    raise RuntimeError("無法從給付規定 PDF 前 60 頁或官方下載檔名辨識版本日期")


def inspect_rules_pdf(pdf_path: Path) -> dict[str, object]:
    size = pdf_path.stat().st_size
    if size < MIN_RULES_PDF_BYTES:
        raise RuntimeError(f"給付規定 PDF 過小：{size:,} bytes")
    if pdf_path.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("給付規定下載結果不是 PDF 檔")
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)], check=True, capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 pdfinfo；GitHub Actions 需安裝 poppler-utils") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("給付規定 PDF 結構檢查失敗") from exc
    page_match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if not page_match:
        raise RuntimeError("無法辨識給付規定 PDF 頁數")
    pages = int(page_match.group(1))
    if pages < MIN_RULES_PDF_PAGES:
        raise RuntimeError(f"給付規定 PDF 頁數異常：{pages} 頁")
    return {
        "bytes": size,
        "pages": pages,
        "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
    }


def assess_source_versions(page_version: str, pdf_version: str, previous_version: str) -> str:
    page_key = version_key(page_version)
    pdf_key = version_key(pdf_version)
    previous_key = version_key(previous_version)
    if pdf_key < previous_key:
        raise RuntimeError(
            f"官方 PDF 版本倒退：PDF {pdf_version}，線上既有版本 {previous_version}"
        )
    if page_key == pdf_key:
        return "ready"
    if page_key > pdf_key and pdf_key == previous_key:
        raise SourcePendingError(
            f"健保署官方文件已更新為 {page_version}，但 PDF 仍為 {pdf_version}；保留既有線上版本，等待官網同步"
        )
    raise RuntimeError(
        f"健保署來源版本無法確認：官方文件 {page_version}、PDF {pdf_version}、線上既有版本 {previous_version}"
    )


def load_previous_meta(url: str, target: Path) -> dict[str, object]:
    download(url, target)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        meta = payload["meta"]
        version_key(str(meta["rulesVersion"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("無法讀取線上既有資料版本，停止自動發布") from exc
    return meta


def write_status(path: Path | None, status: str, message: str, **details: object) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": status, "message": message, **details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    parser.add_argument("--tfda-licenses-url", default=None)
    parser.add_argument("--rules-page", default=None)
    parser.add_argument("--rules-pdf-url", help="指定健保署整份給付規定 PDF")
    parser.add_argument("--rules-docx-url", help="健保署整份給付規定 Word；用修改日期交叉確認 PDF")
    parser.add_argument("--expected-rules-version", help="預期規定版本，例如 115.7.23；不符時停止發布")
    parser.add_argument("--previous-version-url", help="線上既有 data_version.json；自動判斷最新版時必填")
    parser.add_argument("--status-file", type=Path, help="輸出 ready／pending 狀態供 GitHub Actions 判斷")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = load_builder()
    items_url = args.items_url or builder.OFFICIAL_ITEMS_URL
    tfda_licenses_url = args.tfda_licenses_url or builder.OFFICIAL_TFDA_LICENSES_URL
    rules_page = args.rules_page or builder.OFFICIAL_RULES_PAGE
    with tempfile.TemporaryDirectory(prefix="nhi-lookup-") as temp_dir:
        temp = Path(temp_dir)
        csv_path = temp / "items.csv"
        tfda_path = temp / "tfda_licenses.zip"
        pdf_path = temp / "rules.pdf"
        docx_path = temp / "rules.docx"
        page_version = ""
        final_docx_url = ""
        if args.rules_pdf_url:
            pdf_url = checked_url(args.rules_pdf_url)
            if args.rules_docx_url:
                final_docx_url, _ = download(args.rules_docx_url, docx_path)
                page_version = detect_docx_modified_version(docx_path)
        else:
            page_path = temp / "rules.html"
            download(rules_page, page_path)
            page_html = page_path.read_text(encoding="utf-8", errors="replace")
            page_version = detect_page_version(page_html)
            pdf_url = find_whole_rules_pdf(
                page_html, rules_page
            )
        final_pdf_url, pdf_source_name = download(pdf_url, pdf_path)
        pdf_facts = inspect_rules_pdf(pdf_path)
        version = detect_rules_version(pdf_path, pdf_source_name)
        if args.expected_rules_version and version != args.expected_rules_version:
            raise RuntimeError(
                f"給付規定版本不符：PDF 為 {version}，設定為 {args.expected_rules_version}；停止發布"
            )
        previous_meta: dict[str, object] = {}
        if args.previous_version_url:
            previous_meta = load_previous_meta(args.previous_version_url, temp / "previous.json")
            previous_version = str(previous_meta["rulesVersion"])
            if page_version:
                try:
                    assess_source_versions(page_version, version, previous_version)
                except SourcePendingError as exc:
                    write_status(
                        args.status_file,
                        "pending",
                        str(exc),
                        officialVersion=page_version,
                        pdfVersion=version,
                        previousVersion=previous_version,
                        rulesPdf=final_pdf_url,
                    )
                    print(f"等待：{exc}")
                    return
            previous_hash = str(previous_meta.get("rulesPdfSha256") or "")
            if version == previous_version and previous_hash and previous_hash != pdf_facts["sha256"]:
                raise RuntimeError("官方 PDF 在版本日期不變時內容雜湊已改變，停止自動發布")
        elif page_version:
            raise RuntimeError("自動尋找最新版時必須提供 --previous-version-url")
        download(items_url, csv_path)
        download(tfda_licenses_url, tfda_path)
        build_args = argparse.Namespace(
            csv=csv_path,
            tfda_licenses=tfda_path,
            rules_pdf=pdf_path,
            rules_text=None,
            rules_version=version,
            as_of=args.as_of or date.today().isoformat(),
            template=builder.DEFAULT_TEMPLATE,
            output=args.output,
        )
        report = builder.build(build_args)
        report["meta"].update({
            "rulesOfficialVersion": page_version or version,
            "rulesPdfSha256": pdf_facts["sha256"],
            "rulesPdfBytes": pdf_facts["bytes"],
            "rulesPdfPages": pdf_facts["pages"],
            "sourceVerification": "automatic-cross-check",
        })
        report["sources"] = {
            "items": items_url,
            "tfdaLicenses": tfda_licenses_url,
            "rulesPage": rules_page,
            "rulesPdf": final_pdf_url,
            "rulesDocx": final_docx_url,
        }
        version_file = args.output.with_name("data_version.json")
        version_file.write_text(
            json.dumps({"meta": report["meta"], "sources": report["sources"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["versionFile"] = str(version_file)
        write_status(
            args.status_file,
            "ready",
            f"健保署官方文件與 PDF 版本一致：{version}",
            officialVersion=page_version or version,
            pdfVersion=version,
            previousVersion=previous_meta.get("rulesVersion", ""),
            rulesPdf=final_pdf_url,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
