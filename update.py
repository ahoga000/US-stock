"""
一鍵更新 Congress Stock Tracker 資料。
執行：python update.py

步驟：
  1. 爬 Capitol Trades /politicians → 200 位議員彙整資料
  2. 爬 Capitol Trades /trades → 2000 筆最新交易明細
  3. 合併資料 → data.json
  4. 用 yfinance 抓每筆交易當天股價 → 計算報酬率 → 寫回 data.json
"""
import asyncio, json, math, re, time, subprocess, webbrowser, sys
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf
from playwright.async_api import async_playwright

OUTPUT     = Path(__file__).parent / "data.json"
BASE_URL   = "https://www.capitoltrades.com"
TRADE_PAGES = 20   # 100 筆/頁 × 20 = 2000 筆

# ── 工具函式 ────────────────────────────────────────────────

def parse_vol(s):
    if not s: return 0
    mults = {'K': 1e3, 'M': 1e6, 'B': 1e9}
    pairs = re.findall(r'(\d+\.?\d*)\s*([KMBkmb]?)', s.replace(',', '').replace('$', ''))
    nums = [float(d) * mults.get(u.upper(), 1) for d, u in pairs if d]
    return nums[0] if nums else 0

def norm_party(s):
    s = s.lower()
    if 'republican' in s: return 'R'
    if 'democrat'   in s: return 'D'
    if 'independent' in s: return 'I'
    return 'Unknown'

def tx_kind(t):
    t = t.lower()
    if 'buy' in t or 'purchase' in t: return 'buy'
    if 'sell' in t or 'sale'    in t: return 'sell'
    return 'other'

def state_abbr(full):
    MAP = {
        'Alabama':'AL','Alaska':'AK','Arizona':'AZ','Arkansas':'AR','California':'CA',
        'Colorado':'CO','Connecticut':'CT','Delaware':'DE','Florida':'FL','Georgia':'GA',
        'Hawaii':'HI','Idaho':'ID','Illinois':'IL','Indiana':'IN','Iowa':'IA',
        'Kansas':'KS','Kentucky':'KY','Louisiana':'LA','Maine':'ME','Maryland':'MD',
        'Massachusetts':'MA','Michigan':'MI','Minnesota':'MN','Mississippi':'MS',
        'Missouri':'MO','Montana':'MT','Nebraska':'NE','Nevada':'NV','New Hampshire':'NH',
        'New Jersey':'NJ','New Mexico':'NM','New York':'NY','North Carolina':'NC',
        'North Dakota':'ND','Ohio':'OH','Oklahoma':'OK','Oregon':'OR','Pennsylvania':'PA',
        'Rhode Island':'RI','South Carolina':'SC','South Dakota':'SD','Tennessee':'TN',
        'Texas':'TX','Utah':'UT','Vermont':'VT','Virginia':'VA','Washington':'WA',
        'West Virginia':'WV','Wisconsin':'WI','Wyoming':'WY',
        'District of Columbia':'DC','Puerto Rico':'PR',
    }
    return MAP.get(full, full[:2].upper() if full else '')

def parse_date(s):
    s = re.sub(r'\s+', ' ', (s or '').strip())
    for fmt in ['%d %b %Y', '%d %B %Y']:
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    return None

# ── Step 1 & 2：Playwright 爬蟲 ────────────────────────────

async def scrape_politicians(page):
    members = {}
    for p in range(1, 5):
        url = f'{BASE_URL}/politicians?page={p}&pageSize=100'
        print(f'  Politicians page {p}...', end=' ', flush=True)
        await page.goto(url, wait_until='networkidle', timeout=35000)
        links = await page.query_selector_all('a[href*="/politicians/"]')
        if not links:
            print('done')
            break
        print(f'{len(links)} politicians')
        for lnk in links:
            txt  = await lnk.inner_text()
            href = await lnk.get_attribute('href') or ''
            lines = [l.strip() for l in txt.split('\n') if l.strip()]
            if not lines: continue
            name = lines[0]
            ps   = lines[1] if len(lines) > 1 else ''
            party = norm_party(ps)
            full_state = re.sub(r'^(Republican|Democrat|Independent)', '', ps).strip()
            stats, i = {}, 2
            while i < len(lines) - 1:
                stats[lines[i].lower()] = lines[i + 1]; i += 2
            members[name] = {
                'name': name, 'party': party, 'chamber': 'unknown',
                'state': state_abbr(full_state),
                'vol': parse_vol(stats.get('volume', '0')),
                'trade_count': int(re.sub(r'\D', '', stats.get('trades', '0')) or 0),
                'buy': 0, 'sell': 0, 'other': 0,
                'stocks': {}, 'recent': [],
            }
        if len(links) < 100:
            break
    return members

