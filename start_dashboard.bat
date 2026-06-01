@echo off
echo Starting Congress Dashboard...
echo.
echo Data last fetched:
type data.json | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('fetched_at','unknown'))" 2>nul
echo.
echo To refresh data, run: python update.py
echo.
start http://localhost:8765/congress_dashboard.html
python -m http.server 8765
