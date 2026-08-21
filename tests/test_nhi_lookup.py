import csv
import importlib.util
import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nhi" / "build_nhi_lookup.py"
SPEC = importlib.util.spec_from_file_location("build_nhi_lookup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

UPDATE_SCRIPT = ROOT / "tools" / "nhi" / "update_from_official.py"
UPDATE_SPEC = importlib.util.spec_from_file_location("update_from_official", UPDATE_SCRIPT)
UPDATE_MODULE = importlib.util.module_from_spec(UPDATE_SPEC)
assert UPDATE_SPEC and UPDATE_SPEC.loader
UPDATE_SPEC.loader.exec_module(UPDATE_MODULE)


FIELDNAMES = [
    "異動", "藥品代號", "藥品英文名稱", "藥品中文名稱", "成分", "規格量", "規格單位",
    "單複方", "支付價", "有效起日", "有效迄日", "藥商", "製造廠名稱", "劑型",
    "藥品分類", "分類分組名稱", "ATC代碼", "給付規定章節", "藥品代碼超連結",
    "給付規定章節連結",
]


def row(code: str, start: str, end: str, chapter: str, name: str) -> dict[str, str]:
    result = {key: "" for key in FIELDNAMES}
    result.update({
        "藥品代號": code,
        "藥品英文名稱": name,
        "藥品中文名稱": "測試藥品",
        "成分": "TEST INGREDIENT",
        "規格量": "100",
        "規格單位": "MG",
        "支付價": "12.30",
        "有效起日": start,
        "有效迄日": end,
        "藥商": "測試藥商",
        "劑型": "注射劑",
        "ATC代碼": "L01XX00",
        "給付規定章節": chapter,
        "藥品代碼超連結": "https://example.invalid/drug",
        "給付規定章節連結": "https://example.invalid/rule.pdf",
    })
    return result


class NhiLookupTest(unittest.TestCase):
    def test_current_rows_and_latest_duplicate_are_kept(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "items.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
                writer.writeheader()
                writer.writerows([
                    row("A001", "1140101", "", "9.26.", "OLD NAME"),
                    row("A001", "1150101", "", "9.26.", "LATEST NAME"),
                    row("A002", "1120101", "1141231", "9.9.1.", "EXPIRED"),
                    row("A003", "1160101", "", "9.9.1.", "FUTURE"),
                ])
            records, stats = MODULE.load_current_records(csv_path, date(2026, 8, 21))
        self.assertEqual([item["code"] for item in records], ["A001"])
        self.assertEqual(records[0]["en"], "LATEST NAME")
        self.assertEqual(stats["source"], 4)
        self.assertEqual(stats["current_unique"], 1)

    def test_exact_heading_parent_fallback_and_sibling_boundary(self):
        text = """
1.3.2.6 Carbamazepine（測試）
第一段內容
1.3.2.7.Next rule
不得混入上一章
9.9.Vinorelbine
父章節內容
9.10.Next rule
不得混入父章節
"""
        chapters, missing = MODULE.extract_chapters(text, {"1.3.2.6", "9.9.1"})
        self.assertEqual(missing, [])
        self.assertIn("第一段內容", chapters["1.3.2.6"]["text"])
        self.assertNotIn("不得混入上一章", chapters["1.3.2.6"]["text"])
        self.assertEqual(chapters["9.9.1"]["matchedChapter"], "9.9")
        self.assertIn("父章節內容", chapters["9.9.1"]["text"])
        self.assertNotIn("不得混入父章節", chapters["9.9.1"]["text"])

    def test_whole_rules_pdf_is_selected_from_official_page(self):
        page = """
        <a href="/ch/dl-small.pdf">分章載點</a>
        <a href="/ch/dl-whole.pdf">整份給付規定內容 (PDF)</a>
        """
        result = UPDATE_MODULE.find_whole_rules_pdf(
            page, "https://www.nhi.gov.tw/ch/np-2505-1.html"
        )
        self.assertEqual(result, "https://www.nhi.gov.tw/ch/dl-whole.pdf")

    def test_latest_page_version_is_selected_when_old_heading_remains(self):
        page = """
        <h1>最新版藥品給付規定內容(整份帶走)-115.07.23更新</h1>
        <div>最新版藥品給付規定內容(整份帶走)-115.8.21更新</div>
        <div>更新日期 115-08-21</div>
        """
        self.assertEqual(UPDATE_MODULE.detect_page_version(page), "115.8.21")

    def test_docx_modified_date_is_converted_to_roc_version(self):
        core = """<?xml version="1.0"?>
        <cp:coreProperties xmlns:cp="x" xmlns:dcterms="y">
          <dcterms:modified xsi:type="dcterms:W3CDTF" xmlns:xsi="z">2026-08-21T08:03:00Z</dcterms:modified>
        </cp:coreProperties>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "rules.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("docProps/core.xml", core)
            version = UPDATE_MODULE.detect_docx_modified_version(source)
        self.assertEqual(version, "115.8.21")

    def test_page_ahead_of_unchanged_pdf_is_pending(self):
        with self.assertRaises(UPDATE_MODULE.SourcePendingError):
            UPDATE_MODULE.assess_source_versions("115.8.21", "115.7.23", "115.7.23")

    def test_matching_page_and_pdf_are_ready(self):
        self.assertEqual(
            UPDATE_MODULE.assess_source_versions("115.8.21", "115.8.21", "115.7.23"),
            "ready",
        )

    def test_pdf_version_must_not_go_backwards(self):
        with self.assertRaises(RuntimeError):
            UPDATE_MODULE.assess_source_versions("115.7.23", "115.7.23", "115.8.21")

    def test_non_nhi_download_url_is_rejected(self):
        with self.assertRaises(RuntimeError):
            UPDATE_MODULE.checked_url("https://example.com/items.csv")

    def test_tfda_official_download_url_is_allowed(self):
        url = "https://data.fda.gov.tw/data/opendata/export/37/json"
        self.assertEqual(UPDATE_MODULE.checked_url(url), url)

    def test_own_pages_metadata_url_is_allowed(self):
        url = "https://lcctaiwan.github.io/chemodrugstable-query/nhi/data_version.json"
        self.assertEqual(UPDATE_MODULE.checked_url(url), url)

    def test_tfda_zip_is_deduplicated_and_cancelled_license_is_excluded(self):
        rows = [
            {
                "許可證字號": "衛部菌疫輸字第001245號",
                "註銷狀態": "",
                "中文品名": "衛癌瑪注射液",
                "英文品名": "Vegzelma",
                "主成分略述": "BEVACIZUMAB",
                "劑型": "注射液劑",
                "有效日期": "2029/01/09",
            },
            {
                "許可證字號": "衛部菌疫輸字第001245號",
                "註銷狀態": "",
                "中文品名": "衛癌瑪注射液",
                "英文品名": "Vegzelma",
                "主成分略述": "BEVACIZUMAB",
                "劑型": "注射液劑",
                "有效日期": "2029/01/09",
            },
            {
                "許可證字號": "衛部藥製字第999999號",
                "註銷狀態": "已註銷",
                "中文品名": "停用品項",
                "英文品名": "CANCELLED",
                "主成分略述": "TEST",
                "劑型": "錠劑",
                "有效日期": "2020/01/01",
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "tfda.zip"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("37.json", json.dumps(rows, ensure_ascii=False))
            original_minimum = MODULE.MIN_TFDA_RECORDS
            MODULE.MIN_TFDA_RECORDS = 1
            try:
                labels, stats = MODULE.load_tfda_labels(source)
            finally:
                MODULE.MIN_TFDA_RECORDS = original_minimum
        self.assertEqual([item["license"] for item in labels], ["衛部菌疫輸字第001245號"])
        self.assertIn("%E8%A1%9B%E9%83%A8%E8%8F%8C%E7%96%AB%E8%BC%B8%E5%AD%97", labels[0]["url"])
        self.assertEqual(stats["duplicate"], 1)
        self.assertEqual(stats["cancelled"], 1)

    def test_official_download_filename_version_is_normalized(self):
        match = UPDATE_MODULE.re.search(
            r"(?<!\d)(\d{3})(\d{2})(\d{2})(?!\d)",
            "attachment; filename*=UTF-8''完整給付規定1150723.pdf",
        )
        self.assertIsNotNone(match)
        self.assertEqual(UPDATE_MODULE.normalize_version_match(match.groups()), "115.7.23")

    def test_template_uses_structural_layout_without_keyword_highlights(self):
        template = MODULE.DEFAULT_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("logicalRuleBlocks", template)
        self.assertIn("ruleUrlFor", template)
        self.assertIn("__LABELS_JSON__", template)
        self.assertIn("TFDA 電子仿單", template)
        self.assertIn("rule-details", template)
        self.assertNotIn("mark-bio", template)
        self.assertNotIn("<mark", template)


if __name__ == "__main__":
    unittest.main()
