import unittest
from unittest.mock import patch
from tools.inserts.update import catalog_documents, update_document, validate_url, read_catalog


class InsertTests(unittest.TestCase):
    def test_multiple_urls_and_deduplication(self):
        row = {'許可證字號': '藥證A', '仿單圖檔連結': 'https://mcp.fda.gov.tw/a;https://mcp.fda.gov.tw/b'}
        self.assertEqual(len(catalog_documents([row, row])), 2)

    def test_failed_download_preserves_evidence(self):
        doc = {'id': 'a', 'source_url': 'https://mcp.fda.gov.tw/a'}
        old = {'pages': [{'page_no': 1, 'text': '原文'}], 'file_sha256': 'old', 'status': 'text_available'}
        def fail(url):
            raise TimeoutError('timeout')
        result = update_document(doc, old, fetch=fail)
        self.assertEqual(result['pages'], old['pages'])
        self.assertEqual(result['file_sha256'], 'old')
        self.assertEqual(result['last_check_status'], 'error')

    def test_same_url_new_content_updates(self):
        doc = {'id': 'a', 'source_url': 'https://mcp.fda.gov.tw/a'}
        with patch('tools.inserts.update.extract', return_value=('text_available', [{'page_no': 1, 'text': '新版'}])):
            result = update_document(doc, {'file_sha256': 'old'}, fetch=lambda u: b'%PDF-new')
        self.assertEqual(result['pages'][0]['text'], '新版')
        self.assertEqual(result['last_check_status'], 'changed')

    def test_changed_scan_clears_outdated_text(self):
        doc = {'id': 'a', 'source_url': 'https://mcp.fda.gov.tw/a'}
        with patch('tools.inserts.update.extract', return_value=('needs_review_no_ocr', [])):
            result = update_document(doc, {'file_sha256': 'old', 'pages': ['old']}, fetch=lambda u: b'%PDF-scan')
        self.assertEqual(result['pages'], [])
        self.assertEqual(result['status'], 'needs_review_no_ocr')

    def test_reject_unofficial_sources_and_small_catalog(self):
        for url in ('http://mcp.fda.gov.tw/a', 'https://evil.example/a', 'https://mcp.fda.gov.tw.evil.example/a'):
            with self.assertRaises(ValueError):
                validate_url(url)
        with self.assertRaises(ValueError):
            read_catalog(b'[]')
