# ChemoDrugStable 查詢頁

化療調配核對查詢，資料版本 1150806，共 84 個品項。

- 本頁為核對輔助，不取代仿單、院內調配規範與雙人核對。
- 正式發布內容須依院內流程核對與簽核。
- 網頁為單一靜態 HTML，不會上傳使用者輸入內容。

## 健保給付條件查詢

- 線上頁面：<https://lcctaiwan.github.io/chemodrugstable-query/nhi/>
- 每週一（台灣時間）由 GitHub Actions 下載健保署藥品 CSV，清洗有效品項並重新建置。
- 給付規定 PDF 採人工核對過的官方直連與預期版本；PDF 內容版本不符時停止發布。
- 查詢結果保留官方規定原文與連結，不代替個案給付判定。
- 條文只依章節與條款層級重新排版，不使用關鍵字彩色標記。

資料來源設定在 `tools/nhi/rules_source.json`，建置紀錄會輸出為 `/nhi/data_version.json`。
