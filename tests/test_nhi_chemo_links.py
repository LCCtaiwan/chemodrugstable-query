"""Regression contract for the 1150914 query and its offline generator."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://lcctaiwan.github.io/chemodrugstable-query/nhi/'

def drugs(page):
    m = re.search(r'const DRUGS\s*=\s*', page)
    return json.JSONDecoder().raw_decode(page[m.end():])[0]

class ChemoNhiLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / 'index.html').read_text()
        cls.generator = (ROOT / 'tools/update-chemo.html').read_text()
        cls.template = json.loads(re.search(r'const APP_TEMPLATE = ("(?:[^"\\]|\\.)*");', cls.generator)[1])

    def test_verified_1150914_records_preserved(self):
        data = drugs(self.page)
        self.assertEqual(len(data), 85)
        canonical = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), 'f247a870c3f779f387dda5fbdb768516df0d11f9dff7e929c830f8a22bd6ed71')
        self.assertIn('1150914', self.page)

    def test_both_query_and_template_have_only_nhi_entries(self):
        for page in (self.page, self.template):
            self.assertEqual(page.count('data-nhi-query'), 1)
            self.assertNotIn('data-nhi-general', page)
            self.assertNotIn('data-nhi-drug', page)
            self.assertNotIn('nhi-section', page)
            self.assertIn("nhiLink.href=nhiLookupUrl(nhiTerm)", page)
            self.assertIn("nhiTerm?'查此藥健保給付':'健保給付查詢'", page)
            self.assertIn("const NHI_LOOKUP_URL = '" + URL + "';", page)
            self.assertIn("'?q=' + encodeURIComponent(term)", page)
            self.assertIn("return NHI_LOOKUP_URL + (term ?", page)
            self.assertNotIn('/inserts/', page)
            for tag in re.findall(r'<a\b[^>]+>', page):
                if 'data-nhi-' in tag:
                    self.assertIn('target="_blank"', tag)
                    self.assertIn('rel="noopener noreferrer"', tag)

    def test_generator_preserves_template_placeholders(self):
        for key in ('__DATA__', '__VER__', '__VER_DIL__', '__VER_STAB__', '__VER_NOTE__', '__BUILD__'):
            self.assertIn(key, self.template)
        self.assertIn('function buildQueryHtml(r)', self.generator)
        self.assertIn('function buildWordBytes(r)', self.generator)
        self.assertIn('function downloadWorkbook()', self.generator)
        self.assertIn('href="' + URL + '"', self.generator)
        self.assertNotIn('/inserts/', self.generator)

    def test_original_clinical_script_unchanged_except_template(self):
        script = re.search(r'<script[^>]*>(.*?)</script>', self.generator, re.S)[1]
        masked = re.sub(r'const APP_TEMPLATE = ("(?:[^"\\]|\\.)*");', 'const APP_TEMPLATE = "OMITTED";', script)
        self.assertEqual(hashlib.sha256(masked.encode()).hexdigest(), '777a62f8074f53b8fd0554e65f899128a00729030d3a1bbe86980df72ada7725')

if __name__ == '__main__':
    unittest.main()