async def scrape_trades(page, max_pages):
    all_trades = []
    for p in range(1, max_pages + 1):
        url = f'{BASE_URL}/trades?page={p}&pageSize=100'
        print(f'  Trades page {p}...', end=' ', flush=True)
        try:
            await page.goto(url, wait_until='networkidle', timeout=35000)
        except Exception as e:
            print(f'ERROR: {e}'); break
        rows = await page.query_selector_all('table tbody tr')
        if not rows:
            print('no rows, done'); break
        page_trades = []
        for row in rows:
            cells = await row.query_selector_all('td')
            if len(cells) < 7: continue
            texts = [await c.inner_text() for c in cells]
            pol_lines = [l.strip() for l in texts[0].split('\n') if l.strip()]
            name      = pol_lines[0] if pol_lines else ''
            pol_extra = ' '.join(pol_lines[1:])
            chamber   = 'house' if 'House' in pol_extra else ('senate' if 'Senate' in pol_extra else 'unknown')
            party     = norm_party(pol_extra)
            sm        = re.search(r'\b([A-Z]{2})\b', pol_extra)
            state     = sm.group(1) if sm else ''
            il        = [l.strip() for l in texts[1].split('\n') if l.strip()]
            asset     = il[0] if il else ''
            tr        = il[1] if len(il) > 1 else ''
            ticker    = (tr.split(':')[0].strip().upper() if ':' in tr else tr.strip().upper())
            if ticker in ('N/A', '--', ''): ticker = ''
            if not name or len(name) < 2: continue
            page_trades.append({
                'name': name, 'chamber': chamber, 'party': party, 'state': state,
                'asset': asset, 'ticker': ticker,
                'date': texts[3].strip().replace('\n', ' '),
                'type': texts[6].strip() if len(texts) > 6 else '',
                'amount': texts[7].strip() if len(texts) > 7 else '',
            })
        print(f'{len(page_trades)} rows')
        all_trades.extend(page_trades)
        has_next = False
        for sel in ['[aria-label="Next page"]:not([aria-disabled="true"])',
                    '[aria-label="Go to next page"]:not([aria-disabled="true"])']:
            if await page.query_selector(sel):
                has_next = True; break
        if not has_next:
            print('  Last page'); break
    return all_trades

async def run_scraper():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx  = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        page = await ctx.new_page()
        print('=== Step 1: Politicians ===')
        members = await scrape_politicians(page)
        print(f'Found: {len(members)}\n')
        print(f'=== Step 2: Trades ({TRADE_PAGES} pages) ===')
        trades = await scrape_trades(page, TRADE_PAGES)
        print(f'Found: {len(trades)}\n')
        await browser.close()
    return members, trades

# ── Step 3：合併 ─────────────────────────────────────────────

def merge(members, trades):
    print('=== Step 3: Merging ===')
    for tx in trades:
        name = tx['name']
        if name not in members:
            members[name] = {
                'name': name, 'party': tx['party'], 'chamber': tx['chamber'],
                'state': tx['state'], 'vol': 0, 'trade_count': 0,
                'buy': 0, 'sell': 0, 'other': 0, 'stocks': {}, 'recent': [],
            }
        m = members[name]
        if m['chamber'] == 'unknown' and tx['chamber'] != 'unknown': m['chamber'] = tx['chamber']
        if m['party']   == 'Unknown' and tx['party']   != 'Unknown': m['party']   = tx['party']
        if not m['state'] and tx['state']: m['state'] = tx['state']
        kind = tx_kind(tx['type'])
        if kind == 'buy':    m['buy']   += 1
        elif kind == 'sell': m['sell']  += 1
        else:                m['other'] += 1
        tk = tx.get('ticker', '').upper()
        if tk and re.match(r'^[A-Z]{1,7}$', tk):
            m['stocks'][tk] = m['stocks'].get(tk, 0) + 1
        if len(m['recent']) < 25:
            m['recent'].append(tx)
    result = list(members.values())
    for m in result:
        m.pop('trade_count', None)
    print(f'Total politicians: {len(result)}')
    return result

