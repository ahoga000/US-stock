"""
Fetches Congressional trading data from Capitol Trades using Playwright.
Strategy:
  1. Scrape /politicians (all 200) for aggregate stats (volume, trade count, etc.)
  2. Scrape /trades (recent pages) for transaction-level detail to populate the detail panel
Run: python fetch_congress.py
"""
import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT = Path(__file__).parent / "data.json"
BASE_URL = "https://www.capitoltrades.com"
TRADE_PAGES = 20   # 20 pages x 100 = 2000 recent trades for detail panel

def parse_vol(s):
    if not s: return 0
    mults = {'K': 1e3, 'M': 1e6, 'B': 1e9}
    pairs = re.findall(r'(\d+\.?\d*)\s*([KMBkmb]?)', s.replace(',','').replace('$',''))
    nums = [float(d) * mults.get(u.upper(), 1) for d, u in pairs if d]
    return nums[0] if nums else 0

def norm_party(s):
    s = s.lower()
    if 'republican' in s: return 'R'
    if 'democrat' in s:   return 'D'
    if 'independent' in s: return 'I'
    return 'Unknown'

def tx_kind(t):
    t = t.lower()
    if 'buy' in t or 'purchase' in t: return 'buy'
    if 'sell' in t or 'sale' in t:    return 'sell'
    return 'other'

