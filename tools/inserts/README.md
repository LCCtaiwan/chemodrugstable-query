# 電子仿單資料試行版

每週日台灣時間 02:43，從 TFDA 官方 InfoId 39 取得清冊，固定追蹤最多 100 個「藥證＋URL」文件。這是電子 PDF 收錄試行，不是全國完整仿單庫，也尚未接入結構化仿單 HTML 正文介面。

## 產出

- `data/inserts/documents.json`：品名、許可證、來源、PDF 檔案 SHA-256、每頁原文、最後檢查時間與文字更新時間。
- `data/inserts/last_run.json`：每份下載與解析的狀態，包括 HTTP 錯誤。
- `data/inserts/failure.json`：整批失敗原因。此檔可能是歷史紀錄，請看 checked_at。

無 OCR、不推論章節或臨床意義。每頁至少 50 個非空白字元且低替代字元比例才納入；這只是文字層篩選，仍須人工確認抽取品質。混合掃描或稀疏文字暫不收錄。頁碼是 PDF 實際頁序。

每次下載既有 URL 的內容並比較 Hash；網址未變也會檢查。個別失敗保留舊原文並標記 error；新版本無可用文字時清除舊原文避免當成新版。全部失敗不覆寫 documents.json；catalog 缺漏不視為藥證註銷。試行清單退出或更換 URL 只影響收錄範圍，不代表停藥或下架。

## GitHub Actions

PR 會跑單元測試及 100 份下載測試，結果在 workflow 的 `electronic-insert-results` artifact。下載全失敗會顯示失敗，不能當成已驗證上線。

合併 main 後才啟用每週排程；可由 Actions → Electronic insert pilot → Run workflow 手動啟動。成功的 main 執行將 JSON commit 回 repo，提供持久版本紀錄；artifact 只保留 30 日，不作主資料庫。若分支保護禁止 bot 寫入，push 會失敗，該次結果仍保留在 artifact，需改採資料 PR 流程。

此階段不修改健保頁或 Pages 部署。先驗證下載與更新後，再將可用原文接到搜尋頁。原始 PDF 不寫進 Git。

## 本機

```sh
python3 -m pip install -r tools/inserts/requirements.txt
python3 -m unittest discover -s tests -p test_inserts.py
python3 tools/inserts/update.py --limit 100
```

官方來源：https://data.gov.tw/dataset/9117
