import unittest
from tools.inserts.catalog import build_catalog, read_licenses, fetch_catalog
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

class CatalogueTests(unittest.TestCase):
    def test_full_union_no_link_rows_and_duplicate_manufacturers(self):
        lic=[{'許可證字號':'A','中文品名':'第一名','英文品名':None,'註銷狀態':'已註銷','有效日期':'2000/01/01','主成分略述':'TEST'},
             {'許可證字號':'A','中文品名':'別名','註銷狀態':'','有效日期':'2001/01/01'},
             {'許可證字號':'B','中文品名':None,'英文品名':'Second','註銷狀態':''}]
        inserts=[{'許可證字號':'A','中文品名':'仿單名','仿單圖檔連結':'https://mcp.fda.gov.tw/a;https://mcp.fda.gov.tw/a'},
                 {'許可證字號':'C','中文品名':'Only insert','仿單圖檔連結':''}]
        d=build_catalog(inserts,'now','sha39',lic,'sha36')
        self.assertEqual(d['license_count'],3)
        self.assertEqual(d['drug_catalog_license_count'],2)
        self.assertEqual(d['insert_catalog_license_count'],2)
        self.assertEqual(d['with_link_count'],1)
        self.assertEqual(d['without_link_count'],2)
        self.assertEqual(d['unique_document_count'],1)
        a,b,c=d['items']
        self.assertEqual(a['license_statuses'],['已註銷',''])
        self.assertEqual(a['aliases'],['第一名','別名','仿單名'])
        self.assertEqual(a['expiry_dates'],['2000/01/01','2001/01/01'])
        self.assertEqual(b['drug_name'],'')
        self.assertFalse(c['in_license_catalog'])
        self.assertEqual(c['license_statuses'],[])

    def test_unsafe_links_not_rendered_but_item_retained(self):
        rows=[{'許可證字號':'A','仿單圖檔連結':'http://mcp.fda.gov.tw/a;https://evil.example/b;https://mcp.fda.gov.tw/good'}]
        d=build_catalog(rows,'now','sha')
        self.assertEqual(d['items'][0]['unusable_link_count'],2)
        self.assertEqual(d['items'][0]['source_urls'],['https://mcp.fda.gov.tw/good'])

    def test_truncated_license_source_rejected(self):
        with self.assertRaises(ValueError):read_licenses(b'[]')

    def test_duplicate_insert_rows_merge_links(self):
        d=build_catalog([{'許可證字號':'A','中文品名':'One','仿單圖檔連結':'https://mcp.fda.gov.tw/a'}, {'許可證字號':'A','中文品名':'Two','仿單圖檔連結':'https://mcp.fda.gov.tw/b'}],'now','sha')
        self.assertEqual(d['source_row_count'],2)
        self.assertEqual(d['license_count'],1)
        self.assertEqual(d['items'][0]['aliases'],['One','Two'])
        self.assertEqual(len(d['items'][0]['source_urls']),2)

class CatalogueTransferTests(unittest.TestCase):
    def test_retry_corrupt_zip_once(self):
        calls=[]
        def fetch(url,timeout):calls.append(url);return b'PKbroken' if len(calls)==1 else b'good'
        def parse(raw):
            if raw.startswith(b'PK'):raise zipfile.BadZipFile('truncated')
            return ['verified']
        with patch('tools.inserts.catalog.time.sleep'):
            self.assertEqual(fetch_catalog('https://data.fda.gov.tw/data/opendata/export/36/json',parse,fetch=fetch),(b'good',['verified']))
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])

    def test_no_retry_denied_http(self):
        calls=[]
        def fetch(url,timeout):calls.append(url);raise HTTPError(url,403,'Forbidden',{},None)
        with self.assertRaises(HTTPError):fetch_catalog('https://data.fda.gov.tw/a',lambda x:[],fetch=fetch)
        self.assertEqual(len(calls),1)

    def test_fail_after_second_corrupt_response(self):
        calls=[]
        def fetch(url,timeout):calls.append(url);return b'PKbroken'
        def parse(raw):raise zipfile.BadZipFile('truncated')
        with patch('tools.inserts.catalog.time.sleep'):
            with self.assertRaisesRegex(ValueError,'after two reads'):fetch_catalog('https://data.fda.gov.tw/a',parse,fetch=fetch)
        self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