def extract_state_abbr(full_state):
    STATE_MAP = {
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
    return STATE_MAP.get(full_state, full_state[:2].upper() if full_state else '')

async def scrape_politicians(page):
    members = {}
    for p in range(1, 5):
        url = f'{BASE_URL}/politicians?page={p}&pageSize=100'
        print(f'  Politicians page {p}...', end=' ', flush=True)
        await page.goto(url, wait_until='networkidle', timeout=35000)
        links = await page.query_selector_all('a[href*="/politicians/"]')
        if not links:
            print('no items, done')
            break
        print(f'{len(links)} politicians')

        for lnk in links:
            txt = await lnk.inner_text()
            href = await lnk.get_attribute('href') or ''
            lines = [l.strip() for l in txt.split('\n') if l.strip()]
            if not lines: continue

            name = lines[0]
            # Second line: party + full state (e.g. "RepublicanPennsylvania")
            party_state = lines[1] if len(lines) > 1 else ''
            party = norm_party(party_state)
            # Extract state name: everything after party word
            state_full = re.sub(r'^(Republican|Democrat|Independent)', '', party_state).strip()
            state = extract_state_abbr(state_full)

            # Parse stats from remaining lines: key\nvalue pairs
            stats = {}
            i = 2
            while i < len(lines) - 1:
                key = lines[i].lower()
                val = lines[i+1]
                stats[key] = val
                i += 2

            trade_count = int(re.sub(r'\D', '', stats.get('trades', '0')) or 0)
            vol = parse_vol(stats.get('volume', '0'))

            # Chamber: not shown on politicians page, will be inferred from trades
            members[name] = {
                'name': name,
                'party': party,
                'chamber': 'unknown',
                'state': state,
                'pol_id': href.split('/')[-1] if href else '',
                'vol': vol,
                'buy': 0, 'sell': 0, 'other': 0,
                'trade_count': trade_count,
                'stocks': {},
                'recent': [],
            }

        if len(links) < 100:
            break  # last page

    return members

async def scrape_trades(page, max_pages):
    all_trades = []
    for p in range(1, max_pages + 1):
        url = f'{BASE_URL}/trades?page={p}&pageSize=100'
        print(f'  Trades page {p}...', end=' ', flush=True)
        try:
            await page.goto(url, wait_until='networkidle', timeout=35000)
        except Exception as e:
            print(f'ERROR: {e}')
            break

        rows = await page.query_selector_all('table tbody tr')
        if not rows:
            print('no rows, done')
            break

        page_trades = []
        for row in rows:
            cells = await row.query_selector_all('td')
            if len(cells) < 7: continue
            texts = [await c.inner_text() for c in cells]

            pol_lines = [l.strip() for l in texts[0].split('\n') if l.strip()]
            name = pol_lines[0] if pol_lines else ''
            pol_extra = ' '.join(pol_lines[1:])

            chamber = 'unknown'
            if 'House' in pol_extra: chamber = 'house'
            elif 'Senate' in pol_extra: chamber = 'senate'

            party = norm_party(pol_extra)

            state = ''
            m = re.search(r'\b([A-Z]{2})\b', pol_extra)
            if m: state = m.group(1)

            issuer_lines = [l.strip() for l in texts[1].split('\n') if l.strip()]
            asset = issuer_lines[0] if issuer_lines else ''
            ticker_raw = issuer_lines[1] if len(issuer_lines) > 1 else ''
            ticker = ticker_raw.split(':')[0].strip().upper() if ':' in ticker_raw else ticker_raw.strip().upper()
            if ticker in ('N/A', '--', ''): ticker = ''

            traded_date = texts[3].strip().replace('\n', ' ')
            tx_type = texts[6].strip() if len(texts) > 6 else ''
            amount = texts[7].strip() if len(texts) > 7 else ''

            if not name or len(name) < 2: continue
            page_trades.append({
                'name': name, 'chamber': chamber, 'party': party, 'state': state,
                'asset': asset, 'ticker': ticker,
                'date': traded_date, 'type': tx_type, 'amount': amount,
            })

        print(f'{len(page_trades)} rows')
        all_trades.extend(page_trades)

        # Check next page
        has_next = False
        for sel in ['[aria-label="Next page"]:not([aria-disabled="true"])',
                    '[aria-label="Go to next page"]:not([aria-disabled="true"])']:
            el = await page.query_selector(sel)
            if el:
                has_next = True
                break
        if not has_next:
            print('  Last page')
            break

    return all_trades

async def main():
    print('Starting Capitol Trades scraper...\n')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            locale='en-US',
        )
        page = await ctx.new_page()

        print('=== Step 1: Scraping politicians (all 200) ===')
        members = await scrape_politicians(page)
        print(f'Total politicians found: {len(members)}\n')

        print(f'=== Step 2: Scraping recent trades ({TRADE_PAGES} pages) ===')
        trades = await scrape_trades(page, TRADE_PAGES)
        print(f'Total recent trades: {len(trades)}\n')

        await browser.close()

    print('=== Step 3: Merging trade detail into politicians ===')
    # Enrich members with trade type stats and recent transactions
    for tx in trades:
        name = tx['name']
        # Create member entry if not already from politicians page
        if name not in members:
            members[name] = {
                'name': name, 'party': tx['party'], 'chamber': tx['chamber'],
                'state': tx['state'], 'pol_id': '',
                'vol': 0, 'buy': 0, 'sell': 0, 'other': 0,
                'trade_count': 0, 'stocks': {}, 'recent': [],
            }
        m = members[name]
        # Fill in chamber/party if unknown
        if m['chamber'] == 'unknown' and tx['chamber'] != 'unknown':
            m['chamber'] = tx['chamber']
        if m['party'] == 'Unknown' and tx['party'] != 'Unknown':
            m['party'] = tx['party']
        if not m['state'] and tx['state']:
            m['state'] = tx['state']

        kind = tx_kind(tx['type'])
        if kind == 'buy':    m['buy'] += 1
        elif kind == 'sell': m['sell'] += 1
        else:                m['other'] += 1

        tk = tx.get('ticker', '').upper()
        if tk and re.match(r'^[A-Z]{1,7}$', tk):
            m['stocks'][tk] = m['stocks'].get(tk, 0) + 1

        if len(m['recent']) < 25:
            m['recent'].append(tx)

    members_list = list(members.values())
    # Use trade_count from politicians page if available (more accurate), else use merged buy+sell+other
    for m in members_list:
        if m.get('trade_count', 0) > 0 and (m['buy'] + m['sell'] + m['other']) == 0:
            # no trades scraped for this politician, use trade_count as total estimate
            pass
        del m['trade_count']
        del m['pol_id']

    print(f'Final politician count: {len(members_list)}')

    out = {
        'members': members_list,
        'total_trades': len(trades),
        'fetched_at': __import__('datetime').datetime.now().isoformat(),
    }
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nSaved → {OUTPUT}')

if __name__ == '__main__':
    asyncio.run(main())
