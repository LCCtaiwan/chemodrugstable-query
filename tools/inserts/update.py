"""Bounded electronic PDF pilot. No OCR; no inference of clinical sections."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
import zipfile
from pypdf import PdfReader

CATALOG = 'https://data.fda.gov.tw/data/opendata/export/39/json'
HOSTS = {'data.fda.gov.tw', 'mcp.fda.gov.tw'}
LIMIT = 32 * 1024 * 1024


def validate_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError('URL outside official HTTPS sources')
    return url


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url):
    validate_url(url)
    for attempt in range(2):
        try:
            req = Request(quote(url, safe=':/?=&%'), headers={'User-Agent': 'TFDA-electronic-insert-pilot/1.0'})
            with build_opener(OfficialRedirect()).open(req, timeout=30) as response:
                data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('Download exceeds 32 MiB')
            return data
        except HTTPError as exc:
            if attempt or exc.code not in (429, 500, 502, 503, 504):
                raise
        except (TimeoutError, ConnectionError):
            if attempt:
                raise
        time.sleep(2)


def read_catalog(data):
    if data.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [e for e in archive.infolist() if e.filename.endswith('.json')]
            if len(entries) != 1 or entries[0].file_size > 64 * 1024 * 1024:
                raise ValueError('Unexpected catalog archive')
            data = archive.read(entries[0])
    rows = json.loads(data.decode('utf-8-sig'))
    if not isinstance(rows, list) or len(rows) < 10000:
        raise ValueError('Catalog incomplete; refusing update')
    if any(not isinstance(r, dict) or not r.get('許可證字號') or '仿單圖檔連結' not in r for r in rows):
        raise ValueError('Catalog schema changed')
    return rows


def catalog_documents(rows):
    docs = {}
    for row in rows:
        for url in (row.get('仿單圖檔連結') or '').split(';'):
            url = url.strip()
            if not url:
                continue
            key = hashlib.sha256((row['許可證字號'] + '\n' + url).encode()).hexdigest()
            docs[key] = dict(id=key, license_no=row['許可證字號'], drug_name=row.get('中文品名', ''), source_url=url)
    return docs


def extract(data):
    if not data.startswith(b'%PDF-'):
        raise ValueError('Response is not a PDF')
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 200:
        raise ValueError('Encrypted or oversized PDF')
    pages = [{'page_no': i + 1, 'text': page.extract_text() or ''} for i, page in enumerate(reader.pages)]
    counts = [len(''.join(p['text'].split())) for p in pages]
    # Conservative heuristic: every page must contain text. Mixed scans excluded.
    if not counts or min(counts) < 50:
        return 'needs_review_no_ocr', []
    if sum(p['text'].count('\ufffd') for p in pages) / sum(counts) > .01:
        return 'needs_review_no_ocr', []
    return 'text_available', pages


def update_document(doc, previous, fetch=download, now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    result = {**doc, 'last_checked': now, 'last_check_status': 'error', 'error': None}
    if previous:
        for key in ('file_sha256', 'pages', 'text_updated_at', 'status'):
            if key in previous:
                result[key] = previous[key]
    try:
        validate_url(doc['source_url'])
        payload = fetch(doc['source_url'])  # Always fetch even when URL is unchanged.
        digest = hashlib.sha256(payload).hexdigest()
        if digest == result.get('file_sha256'):
            result['last_check_status'] = 'unchanged'
        else:
            status, pages = extract(payload)
            result.update(file_sha256=digest, pages=pages, status=status, text_updated_at=now, last_check_status='changed')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result.setdefault('status', 'unavailable')
        result.setdefault('pages', [])
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('data/inserts'))
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('Pilot limit must be between 1 and 100')
    now = datetime.now(timezone.utc).isoformat()
    path = args.output / 'documents.json'
    previous = json.loads(path.read_text()) if path.exists() else {'documents': [], 'selected_ids': []}
    old = {d['id']: d for d in previous['documents']}
    try:
        catalog = catalog_documents(read_catalog(download(CATALOG)))
        # Preserve a fixed cohort for update comparisons, fill vacancies deterministically.
        selected = [k for k in previous.get('selected_ids', []) if k in catalog][:args.limit]
        selected += [k for k in sorted(catalog) if k not in selected][:args.limit - len(selected)]
        records = []
        for key in selected:
            records.append(update_document(catalog[key], old.get(key)))
            time.sleep(.3)
        ok = sum(d['last_check_status'] != 'error' for d in records)
        counts = dict(Counter(d['status'] for d in records))
        summary = dict(checked_at=now, scope='fixed PDF pilot; no OCR', catalog_documents=len(catalog), attempted=len(records), successful_checks=ok, statuses=counts, results=records)
        write_json(args.output / 'last_run.json', summary)
        # All-failed runs must not replace the last usable dataset.
        if not ok:
            raise RuntimeError('All downloads failed; previous documents.json retained')
        write_json(path, dict(checked_at=now, selected_ids=selected, documents=records))
        print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, ensure_ascii=False))
    except Exception as exc:
        write_json(args.output / 'failure.json', dict(checked_at=now, error=str(exc)))
        raise


if __name__ == '__main__':
    main()
