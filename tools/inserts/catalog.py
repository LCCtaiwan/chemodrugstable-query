"""Full official drug-license + insert catalogues. No clinical/status inference."""
from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile
import time
from collections import OrderedDict
from pathlib import Path
from tools.inserts.update import CATALOG, validate_url, read_catalog, download, write_json
APPEARANCE_CATALOG = 'https://data.fda.gov.tw/data/opendata/export/42/json'
LICENSE_CATALOG = 'https://data.fda.gov.tw/data/opendata/export/36/json'


def read_licenses(data):
    if data.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries=[e for e in z.infolist() if e.filename.endswith('.json')]
            if len(entries)!=1 or entries[0].file_size>128*1024*1024:
                raise ValueError('Unexpected license archive')
            data=z.read(entries[0])
    rows=json.loads(data.decode('utf-8-sig'))
    if not isinstance(rows,list) or len(rows)<10000 or any(not isinstance(r,dict) or not r.get('許可證字號') or '註銷狀態' not in r for r in rows):
        raise ValueError('License catalog incomplete or schema changed')
    return rows


def build_catalog(rows, checked_at, source_sha256, licenses=None, license_sha256=None, appearance=None, appearance_sha256=None):
    # Each official license can have multiple manufacturer rows. Preserve distinct names/statuses.
    grouped=OrderedDict()
    def item(row, source):
        key=row['許可證字號']
        if key not in grouped:
            grouped[key]={'license_no':key,'drug_name':'','english_name':'','aliases':[], 'ingredients':[],
                          'source_urls':[],'unusable_link_count':0,'license_statuses':[], 'expiry_dates':[],
                          'in_license_catalog':False,'in_insert_catalog':False,'in_appearance_catalog':False, 'details':{}, 'image_urls':[]}
        i=grouped[key];i['in_'+source+'_catalog']=True
        for field,dest in [('中文品名','drug_name'),('英文品名','english_name')]:
            name=row.get(field) or ''
            if not i[dest]:i[dest]=name
            if name and name not in i['aliases']:i['aliases'].append(name)
        return i
    for row in licenses or []:
        i=item(row,'license')
        for field,dest in [('註銷狀態','license_statuses'),('有效日期','expiry_dates'),('主成分略述','ingredients')]:
            value=row.get(field) or ''
            if value not in i[dest] and (value or dest=='license_statuses'):i[dest].append(value)
        for field,key in [('適應症','indications'),('用法用量','dosage'),('劑型','dosage_forms'),('藥品類別','categories'),('申請商名稱','applicants'),('製造商名稱','manufacturers')]:
            value=row.get(field) or ''
            if value and value not in i['details'].setdefault(key,[]):i['details'][key].append(value)
    for row in rows:
        i=item(row,'insert')
        for url in dict.fromkeys(u.strip() for u in (row.get('仿單圖檔連結') or '').split(';') if u.strip()):
            try:
                validate_url(url)
                if url not in i['source_urls']:i['source_urls'].append(url)
            except ValueError:i['unusable_link_count']+=1
        for url in dict.fromkeys(u.strip() for u in (row.get('外盒圖檔連結') or '').split(';') if u.strip()):
            try:
                validate_url(url)
                if url not in i['image_urls']:i['image_urls'].append(url)
            except ValueError:pass
    for row in appearance or []:
        i=item(row,'appearance')
        record={k:(row.get(k) or '') for k in ['形狀','特殊劑型','顏色','特殊氣味','刻痕','外觀尺寸','標註一','標註二']}
        if record not in i['details'].setdefault('appearance',[]):i['details']['appearance'].append(record)
        for url in (row.get('外觀圖檔連結') or '').split(';'):
            url=url.strip()
            if not url:continue
            try:
                validate_url(url)
                if url not in i['details'].setdefault('appearance_urls',[]):i['details']['appearance_urls'].append(url)
            except ValueError:pass
    items=list(grouped.values())
    return {'schema_version':4,'catalog_version':hashlib.sha256((source_sha256+'|'+(license_sha256 or '')+'|'+(appearance_sha256 or '')).encode()).hexdigest(),'checked_at':checked_at,'source_url':CATALOG,'source_sha256':source_sha256,
            'license_source_url':LICENSE_CATALOG if licenses is not None else None,'license_source_sha256':license_sha256,
            'source_row_count':len(rows),'license_source_row_count':len(licenses) if licenses is not None else 0,
            'insert_catalog_license_count':len({r['許可證字號'] for r in rows}),
            'drug_catalog_license_count':len({r['許可證字號'] for r in licenses or []}),
            'appearance_source_url':APPEARANCE_CATALOG if appearance is not None else None,'appearance_source_sha256':appearance_sha256,
            'appearance_row_count':len(appearance or []),'appearance_catalog_license_count':sum(i['in_appearance_catalog'] for i in items),'appearance_matched_base_license_count':sum(i['in_appearance_catalog'] and (i['in_license_catalog'] or i['in_insert_catalog']) for i in items),
            'license_count':len(items),'with_link_count':sum(bool(i['source_urls']) for i in items),
            'without_link_count':sum(not i['source_urls'] for i in items),
            'unique_document_count':sum(len(i['source_urls']) for i in items),
            'status_scope':'Union of official InfoId36 licenses, InfoId39 inserts and InfoId42 appearance; raw cancellation/expiry fields are not market availability or clinical eligibility.',
            'items':items}


