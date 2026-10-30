# ChemoDrugStable 查詢頁

本專案提供兩個互相連結的 GitHub Pages 查詢工具。

| 頁面 | 用途 | 更新方式 |
| --- | --- | --- |
| [ChemoDrugStable 化療調配核對](https://lcctaiwan.github.io/chemodrugstable-query/) | 查詢院內化療藥品調配、安定性、給藥、監測及外滲資料 | 由院內已核對版本產生，目前為 1150914、85 個品項 |
| [健保給付與電子仿單](https://lcctaiwan.github.io/chemodrugstable-query/nhi/) | 查詢健保有效品項、給付條文及 TFDA 電子仿單入口 | GitHub Actions 每日檢查官方資料，驗證通過後重新部署 |

ChemoDrugStable 藥品頁可將清理後的學名帶入既有健保給付查詢。這是名稱關鍵字搜尋，不是院內藥品與健保碼的一對一配對。

## 專案結構

```text
index.html                         ChemoDrugStable 主查詢頁
tools/nhi/template.html            健保／仿單頁面模板
tools/nhi/build_nhi_lookup.py      清洗資料並產生靜態 HTML
tools/nhi/update_from_official.py  下載及驗證官方資料
tools/nhi/rules_source.json        官方來源網址設定
tests/test_nhi_lookup.py           資料清洗與頁面結構測試
.github/workflows/update-nhi-lookup.yml
                                   每日檢查、建置及部署 GitHub Pages
```

## 健保資料更新方式

1. GitHub Actions 每天於台灣時間約 02:17 執行。
2. 給付條文以健保署官方 Word 為主來源，檢查版本、結構、大小、SHA-256 與章節覆蓋率。
3. 官方 PDF 作為第二層完整性與日期核對。
4. 健保品項及 TFDA 未註銷許可證資料一併下載、清洗。
5. 只有全部檢查通過才重新建置並部署 `/nhi/`；可疑資料不會覆蓋既有線上版。

建置紀錄位於 `/nhi/data_version.json`。只有官方更換附件網址，或來源文件彼此無法對應時，才需要人工處理 `tools/nhi/rules_source.json`。

## 使用限制

- 本頁是核對輔助，不會判斷個別病人是否符合健保給付。
- 查詢結果保留健保署原文與官方連結，不自行推論或改寫臨床內容。
- 電子仿單依 TFDA 許可證分開列出，不推測不同廠牌與院內品項的一對一關係。
- 正式申報或臨床使用前，仍須查閱最新公告並依院內流程完成第二人核對與主管簽核。
- 靜態網頁不會上傳使用者輸入的搜尋文字。

## 1150914 離線查詢與更新工具

- `index.html` 保留使用者提供的 1150914 版 85 個品項與原有列印內容。
- `tools/update-chemo.html` 是既有一站式更新工具，新增健保給付入口，並同步更新其內嵌查詢頁模板。先下載 HTML，再於本機 Chrome／Edge 開啟；Excel 匯入、修改、Word 與 Excel 輸出仍沿用原程式。
- 新入口使用固定 HTTPS 絕對網址，本機匯出的 HTML 與 GitHub Pages 都可開啟。個別藥品以清理後學名作關鍵字，空學名則開啟查詢首頁；不自動判斷給付資格。
- 本次不新增仿單入口，也未改動原有 `/nhi/` 或 `/inserts/` 內容與更新流程。既有健保目的頁原本的 TFDA 區塊仍保留。
- `tests/test_nhi_chemo_links.py` 由既有健保 Actions 測試步驟執行，驗證 85 筆資料、產生器邏輯、模板與連結。臨床資料經正式更新時，須重新核對並更新對應的資料摘要測試。

