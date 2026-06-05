"""只開啟儀表板（不更新資料）。雙擊「看儀表板.bat」或執行：python view.py

與 update.py 不同：這支不爬蟲、不補價，只用現有的 data.json 起本機 server
並開瀏覽器。只依賴 Python 標準函式庫，所以即使沒裝 yfinance/playwright 也能跑。
"""
import webbrowser, os, socket, time, http.server
from pathlib import Path

PORT = 8765
HERE = Path(__file__).parent
URL  = f'http://localhost:{PORT}/congress_dashboard.html'


def open_browser():
    # 優先 webbrowser，失敗或無預設瀏覽器時退回 os.startfile（Windows）
    try:
        if webbrowser.open(URL):
            return
    except Exception:
        pass
    try:
        if hasattr(os, 'startfile'):
            os.startfile(URL)  # noqa
            return
    except Exception:
        pass
    print(f'無法自動開啟瀏覽器，請手動開：{URL}')


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def main():
    if not (HERE / 'data.json').exists():
        print('找不到 data.json，請先執行 python update.py 產生資料。')
        input('按 Enter 結束...')
        return

    # 已有 server 在跑 → 直接開瀏覽器連過去
    if port_in_use():
        print(f'Server 已在執行中，開啟儀表板：{URL}')
        open_browser()
        return

    os.chdir(HERE)
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None  # 不印每筆請求
    try:
        httpd = http.server.HTTPServer(('', PORT), handler)
    except OSError as e:
        print(f'無法啟動 server（{e}），仍嘗試開啟：{URL}')
        open_browser()
        return

    print(f'Dashboard: {URL}')
    print('關掉這個視窗即可停止 server。')
    time.sleep(0.5)
    open_browser()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
