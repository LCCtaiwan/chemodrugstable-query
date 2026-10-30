"""Protect all-item lookup defaults; status filters must be explicit choices."""
import unittest
from html.parser import HTMLParser
from pathlib import Path

class SelectDefaults(HTMLParser):
    def __init__(self):super().__init__();self.current=None;self.options={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='select':self.current=a.get('id');self.options[self.current]=[]
        if tag=='option' and self.current:self.options[self.current].append((a.get('value'),'selected' in a))
    def handle_endtag(self,tag):
        if tag=='select':self.current=None

class AllItemDefaults(unittest.TestCase):
    def setUp(self):self.page=Path('inserts/index.html').read_text();self.parser=SelectDefaults();self.parser.feed(self.page)
    def test_all_licenses_default_not_revoked_subset(self):
        self.assertEqual([v for v,selected in self.parser.options['licenseFilter'] if selected],['all'])
    def test_keep_explicit_status_choices(self):
        self.assertEqual([v for v,_ in self.parser.options['licenseFilter']],['all','marked','unmarked'])
    def test_catalogue_and_auxiliary_body_modes_distinct(self):
        self.assertIn('藥品目錄：中英文藥名／成分／許可證',self.page)
        self.assertIn('輔助功能：已收錄 PDF 內文搜尋',self.page)
        self.assertIn('不限制上方藥品目錄查詢',self.page)
        self.assertNotIn('預設未標註銷／廢止',self.page)

if __name__=='__main__':unittest.main()