# ── Step 4：股價與報酬率 ─────────────────────────────────────

def enrich_prices(members_list):
    print('\n=== Step 4: Stock prices & returns ===')
    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    trade_map = {}
    for mi, m in enumerate(members_list):
        for ti, tx in enumerate(m.get('recent', [])):
            tk = tx.get('ticker', '').strip().upper()
            if not tk or not re.match(r'^[A-Z]{1,7}$', tk): continue
            d = parse_date(tx.get('date', ''))
            if not d or d >= today: continue
            trade_map.setdefault(tk, []).append((mi, ti, d))
    total = len(trade_map)
    print(f'Unique tickers: {total}\n')
    enriched = 0
    for idx, (ticker, entries) in enumerate(sorted(trade_map.items()), 1):
        min_date = min(e[2] for e in entries)
        start = (min_date - timedelta(days=7)).strftime('%Y-%m-%d')
        end   = (today + timedelta(days=2)).strftime('%Y-%m-%d')
        print(f'  [{idx}/{total}] {ticker}...', end=' ', flush=True)
        try:
            hist = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if hist.empty:
                print('no data'); continue
            if hasattr(hist.columns, 'levels'):
                hist.columns = hist.columns.get_level_values(0)
            close = hist['Close']
            current = float(close.iloc[-1])
            if math.isnan(current):
                print('no current price'); continue
            for mi, ti, trade_date in entries:
                future = close[close.index >= trade_date.strftime('%Y-%m-%d')]
                if future.empty: continue
                tp = float(future.iloc[0])
                if math.isnan(tp) or tp <= 0: continue
                tx_obj = members_list[mi]['recent'][ti]
                tx_obj.update({
                    'trade_price':   round(tp, 2),
                    'current_price': round(current, 2),
                    'return_pct':    round((current - tp) / tp * 100, 2),
                    'hold_days':     (today - trade_date).days,
                })
                enriched += 1
                # 買進後 3 個月內峰值漲幅 > 30% → 標記爆漲（僅 BUY）
                if tx_kind(tx_obj.get('type', '')) == 'buy':
                    win_end = (trade_date + timedelta(days=90)).strftime('%Y-%m-%d')
                    window = future[future.index <= win_end]
                    if not window.empty:
                        peak = float(window.max())
                        if not math.isnan(peak) and peak > 0:
                            spct = round((peak - tp) / tp * 100, 2)
                            pdate = window.idxmax()
                            tx_obj['spike3m_pct']  = spct
                            tx_obj['spike3m_days'] = (pdate.to_pydatetime() - trade_date).days
                            tx_obj['spike3m']      = spct > 30
            print(f'${current:.2f}')
        except Exception as e:
            print(f'ERROR: {e}')
        if idx % 10 == 0:
            time.sleep(1)
    print(f'\nEnriched {enriched} trades')
    return members_list

# ── 主程式 ───────────────────────────────────────────────────

def main():
    t0 = time.time()
    members_raw, trades = asyncio.run(run_scraper())
    members_list = merge(members_raw, trades)
    members_list = enrich_prices(members_list)
    out = {
        'members':           members_list,
        'total_trades':      len(trades),
        'fetched_at':        datetime.now().isoformat(),
        'prices_updated_at': datetime.now().isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    elapsed = int(time.time() - t0)
    print(f'\nDone in {elapsed}s -> {OUTPUT}')

    port = 8765
    folder = OUTPUT.parent
    url = f'http://localhost:{port}/congress_dashboard.html'

    # Open browser first (non-blocking), then run server in foreground
    time.sleep(1)
    webbrowser.open(url)
    print(f'\nDashboard: {url}')
    print('Press Ctrl+C to stop the server.\n')

    import http.server, os
    os.chdir(folder)
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None  # silence request logs
    with http.server.HTTPServer(('', port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except Exception:
        import traceback
        traceback.print_exc()
        input('\nPress Enter to exit...')