def write_catalog(output, data):
    # Keep detailed labels out of the initial payload. Fetch one official-data shard on expansion.
    shards={}
    for i in data['items']:
        shard=hashlib.sha256(i['license_no'].encode()).hexdigest()[:2]
        details=i.pop('details',{})
        details['image_urls']=i.pop('image_urls',[])
        shards.setdefault(shard,{})[i['license_no']]=details
        i['detail_shard']=shard
    output.parent.mkdir(parents=True,exist_ok=True)
    for key, values in shards.items():
        p=output.parent/'details'/(key+'.json');p.parent.mkdir(exist_ok=True)
        tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'catalog_version':data['catalog_version'],'items':values},ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(p)
    data['detail_shard_count']=len(shards)
    tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(output)


def read_appearance(data):
    if data.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries=[e for e in z.infolist() if e.filename.endswith('.json')]
            if len(entries)!=1 or entries[0].file_size>32*1024*1024:raise ValueError('Unexpected appearance archive')
            data=z.read(entries[0])
    rows=json.loads(data.decode('utf-8-sig'))
    if not isinstance(rows,list) or len(rows)<1000 or any(not isinstance(r,dict) or not r.get('許可證字號') or '外觀圖檔連結' not in r for r in rows):raise ValueError('Appearance catalog incomplete or schema changed')
    return rows


def fetch_catalog(url, parser, fetch=download):
    # A successful HTTP response can still contain a truncated ZIP. Never publish it.
    # Retry parse/transfer corruption once at the same public URL; HTTP403 is not retried here.
    for attempt in range(2):
        raw=fetch(url, timeout=90)
        try:
            rows=parser(raw)
            return raw, rows
        except (zipfile.BadZipFile, EOFError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if attempt:raise ValueError(f'Invalid catalogue after two reads: {url}; bytes={len(raw)}; {type(exc).__name__}') from exc
            print(f'Incomplete catalogue response; retrying same official URL once: {url}; bytes={len(raw)}',flush=True)
            time.sleep(2)


def refresh(output):
    raw,rows=fetch_catalog(CATALOG,read_catalog)
    license_raw,licenses=fetch_catalog(LICENSE_CATALOG,read_licenses)
    appearance_raw,appearance=fetch_catalog(APPEARANCE_CATALOG,read_appearance)
    data=build_catalog(rows,datetime.now(timezone.utc).isoformat(),hashlib.sha256(raw).hexdigest(),licenses,hashlib.sha256(license_raw).hexdigest(),appearance,hashlib.sha256(appearance_raw).hexdigest())
    # A major unexplained drop requires review; retain the prior usable catalogue.
    if output.exists():
        old=json.loads(output.read_text())
        for key in ['insert_catalog_license_count','drug_catalog_license_count']:
            prior=old.get(key,0)
            if prior and data[key]<prior*.8:raise ValueError('Catalogue shrank over 20%; previous snapshot retained')
    write_catalog(output,data)
    return data


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('data/inserts/catalog.json'));args=parser.parse_args()
    data=refresh(args.output);print(json.dumps({k:v for k,v in data.items() if k!='items'},ensure_ascii=False))
