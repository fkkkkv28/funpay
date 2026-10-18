import urllib.request
import urllib.parse
import http.cookiejar
import re
import json
import html
import time
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
GOLDEN_KEY = os.environ.get("FUNPAY_GOLDEN_KEY", "")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
LOG_FILE = "funpay_bot.log"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

class FunPayAutoBot:
    def __init__(self, golden_key):
        self.golden_key = golden_key
        self.cj = http.cookiejar.CookieJar()
        ck = http.cookiejar.Cookie(
            0, "golden_key", golden_key, None, False,
            ".funpay.com", True, True, "/", True, True, None, False, None, None, {}
        )
        self.cj.set_cookie(ck)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.user_id = None
        self.csrf_token = None

    def _request(self, url, data=None, extra_headers=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if extra_headers:
            headers.update(extra_headers)

        if data is not None and isinstance(data, str):
            data = data.encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers)
        resp = self.opener.open(req, timeout=20)
        return resp.read().decode("utf-8", "ignore")

    def init_session(self):
        page = self._request("https://funpay.com/")
        m = re.search(r'data-app-data="([^"]+)"', page)
        if not m:
            raise RuntimeError("Не удалось получить data-app-data. Проверьте golden_key.")
        app_data = json.loads(html.unescape(m.group(1)))
        self.user_id = app_data.get("userId")
        self.csrf_token = app_data.get("csrf-token")
        if not self.user_id:
            raise RuntimeError("Не удалось авторизоваться по golden_key (userId пуст).")
        log(f"Авторизован как ID {self.user_id} (AEGIS222) | Вечный Онлайн + Автоподнятие активны!")

    def ping_online(self):
        try:
            self._request("https://funpay.com/")
        except Exception as e:
            log(f"Ошибка пинга онлайна: {e}")

    def get_trade_categories(self):
        u_page = self._request(f"https://funpay.com/users/{self.user_id}/")
        links = re.findall(r'href="(https://funpay\.com/lots/(\d+)/trade)"', u_page)
        seen = set()
        result = []
        for full_url, node_id in links:
            if node_id not in seen:
                seen.add(node_id)
                result.append((node_id, full_url))
        return result

    def raise_category(self, node_id, trade_url):
        t_page = self._request(trade_url)
        title_m = re.search(r"<title>([^<]+)</title>", t_page)
        cat_title = title_m.group(1).split("/")[0].strip() if title_m else f"Раздел {node_id}"

        btn = re.search(r'data-game="(\d+)"[^>]*data-node="(\d+)"', t_page)
        if not btn:
            log(f"[{cat_title}] Кнопка поднятия не найдена.")
            return

        game_id, real_node_id = btn.group(1), btn.group(2)

        def send_raise(extra_node_ids=None):
            params = [("game_id", game_id), ("node_id", real_node_id)]
            if extra_node_ids:
                for nid in extra_node_ids:
                    params.append(("node_ids[]", nid))
            payload = urllib.parse.urlencode(params)
            raw = self._request(
                "https://funpay.com/lots/raise",
                data=payload,
                extra_headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": "https://funpay.com",
                    "Referer": trade_url
                }
            )
            return json.loads(raw)

        res = send_raise()
        if res.get("modal"):
            modal_html = res.get("modal", "")
            checkbox_ids = re.findall(r'name="node_ids\[\]"[^>]*value="(\d+)"', modal_html)
            if not checkbox_ids:
                checkbox_ids = re.findall(r'value="(\d+)"[^>]*name="node_ids\[\]"', modal_html)
            if checkbox_ids:
                res = send_raise(extra_node_ids=checkbox_ids)

        msg = res.get("msg") or res.get("error") or str(res)
        msg_clean = re.sub(r"<[^>]+>", "", str(msg)).strip()
        wait_sec = res.get("wait")
        if wait_sec:
            mins = int(wait_sec) // 60
            log(f"[{cat_title}] -> {msg_clean} (осталось ~{mins} мин.)")
        else:
            log(f"[{cat_title}] -> {msg_clean}")

    def run_once(self):
        self.init_session()
        cats = self.get_trade_categories()
        log(f"Найдено категорий для автоподнятия: {len(cats)}")
        for node_id, url in cats:
            try:
                self.raise_category(node_id, url)
                time.sleep(1.2)
            except Exception as e:
                log(f"Ошибка при поднятии {url}: {e}")

    def run_forever(self):
        self.init_session()
        last_raise = 0
        last_ping = 0
        RAISE_INTERVAL = 15 * 60   # Проверяем поднятие каждые 15 минут
        PING_INTERVAL = 3 * 60     # Поддерживаем онлайн каждые 3 минуты

        while True:
            now = time.time()
            if now - last_ping >= PING_INTERVAL:
                self.ping_online()
                last_ping = now

            if now - last_raise >= RAISE_INTERVAL:
                try:
                    cats = self.get_trade_categories()
                    log(f"--- Автоподнятие ({len(cats)} разделов) ---")
                    for node_id, url in cats:
                        try:
                            self.raise_category(node_id, url)
                            time.sleep(1.2)
                        except Exception as e:
                            log(f"Ошибка поднятия раздела {node_id}: {e}")
                except Exception as e:
                    log(f"Ошибка получения списка разделов: {e}")
                    try:
                        self.init_session()
                    except Exception:
                        pass
                last_raise = now

            time.sleep(15)

if __name__ == "__main__":
    bot = FunPayAutoBot(GOLDEN_KEY)
    if "--once" in sys.argv:
        bot.run_once()
    else:
        bot.run_forever()
