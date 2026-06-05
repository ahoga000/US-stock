# 買進後爆漲標記（Spike Flag）設計

**日期**：2026-06-05
**範圍**：`update.py`（新增計算與欄位）+ `congress_dashboard.html`（標記顯示 + 新分頁）。
**前置**：建立在既有 leaderboards 之上，見 `2026-06-05-congress-dashboard-leaderboards-design.md`。

## 1. 背景與目標

加上買入日期後，肉眼可見不少「買完很快就大漲」的交易。本功能把這類**時機可疑**的買進自動標記出來，集中成可掃描的清單，輔助使用者判斷哪些議員/交易值得深究。

**這不是法律指控**：標記只代表「買進後短期內股價大漲」，是時機訊號，不等於內線交易。

## 2. 定義

對每一筆**已補價的 BUY 交易**：

- 基準價 = 該筆 `trade_price`（買進日當天/之後第一個收盤價，已存在）
- 觀察窗 = 買進日 → 買進日 + 90 天（日曆日）；若窗尾超過今天，則只算到今天（近期買進用部分窗）
- 峰值價 = 觀察窗內的**最高收盤價**
- **峰值漲幅%** = (峰值價 − 基準價) / 基準價 × 100
- 若 **峰值漲幅 > 30%** → 標記為「爆漲」🚩
- 另記：到頂天數 = 買進日到峰值出現日的天數

> 用「窗內最高價」而非「滿 90 天當天價」：「三個月內就大漲」指中途衝上去，不是剛好第 90 天的價格。

僅對 BUY 計算（語意為「買進後」）。SELL 不適用。

## 3. 資料層改動（`update.py`）

在 `enrich_prices` 既有的逐筆迴圈內計算（**不需額外下載**，沿用已抓的該檔 `close` 序列）。

每筆 BUY 交易新增三個欄位寫入 `data.json`：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `spike3m_pct` | float | 90 天窗內峰值漲幅 % |
| `spike3m_days` | int | 買進到峰值的天數 |
| `spike3m` | bool | `spike3m_pct > 30` |

計算邏輯（接在現有 `trade_price/current_price/return_pct/hold_days` 更新之後）：

```python
win_end = (trade_date + timedelta(days=90)).strftime('%Y-%m-%d')
window = future[future.index <= win_end]   # future 已是 close[index >= trade_date]
if not window.empty:
    peak = float(window.max())
    if not math.isnan(peak) and peak > 0:
        spct = round((peak - tp) / tp * 100, 2)
        pdate = window.idxmax()
        members_list[mi]['recent'][ti].update({
            'spike3m_pct':  spct,
            'spike3m_days': (pdate.to_pydatetime() - trade_date).days,
            'spike3m':      spct > 30,
        })
```

僅在 `tx_kind` 為 buy 時計算（讀取該筆 `type` 判斷；或在迴圈外先過濾）。

**需重跑一次 `python update.py`，`data.json` 才會有這些欄位。**

## 4. 儀表板改動（`congress_dashboard.html`）

### 4.1 新分頁「🚩 可疑交易」

Tab 列新增一頁（置於人物榜與議員明細之間）：

```
📊 股票績效 | 🔥 熱門買賣 | 🏆 人物榜 | 🚩 可疑交易 | 👤 議員明細
```

內容 = 跨議員的扁平清單，列出**所有 `spike3m === true` 的 BUY 交易**：

- 欄位：排名 ｜ 議員 ｜ Ticker ｜ 買入日期 ｜ 峰值漲幅%（`spike3m_pct`）｜ 到頂天數 ｜ 金額 ｜ 到今天報酬%
- 預設排序：峰值漲幅 desc（漲最兇的在最上）
- 表頭可點切換排序（峰值漲幅 / 到頂天數 / 到今天報酬）
- 頂部一行摘要：「共 N 筆買進在 3 個月內漲破 30%，涉及 M 位議員」
- 點議員 → 跳到議員明細並展開（沿用 `openMember`）

### 4.2 既有分頁的 inline 標記

- **股票績效榜**：每檔列尾（ticker 旁）顯示 `🚩×N`（N = 該檔有幾筆爆漲買進，0 則不顯示）。
- **股票績效榜 / 熱門買賣榜的展開明細**：被標記的買進列前面加 🚩，hover 顯示「3個月內最高 +XX%，第N天」。

### 4.3 聚合（`buildAgg`）

- 股票每檔 `e.spikeCount` = 該 ticker 中 `spike3m===true` 的 BUY 筆數。
- 另建一份全域 `S.spikeTrades` = 所有 `spike3m===true` 的 BUY（含議員名、ticker、日期、spike3m_pct、spike3m_days、amount、return_pct），供可疑交易分頁用。

## 5. 邊界情況

- **舊資料無欄位**：`spike3m` 為 undefined → 視為未標記，inline 不顯示；可疑交易分頁顯示空狀態提示「請重新執行 `python update.py` 以產生標記」。
- **近期買進（不足 90 天）**：觀察窗截到今天，用部分窗計算；時間未到不會誤標。
- **峰值價 NaN／基準價 ≤0**：跳過，不寫欄位（沿用既有 NaN 防護）。
- 30% 為硬編定義（依使用者指定），v1 不做成可調門檻。

## 6. 非目標（YAGNI）

- 不做大盤基準調整（alpha）；純絕對漲幅。
- 不對 SELL 標記。
- 門檻 30%、窗 90 天暫不開放 UI 調整。
- 不串接事件/財報/委員會資料（未來可加的下一層）。

## 7. 驗證

1. 改 `update.py` 後重跑，確認 `data.json` 內 BUY 交易出現 `spike3m*` 欄位，且抽樣手算一筆峰值漲幅核對。
2. Playwright 載入：可疑交易分頁有列、排序可切、點議員能跳轉；股票榜 inline 🚩 出現；0 console 錯誤。
3. 抽一筆已知爆漲交易（如 MRVL Byron Donalds）確認有被標記且 spike3m_pct 合理。
