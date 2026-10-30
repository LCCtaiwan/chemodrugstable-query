'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),S=require('../inserts/search.js');
const catalog=JSON.parse(fs.readFileSync('data/inserts/catalog.json','utf8')),items=S.validateCatalog(catalog);
assert.equal(items.length,catalog.license_count);assert.ok(items.length>10000);
for(const i of [items[0],items[Math.floor(items.length/2)],items.at(-1)])assert.ok(S.search(items,[],i.license_no,'name').some(g=>g.license===i.license_no));
assert.equal(S.search(items,[],'','name').length,0);
for(const u of ['javascript:alert(1)','https://evil.example/a','http://mcp.fda.gov.tw/a','https://mcp.fda.gov.tw@evil.example/a'])assert.equal(S.safeSource(u),null);
const bad={...catalog,with_link_count:catalog.with_link_count+1};assert.throws(()=>S.validateCatalog(bad));
const i=items.find(x=>x.source_urls.length===0);assert.ok(S.search(items,[],i.license_no,'name','no_link').some(g=>g.license===i.license_no));
assert.equal(S.search(items,[],'not-a-real-drug-synthetic-value-87654','text').length,0);
const full={license_no:'SYNTHETIC',drug_name:'測試',english_name:'Test',aliases:['別名'],ingredients:['AMPLE'],license_statuses:['已註銷'],expiry_dates:['2000/01/01'],source_urls:['https://mcp.fda.gov.tw/a'],in_license_catalog:true,detail_shard:'aa'};
const doc={license_no:'SYNTHETIC',status:'text_available',last_check_status:'error',source_url:full.source_urls[0],pages:[{page_no:1,text:'腎功能測試原文'}]};
assert.equal(S.search([full],[doc],'ａｍｐｌｅ','name').length,1);
assert.equal(S.search([full],[doc],'別名','name').length,1);
assert.equal(S.search([full],[doc],'腎功能','text').length,1);
assert.equal(S.search([full],[doc],'Test','text').length,0);
assert.equal(S.search([full],[doc],'Test','name','all','unmarked').length,0);
assert.equal(S.search([full],[doc],'Test','name','all','marked').length,1);
for(const x of items){assert.match(x.detail_shard,/^[a-f0-9]{2}$/)}
console.log(`Catalogue/search checks passed: ${items.length} unique licenses, ${catalog.with_link_count} with insert links; not a full-text coverage claim.`);

for(const key of new Set(items.map(i=>i.detail_shard))){const payload=JSON.parse(fs.readFileSync('data/inserts/details/'+key+'.json','utf8'));assert.equal(payload.catalog_version,catalog.catalog_version);for(const i of items.filter(x=>x.detail_shard===key))S.validateDetails(payload,i.license_no,catalog.catalog_version)}
assert.throws(()=>S.validateDetails({catalog_version:'old',items:{}},'A',catalog.catalog_version),/version_changed/);
assert.throws(()=>S.validateDetails({catalog_version:catalog.catalog_version,items:{A:{}}},'A',catalog.catalog_version),/detail_invalid/);
console.log('All detail shards match catalogue version and license fields.');
