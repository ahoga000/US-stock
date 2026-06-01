"""
Enriches data.json with stock performance data using yfinance.
For each trade with a valid ticker, fetches:
  - Price on trade date (first available close on/after that date)
  - Current price (latest close)
  - Return % since trade date
  - Holding days

Run AFTER fetch_congress.py:
  python fetch_prices.py
"""
import json, re, time
from pathlib import Path
from datetime import datetime, timedelta
import yfinance as yf

DATA = Path(__file__).parent / "data.json"

def parse_date(s):
    s = re.sub(r'\s+', ' ', (s or '').strip())
    for fmt in ['%d %b %Y', '%d %B %Y']:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None

def main():
    data = json.loads(DATA.read_text(encoding='utf-8'))
    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Collect all (ticker -> [(member_idx, tx_idx, date)]) ---
    trade_map = {}
    for mi, member in enumerate(data['members']):
        for ti, tx in enumerate(member.get('recent', [])):
            ticker = tx.get('ticker', '').strip().upper()
            if not ticker or not re.match(r'^[A-Z]{1,7}$', ticker):
                continue
            d = parse_date(tx.get('date', ''))
            if not d or d >= today:
                continue
            trade_map.setdefault(ticker, []).append((mi, ti, d))

    total_tickers = len(trade_map)
    print(f"Unique tickers to price: {total_tickers}\n")

    enriched = 0
    for idx, (ticker, entries) in enumerate(sorted(trade_map.items()), 1):
        min_date = min(e[2] for e in entries)
        start = (min_date - timedelta(days=7)).strftime('%Y-%m-%d')
        end   = (today + timedelta(days=2)).strftime('%Y-%m-%d')

        print(f"[{idx}/{total_tickers}] {ticker}...", end=' ', flush=True)
        try:
            hist = yf.download(ticker, start=start, end=end,
                               progress=False, auto_adjust=True)
            if hist.empty:
                print("no data")
                continue

            # Flatten MultiIndex columns if present
            if isinstance(hist.columns, type(hist.columns)) and hasattr(hist.columns, 'levels'):
                hist.columns = hist.columns.get_level_values(0)

            close = hist['Close']
            current_price = float(close.iloc[-1])
            current_date  = close.index[-1].date()

            for mi, ti, trade_date in entries:
                trade_str = trade_date.strftime('%Y-%m-%d')
                # Find first trading day on or after trade date
                future = close[close.index >= trade_str]
                if future.empty:
                    continue
                trade_price = float(future.iloc[0])
                if trade_price <= 0:
                    continue

                ret  = (current_price - trade_price) / trade_price * 100
                days = (today - trade_date).days

                data['members'][mi]['recent'][ti].update({
                    'trade_price':   round(trade_price, 2),
                    'current_price': round(current_price, 2),
                    'return_pct':    round(ret, 2),
                    'hold_days':     days,
                })
                enriched += 1

            print(f"${current_price:.2f}  ({len(entries)} trades)")
        except Exception as e:
            print(f"ERROR: {e}")

        # Polite delay every 10 tickers
        if idx % 10 == 0:
            time.sleep(1)

    data['prices_updated_at'] = datetime.now().isoformat()
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nEnriched {enriched} trades across {total_tickers} tickers → {DATA}")

if __name__ == '__main__':
    main()
