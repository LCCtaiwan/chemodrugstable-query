import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lookup', Path(__file__).resolve().parents[1] / 'tools/nhi/build_nhi_lookup.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def source(license='衛署藥製字第048092號', customs='DHY00104809200'):
    return {'許可證字號': license, '通關簽審文件編號': customs}


class LicenseLinksTest(unittest.TestCase):
    def test_only_official_unambiguous_url(self):
        base='https://lmspiq.fda.gov.tw/web/DRPIQ/DRPIQ1000Result?licId=01048092'
        self.assertEqual(m.official_license_id(base), '01048092')
        for url in [base.replace('https:', 'http:'), base.replace('.gov.tw', '.gov.tw.evil.com'),base+'&licId=02048092',base.replace('01048092','1048092')]:
            self.assertEqual(m.official_license_id(url), '')

    def test_customs_serial_must_agree(self):
        self.assertEqual(m.customs_license_id(source()), '01048092')
        self.assertEqual(m.customs_license_id(source(customs='DHY00104809300')), '')
        self.assertEqual(m.customs_license_id(source(customs='DHYS0104809200')), '01048092')
        self.assertEqual(m.customs_license_id(source(customs='')), '')

    def test_missing_cancelled_and_ambiguous_never_autolink(self):
        for rows, active, expected in [([source()], [source()['許可證字號']], 'matched'),
                                      ([source()], [], 'inactive'),
                                      ([], [], 'unresolved'),
                                      ([source(),source(license='衛署成製字第048092號')], [source()['許可證字號']], 'ambiguous')]:
            records=[{'code':'ANY_CODE','drugUrl':'https://lmspiq.fda.gov.tw/web/DRPIQ/DRPIQ1000Result?licId=01048092'}]
            with patch.object(m,'read_tfda_json',return_value=rows):
                stats=m.link_nhi_licenses(records,[{'license':x} for x in active],Path('unused'))
            self.assertEqual(records[0]['licenseStatus'],expected)
            self.assertEqual(bool(records[0]['licenses']),expected=='matched')
            self.assertEqual(stats['total'],1)

    def test_does_not_guess_from_nhi_code(self):
        records=[{'code':'AC48092100','drugUrl':''}]
        with patch.object(m,'read_tfda_json',return_value=[source()]):
            m.link_nhi_licenses(records,[{'license':source()['許可證字號']}],Path('unused'))
        self.assertEqual(records[0]['licenses'],[])
        self.assertEqual(records[0]['licenseStatus'],'missing_official_id')

    def test_radioactive_license_serial_keeps_r(self):
        self.assertEqual(m.customs_license_id(source('衛部藥輸字第R00091號','DHA052R0009101')), '52R00091')
        self.assertEqual(m.official_license_id('https://lmspiq.fda.gov.tw/web/DRPIQ/DRPIQ1000Result?licId=52R00091'), '52R00091')

    def test_missing_customs_requires_unambiguous_observed_type(self):
        rows=[source('衛署藥製字第000001號','DHY00100000100'),source('衛署藥製字第000002號','DHY00100000200'),source(customs='')]
        records=[{'drugUrl':'https://lmspiq.fda.gov.tw/web/DRPIQ/DRPIQ1000Result?licId=01048092'}]
        with patch.object(m,'read_tfda_json',return_value=rows):
            m.link_nhi_licenses(records,[{'license':source()['許可證字號']}],Path('unused'))
        self.assertEqual(records[0]['licenseMethod'],'license_type_normalization')
        self.assertEqual(records[0]['licenseStatus'],'matched')
        rows[1]['通關簽審文件編號']='DHY00300000200'
        with patch.object(m,'read_tfda_json',return_value=rows):
            m.link_nhi_licenses(records,[{'license':source()['許可證字號']}],Path('unused'))
        self.assertEqual(records[0]['licenseStatus'],'unresolved')
