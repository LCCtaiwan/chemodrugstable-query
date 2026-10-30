/* Pure catalogue/search helpers. No remote requests or clinical inference. */
(function(root){
'use strict';
const normalized=s=>String(s||'').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
const safeSource=url=>{try{const u=new URL(url);return u.protocol==='https:'&&['mcp.fda.gov.tw','data.fda.gov.tw'].includes(u.hostname)&&!u.username&&!u.password&&(!u.port||u.port==='443')?u.href:null}catch{return null}};
function validateCatalog(data){
 if(!data||data.schema_version!==4||!Array.isArray(data.items)||data.items.length!==data.license_count||!data.items.length||!/^[a-f0-9]{64}$/.test(data.catalog_version||''))throw Error('catalog_invalid');
 for(const i of data.items)if(!/^[a-f0-9]{2}$/.test(i.detail_shard||'')||['in_license_catalog','in_insert_catalog','in_appearance_catalog'].some(k=>typeof i[k]!=='boolean')||typeof i.license_no!=='string'||!i.license_no.trim()||typeof i.drug_name!=='string'||typeof i.english_name!=='string'||!Array.isArray(i.aliases)||!Array.isArray(i.ingredients)||!Array.isArray(i.license_statuses)||!Array.isArray(i.expiry_dates)||[...i.aliases,...i.ingredients,...i.license_statuses,...i.expiry_dates].some(v=>typeof v!=='string')||!Array.isArray(i.source_urls)||i.source_urls.some(u=>typeof u!=='string'||!safeSource(u)))throw Error('catalog_invalid');
 if(data.appearance_catalog_license_count!==data.items.filter(i=>i.in_appearance_catalog).length)throw Error('appearance_count_mismatch');
 if(data.drug_catalog_license_count!==data.items.filter(i=>i.in_license_catalog).length||data.insert_catalog_license_count!==data.items.filter(i=>i.in_insert_catalog).length||data.source_row_count<data.insert_catalog_license_count||data.license_source_row_count<data.drug_catalog_license_count)throw Error('catalog_source_count_mismatch');
 if(new Set(data.items.map(i=>i.license_no)).size!==data.license_count)throw Error('catalog_count_mismatch');
 if(data.with_link_count!==data.items.filter(i=>i.source_urls.length).length||data.without_link_count!==data.items.filter(i=>!i.source_urls.length).length||data.unique_document_count!==new Set(data.items.flatMap(i=>i.source_urls.map(u=>i.license_no+'\n'+u))).size)throw Error('catalog_coverage_mismatch');
 return data.items;
}
function search(items,documents,query,mode,filter='all',licenseFilter='all'){
 const q=normalized(query);if(!q)return [];
 const docs=new Map();for(const d of documents){if(!docs.has(d.license_no))docs.set(d.license_no,[]);docs.get(d.license_no).push(d)}
 const groups=new Map();
 for(const i of items){
  const sources=docs.get(i.license_no)||[];
  const hits=sources.map(d=>({document:d,pages:(['text_available','partial_text'].includes(d.status)&&Array.isArray(d.pages))?d.pages.filter(p=>typeof p.text==='string'&&normalized(p.text).includes(q)):[]}));
  if(mode==='text'?!hits.some(h=>h.pages.length):!normalized([i.drug_name,i.english_name,i.license_no,...(i.aliases||[]),...(i.ingredients||[])].join(' ')).includes(q))continue;
  const marked=(i.license_statuses||[]).some(v=>v==='已註銷'||v==='已廢止');
  if(licenseFilter==='marked'&&!marked||licenseFilter==='unmarked'&&(marked||!i.in_license_catalog))continue;
  const hasText=sources.some(d=>['text_available','partial_text'].includes(d.status)&&d.pages?.length);
  const failed=sources.some(d=>d.last_check_status==='error');
  if(filter==='text'&&!hasText||filter==='no_text'&&hasText||filter==='no_link'&&i.source_urls.length||filter==='failed'&&!failed)continue;
  if(!groups.has(i.license_no))groups.set(i.license_no,{license:i.license_no,name:i.drug_name,english:i.english_name,ingredients:i.ingredients||[],license_statuses:i.license_statuses||[],expiry_dates:i.expiry_dates||[],detail_shard:i.detail_shard,in_license_catalog:i.in_license_catalog,source_urls:[...i.source_urls],hits:mode==='text'?hits.filter(h=>h.pages.length):hits,hasText,failed});
 }
 return [...groups.values()];
}
function validateDetails(payload,license,version){
 if(!payload||payload.catalog_version!==version)throw Error('version_changed');
 const d=payload.items?.[license];if(!d||typeof d!=='object'||Array.isArray(d)||!Array.isArray(d.image_urls))throw Error('detail_invalid');
 for(const k of ['indications','dosage','dosage_forms','categories','applicants','manufacturers','image_urls','appearance_urls'])if(k in d&&(!Array.isArray(d[k])||d[k].some(v=>typeof v!=='string')))throw Error('detail_invalid');
 return d;
}
function errorLabel(d){const s=String(d.error||'');if(/403|401/.test(s))return '官方來源拒絕存取，未繞過限制';if(/not a PDF/.test(s))return '來源回應不是 PDF，尚無可搜尋原文';if(/exceeds|oversized/.test(s))return '超過本次處理大小限制，未下載或解析';if(/Timeout|timed out/.test(s))return '來源連線逾時';return '下載或解析未成功'}
const api={normalized,safeSource,validateCatalog,validateDetails,search,errorLabel};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.InsertSearch=api;
})(typeof window==='undefined'?globalThis:window);
