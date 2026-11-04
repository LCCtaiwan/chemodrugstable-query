"""Offline regressions for complete TFDA downloads and fail-closed publication."""
import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from argparse import Namespace
from pathlib import Path
from unittest import mock

from tools.nhi import build_nhi_lookup as builder
from tools.nhi import update_from_official as updater

URL = builder.OFFICIAL_TFDA_LICENSES_URL
ROWS = [{column: "test" for column in builder.TFDA_REQUIRED_COLUMNS}]
JSON = json.dumps(ROWS).encode("utf-8")


def archive_bytes(payload=JSON, names=("licenses.json",)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in names:
            archive.writestr(name, payload)
    return buffer.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, headers=None, url=URL):
        super().__init__(data)
        self.headers = headers or {}
        self.url = url

    def geturl(self):
        return self.url


class DownloadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "licenses.zip"
        self.sleep = mock.patch.object(updater.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def download(self, responses, **kwargs):
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=responses) as opened:
            result = updater.download(URL, self.target, validator=builder.read_tfda_json, **kwargs)
        return result, opened

    def test_valid_zip_first_attempt(self):
        raw = archive_bytes()
        result, opened = self.download([Response(raw, {"Content-Length": str(len(raw)), "Content-Disposition": "attachment"})])
        self.assertEqual(result, (URL, "attachment"))
        self.assertEqual(self.target.read_bytes(), raw)
        self.assertEqual(opened.call_count, 1)
        self.sleep.assert_not_called()

    def test_plain_json_and_bom_still_supported_without_length(self):
        for raw in (JSON, b"\xef\xbb\xbf" + JSON):
            with self.subTest(raw=raw):
                self.download([Response(raw)])
                self.assertEqual(builder.read_tfda_json(self.target), ROWS)

    def test_truncated_zip_is_never_decoded_as_text(self):
        self.target.write_bytes(archive_bytes()[:-22])
        self.assertFalse(zipfile.is_zipfile(self.target))
        with self.assertRaises(zipfile.BadZipFile):
            builder.read_tfda_json(self.target)

    def test_corruption_retries_then_succeeds(self):
        valid = archive_bytes()
        crc_bad = bytearray(valid)
        offset = 30 + len("licenses.json")
        crc_bad[offset] ^= 1
        for bad in (valid[:-22], bytes(crc_bad), b"\xff", b"[{", b"<html>upstream error</html>", b""):
            with self.subTest(bad=bad[:20]):
                _, opened = self.download([Response(bad), Response(valid)], attempts=2)
                self.assertEqual(opened.call_count, 2)
                self.assertEqual(self.target.read_bytes(), valid)
                self.assertEqual(list(self.target.parent.glob("*.part")), [])
                self.assertEqual(opened.call_args_list[0].args[0].full_url, URL)
                self.assertEqual(opened.call_args_list[1].args[0].full_url, URL)

    def test_content_length_mismatch_retries_even_if_json_parses(self):
        for length in (str(len(JSON) + 1), str(len(JSON) - 1), "invalid", "-1"):
            with self.subTest(length=length):
                _, opened = self.download([Response(JSON, {"Content-Length": length}), Response(JSON)], attempts=2)
                self.assertEqual(opened.call_count, 2)

    def test_exhaustion_preserves_existing_target_and_cleans_partial_files(self):
        self.target.write_bytes(b"existing verified data")
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=lambda *a, **k: Response(b"PKbroken")) as opened:
            with self.assertRaisesRegex(updater.TransientDownloadError, "BadZipFile.*bytes=8.*sha256="):
                updater.download(URL, self.target, attempts=3, validator=builder.read_tfda_json)
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)
        self.assertEqual(self.target.read_bytes(), b"existing verified data")
        self.assertEqual(list(self.target.parent.glob("*.part")), [])

    def test_failed_download_does_not_create_target(self):
        with self.assertRaises(updater.TransientDownloadError):
            self.download([Response(b"PKbroken")], attempts=1)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_invalid_schema_is_not_hidden_as_transient(self):
        for raw in (b"[]", b'[{}]', archive_bytes(names=()), archive_bytes(names=("a.json", "b.json"))):
            with self.subTest(raw=raw):
                with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(raw)) as opened:
                    with self.assertRaises(RuntimeError) as caught:
                        updater.download(URL, self.target, validator=builder.read_tfda_json)
                self.assertNotIsInstance(caught.exception, updater.TransientDownloadError)
                self.assertEqual(opened.call_count, 1)
                self.assertFalse(self.target.exists())
        self.sleep.assert_not_called()

    def test_http_denials_and_permanent_failures_are_not_retried(self):
        for code in (401, 403, 404):
            with self.subTest(code=code):
                error = urllib.error.HTTPError(URL, code, "denied", {}, None)
                with mock.patch.object(updater.urllib.request, "urlopen", side_effect=error) as opened:
                    with self.assertRaises(urllib.error.HTTPError):
                        updater.download(URL, self.target)
                self.assertEqual(opened.call_count, 1)
        self.sleep.assert_not_called()

    def test_retryable_http_error_then_success(self):
        for code in (408, 429, 500, 503):
            with self.subTest(code=code):
                _, opened = self.download([urllib.error.HTTPError(URL, code, "temporary", {}, None), Response(JSON)], attempts=2)
                self.assertEqual(opened.call_count, 2)

    def test_unsafe_redirect_is_not_retried(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(JSON, url="https://example.com/data")) as opened:
            with self.assertRaisesRegex(RuntimeError, "不允許"):
                updater.download(URL, self.target)
        self.assertEqual(opened.call_count, 1)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob("*.part")), [])

    def test_programming_error_in_validator_is_not_retried(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(JSON)) as opened:
            with self.assertRaises(TypeError):
                updater.download(URL, self.target, validator=mock.Mock(side_effect=TypeError("bug")))
        self.assertEqual(opened.call_count, 1)

    def test_no_validator_still_downloads_other_sources(self):
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=Response(b"%PDF-example")):
            updater.download(URL, self.target)
        self.assertEqual(self.target.read_bytes(), b"%PDF-example")

    def test_invalid_attempt_count_never_opens_network(self):
        with mock.patch.object(updater.urllib.request, "urlopen") as opened:
            with self.assertRaises(ValueError):
                updater.download(URL, self.target, attempts=0)
        opened.assert_not_called()

    def test_exhausted_tfda_validation_marks_unavailable_without_building(self):
        # Exercise real run_update -> download -> parser -> main status handling.
        output = self.target.parent / "index.html"
        version = self.target.parent / "data_version.json"
        status = self.target.parent / "source_status.json"
        output.write_text("existing page")
        version.write_text("existing metadata")
        args = Namespace(output=output, as_of="2026-11-04", items_url=None,
                         tfda_licenses_url=None, rules_page=None,
                         rules_pdf_url="https://www.nhi.gov.tw/rules.pdf",
                         rules_docx_url=None, expected_rules_version=None,
                         previous_version_url=None, status_file=status)
        responses = [Response(b"pdf"), Response(b"csv")] + [Response(b"PKbroken") for _ in range(5)]
        with (
            mock.patch.object(updater, "parse_args", return_value=args),
            mock.patch.object(updater, "load_builder", return_value=builder),
            mock.patch.object(updater, "inspect_rules_pdf", return_value={"metadataVersion": "115.11.2"}),
            mock.patch.object(updater, "detect_rules_version", return_value="115.11.2"),
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=responses),
            mock.patch.object(builder, "build") as build,
        ):
            updater.main()
        build.assert_not_called()
        self.assertEqual(json.loads(status.read_text())["status"], "unavailable")
        self.assertEqual(output.read_text(), "existing page")
        self.assertEqual(version.read_text(), "existing metadata")


if __name__ == "__main__":
    unittest.main()
