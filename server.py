import urllib.request
import urllib.parse
import http.cookiejar
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading
import re
import json
import html
import time
import os
import sys
import copy
import webbrowser
from datetime import datetime, timezone, timedelta

MSK_TZ = timezone(timedelta(hours=3))

def now_msk():
    return datetime.now(MSK_TZ)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "app_data.json")
LOG_FILE = os.path.join(BASE_DIR, "funpay_bot.log")
HTML_FILE = os.path.join(BASE_DIR, "index.html")
PORT = int(os.environ.get("PORT", 8765))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

DEFAULT_DATA = {
    "golden_key": os.environ.get("FUNPAY_GOLDEN_KEY", ""),
    "telegram": {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "notify_new_order": True,
        "notify_order_closed": True,
        "notify_bumps": True
    },
    "settings": {
        "auto_bump": True,
        "eternal_online": True,
        "sales_mode": "since_sept",
        "raise_interval_min": 15,
        "order_check_sec": 45
    },
    "transactions": [
        {
            "id": "lzt_genshin_3",
            "type": "expense",
            "amount": 90.0,
            "unit_cost": 30.0,
            "qty": 3,
            "category": "Genshin Impact",
            "note": "3 акка 21 AR Европа по 30 ₽ (Mayo: #264267494, #264265948, #264265958)",
            "date": "18.10.2026 00:00"
        },
        {
            "id": "lzt_steam_offline_1",
            "type": "expense",
            "amount": 327.0,
            "unit_cost": 0.0,
            "qty": 1,
            "category": "Steam Оффлайн (10 игр)",
            "note": "Вечный аккаунт с 10 играми: BeamNG, Forza 4, Raft, GMod, Terraria и др. (#265794666)",
            "date": "17.10.2026 23:26"
        },
        {
            "id": "lzt_steam_kzt_28",
            "type": "expense",
            "amount": 56.0,
            "unit_cost": 2.0,
            "qty": 28,
            "category": "Steam Казахстан",
            "note": "28 авторегов Steam Казахстан с родной почтой по 2 ₽ (Император)",
            "date": "17.10.2026 22:24"
        },
        {
            "id": "lzt_genshin_2",
            "type": "expense",
            "amount": 30.0,
            "unit_cost": 30.0,
            "qty": 1,
            "category": "Genshin Impact",
            "note": "1 акк 21 AR Европа за 30 ₽ (Mayo: #263319805)",
            "date": "17.10.2026 21:50"
        },
        {
            "id": "lzt_genshin_1",
            "type": "expense",
            "amount": 90.0,
            "unit_cost": 30.0,
            "qty": 3,
            "category": "Genshin Impact",
            "note": "3 акка 21 AR Европа по 30 ₽ (Mayo: 1-я партия от 6 окт)",
            "date": "06.10.2026"
        }
    ],
    "known_orders": {},
    "cached_orders": [],
    "cached_balance": "279 ₽"
}


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = self.load_data()
        self.logs = []
        self.load_recent_logs()

        # Live runtime state (pre-populated from cache so UI never shows 0 on startup)
        self.user_id = "2941524"
        self.username = "AEGIS222"
        self.balance = self.data.get("cached_balance", "279 ₽")
        self.balance_num = 279.0
        self.online_status = True
        self.last_ping_ts = time.time()
        self.last_raise_ts = 0
        self.last_orders_ts = 0

        self.categories = []
        self.active_lots = []
        self.orders = self.data.get("cached_orders", [])
        self.tg_last_update_id = 0

        self._init_cookie_opener()

    def _init_cookie_opener(self):
        self.cj = http.cookiejar.CookieJar()
        gk = self.data.get("golden_key", "").strip()
        if gk:
            ck = http.cookiejar.Cookie(
                0, "golden_key", gk, None, False,
                ".funpay.com", True, True, "/", True, True, None, False, None, None, {}
            )
            self.cj.set_cookie(ck)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                for k, v in DEFAULT_DATA.items():
                    if k not in d:
                        d[k] = v
                # Inject environment secrets if running in cloud
                if os.environ.get("FUNPAY_GOLDEN_KEY"):
                    d["golden_key"] = os.environ.get("FUNPAY_GOLDEN_KEY").strip()
                if os.environ.get("TELEGRAM_BOT_TOKEN"):
                    d.setdefault("telegram", {})["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN").strip()
                if os.environ.get("TELEGRAM_CHAT_ID"):
                    d.setdefault("telegram", {})["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID").strip()
                d.setdefault("telegram", {})["notify_bumps"] = True
                for t in d.get("transactions", []):
                    if t.get("id") == "lzt_genshin_1":
                        t["amount"] = 90.0
                        t["qty"] = 3
                        t["note"] = "3 акка 21 AR Европа по 30 ₽ (Mayo: 1-я партия от 6 окт)"
                return d
            except Exception:
                pass
        d = json.loads(json.dumps(DEFAULT_DATA))
        if os.environ.get("FUNPAY_GOLDEN_KEY"):
            d["golden_key"] = os.environ.get("FUNPAY_GOLDEN_KEY").strip()
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            d.setdefault("telegram", {})["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN").strip()
        if os.environ.get("TELEGRAM_CHAT_ID"):
            d.setdefault("telegram", {})["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID").strip()
        self.save_data_raw(d)
        return d

    def save_data_raw(self, d):
        try:
            to_save = copy.deepcopy(d)
            # If running in cloud via GitHub Secrets, don't dump credentials back into app_data.json
            if os.environ.get("FUNPAY_GOLDEN_KEY"):
                to_save["golden_key"] = ""
            if os.environ.get("TELEGRAM_BOT_TOKEN"):
                to_save.setdefault("telegram", {})["bot_token"] = ""
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Save error:", e)

    def save_data(self):
        with self.lock:
            self.save_data_raw(self.data)

    def load_recent_logs(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                self.logs = lines[-150:]
            except Exception:
                pass

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        full_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{full_ts}] {msg}"
        if sys.stdout:
            print(line, flush=True)
        with self.lock:
            self.logs.append(f"[{ts}] {msg}")
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def fp_request(self, url, data=None, extra_headers=None):
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

    # ---------------- TELEGRAM BOT ----------------
    def send_telegram(self, text, with_keyboard=True):
        tg = self.data.get("telegram", {})
        token = tg.get("bot_token", "").strip()
        chat_id = str(tg.get("chat_id", "")).strip()
        if not token or not chat_id:
            return False, "Не заполнен Bot Token или Chat ID"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }
        if with_keyboard:
            reply_markup = {
                "keyboard": [
                    [{"text": "📊 Статистика и Прибыль"}, {"text": "🛒 Последние продажи"}],
                    [{"text": "🚀 Поднять лоты сейчас"}, {"text": "📱 Открыть на телефоне"}]
                ],
                "resize_keyboard": True
            }
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        payload = urllib.parse.urlencode(params).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload, headers={"User-Agent": USER_AGENT})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            res = json.loads(raw)
            if res.get("ok"):
                return True, "Уведомление успешно отправлено!"
            return False, res.get("description", "Ошибка Telegram API")
        except Exception as e:
            return False, str(e)

    def start_mobile_tunnel(self):
        cf_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
        cf_exe = os.path.join(BASE_DIR, cf_name)
        if not os.path.exists(cf_exe):
            return
        time.sleep(2.0)
        try:
            import subprocess
            kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "ignore",
            }
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000
            self.tunnel_proc = subprocess.Popen(
                [cf_exe, "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
                **kwargs
            )
            sent_url = False
            for line in self.tunnel_proc.stdout:
                if not sent_url:
                    m = re.search(r"(https://[a-zA-Z0-9-]+\.trycloudflare\.com)", line)
                    if m:
                        sent_url = True
                        self.mobile_url = m.group(1)
                        self.log(f"📱 Мобильная ссылка активна: {self.mobile_url}")
                        mode_label = "☁️ ОБЛАЧНЫЙ СЕРВЕР (GitHub Cloud 24/7)" if os.name != "nt" else "💻 ДОМАШНИЙ ПК"
                        self.send_telegram(
                            f"📱 <b>Мобильный доступ к AEGIS FunPay Terminal готов!</b>\n"
                            f"⚙️ <b>Режим:</b> {mode_label}\n\n"
                            f"🌐 <b>Открыть дашборд (с телефона или ПК):</b>\n"
                            f"👉 <a href=\"{self.mobile_url}\">{self.mobile_url}</a>\n\n"
                            f"<i>Нажмите на ссылку - откроется полный дашборд с кассой, заказами и автоподнятием!</i>"
                        )
        except Exception as e:
            self.log(f"Ошибка мобильного туннеля: {e}")

    def poll_telegram_commands(self):
        tg = self.data.get("telegram", {})
        token = tg.get("bot_token", "").strip()
        if not token:
            return
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={self.tg_last_update_id + 1}&timeout=1"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
            res = json.loads(raw)
            if not res.get("ok"):
                return
            for upd in res.get("result", []):
                uid = upd.get("update_id", 0)
                if uid > self.tg_last_update_id:
                    self.tg_last_update_id = uid
                msg = upd.get("message") or {}
                txt = (msg.get("text") or "").strip()
                if not txt:
                    continue

                if txt in ("/start", "📊 Статистика и Прибыль", "/stats"):
                    a = self.compute_analytics()
                    reply = (
                        f"⚡ <b>AEGIS FunPay Terminal - Сводка</b>\n\n"
                        f"👤 <b>Профиль:</b> {self.username} (Онлайн 🟢)\n"
                        f"🏦 <b>Баланс FunPay:</b> <code>{self.balance}</code>\n\n"
                        f"📈 <b>Заработано с продаж:</b> <code>+{a['total_revenue']} ₽</code> ({a['orders_counted']} шт.)\n"
                        f"💎 <b>Чистая прибыль с проданного:</b> <code>+{a['sales_profit']} ₽</code> (Маржа +{a['sales_roi']}%)\n"
                        f"🚀 <b>Потенциал склада:</b> <code>+{a['potential_physical_rev']:.0f} ₽ + ∞</code> (С вечным акком: +{a['potential_total_rev']:.0f} ₽)\n"
                        f"📦 <b>Окупаемость всего банка:</b> <code>{a['total_revenue']} / {a['total_expenses']} ₽ ({a['recoup_pct']}%)</code>\n\n"
                        f"<b>По проектам:</b>\n"
                        f"• <b>Genshin Impact:</b> Выручка +{a['revenue_by_category'].get('Genshin Impact', 0):.0f} ₽ | Чистый плюс с продаж: <b>+{a['profit_by_category'].get('Genshin Impact', 0):.0f} ₽</b> | На складе {a['genshin_left']} шт. (+{a['genshin_left'] * 69} ₽)\n"
                        f"• <b>Steam Оффлайн (10 игр):</b> Выручка +{a['revenue_by_category'].get('Steam Оффлайн (10 игр)', 0):.0f} ₽ | Потенциал: +1 295+ ₽ (∞)\n"
                        f"• <b>Steam Казахстан:</b> На складе {a['kzt_left']} шт. (Потенциал +{a['kzt_left'] * 13} ₽)"
                    )
                    self.send_telegram(reply)
                elif txt in ("🛒 Последние продажи", "/orders"):
                    lines = ["🛒 <b>Ваши актуальные продажи на FunPay:</b>\n"]
                    for o in self.orders[:8]:
                        st_icon = "🔵" if o["status"] == "Оплачен" else ("🟢" if o["status"] == "Закрыт" else "🔴")
                        lines.append(
                            f"{st_icon} <a href=\"{o['url']}\">#{o['id']}</a> - <b>+{o['price']:.0f} ₽</b> | {o['cat']} ({o['buyer']})"
                        )
                    self.send_telegram("\n".join(lines))
                elif txt in ("🚀 Поднять лоты сейчас", "/bump"):
                    self.send_telegram("🚀 Запускаю внеочередное поднятие всех разделов...")
                    threading.Thread(target=self.raise_all_categories, daemon=True).start()
                elif txt in ("📱 Открыть на телефоне", "🏦 Баланс FunPay", "/mobile"):
                    m_url = getattr(self, "mobile_url", None) or "Запускается..."
                    self.send_telegram(
                        f"📱 <b>Открыть дашборд на телефоне:</b>\n\n"
                        f"🌐 <b>Через любой интернет (4G/LTE/Wi-Fi):</b>\n"
                        f"👉 <a href=\"{m_url}\">{m_url}</a>\n\n"
                        f"🏠 <b>Дома по Wi-Fi:</b>\n"
                        f"👉 <code>http://192.168.1.3:8765</code>"
                    )
        except Exception:
            pass

    def detect_telegram_chat_id(self, token):
        token = token.strip()
        if not token:
            return False, "Введите Bot Token от @BotFather"
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
            res = json.loads(raw)
            if not res.get("ok"):
                return False, res.get("description", "Неверный токен")
            updates = res.get("result", [])
            for upd in reversed(updates):
                msg = upd.get("message") or upd.get("edited_message") or upd.get("my_chat_member")
                if msg and "chat" in msg:
                    cid = msg["chat"].get("id")
                    uname = msg["chat"].get("username") or msg["chat"].get("first_name") or ""
                    if cid:
                        with self.lock:
                            self.data["telegram"]["bot_token"] = token
                            self.data["telegram"]["chat_id"] = str(cid)
                        self.save_data()
                        return True, {"chat_id": str(cid), "username": uname}
            return False, "Напишите боту /start в Telegram и нажмите кнопку еще раз!"
        except Exception as e:
            return False, f"Ошибка подключения к Telegram API: {e}"

    # ---------------- FUNPAY SYNC ----------------
    def init_funpay_session(self):
        try:
            page = self.fp_request("https://funpay.com/")
            m = re.search(r'data-app-data="([^"]+)"', page)
            if not m:
                self.online_status = False
                self.log("Не удалось получить data-app-data с FunPay.")
                return False
            app_data = json.loads(html.unescape(m.group(1)))
            uid = app_data.get("userId")
            if not uid:
                self.online_status = False
                self.log("Ошибка авторизации: проверьте golden_key.")
                return False
            self.user_id = uid
            self.online_status = True
            self.last_ping_ts = time.time()

            bal_m = re.findall(r'badge-balance[^>]*>([^<]+)<', page)
            if bal_m:
                self.balance = bal_m[0].strip()
                num_str = re.sub(r"[^\d.]", "", self.balance.replace(",", "."))
                self.balance_num = float(num_str) if num_str else 0.0

            uname_m = re.search(r'<div class="user-link-name">([^<]+)</div>', page)
            if uname_m:
                self.username = uname_m.group(1).strip()

            self.log(f"Сессия FunPay активна: {self.username} (ID {self.user_id}) | Баланс: {self.balance}")
            return True
        except Exception as e:
            self.online_status = False
            self.log(f"Ошибка инициализации FunPay: {e}")
            return False

    def sync_profile_and_categories(self):
        if not self.user_id:
            if not self.init_funpay_session():
                return
        try:
            u_html = self.fp_request(f"https://funpay.com/users/{self.user_id}/")
            cat_matches = re.findall(
                r'<h3><a href="https://funpay\.com/lots/(\d+)/">([^<]+)</a></h3>',
                u_html
            )
            cat_names = {cid: html.unescape(cname.strip()) for cid, cname in cat_matches}
            trade_links = re.findall(r'href="(https://funpay\.com/lots/(\d+)/trade)"', u_html)

            existing_map = {c["id"]: c for c in self.categories}
            new_cats = []
            seen = set()
            for full_url, cid in trade_links:
                if cid in seen:
                    continue
                cname = cat_names.get(cid, f"Раздел {cid}")
                if "dota 2" in cname.lower():
                    continue
                seen.add(cid)
                old = existing_map.get(cid, {})
                new_cats.append({
                    "id": cid,
                    "name": cname,
                    "url": full_url,
                    "last_status": old.get("last_status", "Активен"),
                    "wait_mins": old.get("wait_mins", 0),
                    "next_bump_ts": old.get("next_bump_ts", 0),
                    "updated_at": old.get("updated_at", "-")
                })
            self.categories = new_cats

            lots_raw = re.findall(
                r'<a href="https://funpay\.com/lots/offer\?id=(\d+)" class="tc-item[^"]*">(.*?)</a>',
                u_html, re.S
            )
            parsed_lots = []
            for lid, linner in lots_raw:
                desc = re.search(r'<div class="tc-desc-text">([^<]+)</div>', linner)
                amt = re.search(r'<div class="tc-amount"[^>]*>([^<]+)</div>', linner)
                pr = re.search(r'<div class="tc-price"[^>]*>\s*<div>([^<]+)<span class="unit">([^<]+)</span>', linner)
                stock_val = amt.group(1).strip().replace(" ", "") if amt else "∞"
                ltitle = html.unescape(desc.group(1).strip()) if desc else f"Лот {lid}"
                if "жетон" in ltitle.lower():
                    continue
                parsed_lots.append({
                    "id": lid,
                    "title": ltitle,
                    "stock": stock_val,
                    "price": pr.group(1).strip() if pr else "0",
                    "unit": pr.group(2).strip() if pr else "₽",
                    "url": f"https://funpay.com/lots/offerEdit?offer={lid}"
                })
            self.active_lots = parsed_lots
        except Exception as e:
            self.log(f"Ошибка синхронизации профиля: {e}")

    def is_relevant_order(self, order_obj):
        d = order_obj.get("date", "").lower()
        cat = order_obj.get("cat", "").lower()
        title = order_obj.get("title", "").lower()
        if re.search(r"\b202[0-5]\b", d):
            return False
        if "dota 2" in cat or "жетон" in title or "карточки" in title:
            return False
        return True

    def sync_orders(self, notify=True):
        try:
            orders_html = self.fp_request("https://funpay.com/orders/trade")
            self.online_status = True
            self.last_orders_ts = time.time()

            bal_m = re.findall(r'badge-balance[^>]*>([^<]+)<', orders_html)
            if bal_m:
                self.balance = bal_m[0].strip()
                num_str = re.sub(r"[^\d.]", "", self.balance.replace(",", "."))
                self.balance_num = float(num_str) if num_str else 0.0

            items = re.findall(
                r'<a href="https://funpay\.com/orders/([A-Z0-9]+)/" class="tc-item([^"]*)">(.*?)</a>',
                orders_html, re.S
            )

            known = self.data.get("known_orders", {})
            first_sync = (len(known) == 0)
            parsed_orders = []
            new_notifications = []

            for oid, cls, inner in items:
                date_m = re.search(r'<div class="tc-date-time">([^<]+)</div>', inner)
                left_m = re.search(r'<div class="tc-date-left">([^<]+)</div>', inner)
                desc_m = re.search(r'<div class="order-desc">\s*<div>([^<]+)</div>\s*<div class="text-muted">([^<]+)</div>', inner, re.S)
                buyer_m = re.search(r'<span class="pseudo-a"[^>]*>([^<]+)</span>', inner)
                status_m = re.search(r'<div class="tc-status[^"]*">([^<]+)</div>', inner)
                price_m = re.search(r'<div class="tc-price[^"]*">([^<]+)<span class="unit">([^<]+)</span>', inner)

                price_str = price_m.group(1).strip().replace(" ", "") if price_m else "0"
                try:
                    price_val = float(price_str.replace(",", "."))
                except Exception:
                    price_val = 0.0

                order_obj = {
                    "id": oid,
                    "cls": cls.strip(),
                    "date": date_m.group(1).strip() if date_m else "",
                    "ago": left_m.group(1).strip() if left_m else "",
                    "title": html.unescape(desc_m.group(1).strip()) if desc_m else "",
                    "cat": html.unescape(desc_m.group(2).strip()) if desc_m else "",
                    "buyer": html.unescape(buyer_m.group(1).strip()) if buyer_m else "",
                    "status": status_m.group(1).strip() if status_m else "",
                    "price": price_val,
                    "unit": price_m.group(2).strip() if price_m else "₽",
                    "url": f"https://funpay.com/orders/{oid}/"
                }

                if not self.is_relevant_order(order_obj):
                    known[oid] = order_obj["status"]
                    continue

                parsed_orders.append(order_obj)

                prev_status = known.get(oid)
                cur_status = order_obj["status"]

                if not first_sync and notify:
                    tg_cfg = self.data.get("telegram", {})
                    if prev_status is None:
                        self.log(f"🔥 НОВЫЙ ЗАКАЗ #{oid} | +{order_obj['price']} ₽ | {order_obj['buyer']} | {order_obj['cat']}")
                        if tg_cfg.get("notify_new_order", True):
                            msg = (
                                f"🎉 <b>НОВАЯ ПРОДАЖА НА FUNPAY!</b>\n\n"
                                f"🆔 <b>Заказ:</b> <a href=\"https://funpay.com/orders/{oid}/\">#{oid}</a>\n"
                                f"💵 <b>Сумма:</b> <code>+{order_obj['price']:.2f} {order_obj['unit']}</code>\n"
                                f"👤 <b>Покупатель:</b> <code>{order_obj['buyer']}</code>\n"
                                f"🎮 <b>Раздел:</b> {order_obj['cat']}\n"
                                f"📦 <b>Лот:</b> {order_obj['title']}\n"
                                f"📊 <b>Статус:</b> {cur_status}\n"
                                f"🏦 <b>Баланс FunPay:</b> {self.balance}"
                            )
                            new_notifications.append(msg)
                    elif prev_status != cur_status:
                        self.log(f"📦 Статус заказа #{oid} изменился: {prev_status} -> {cur_status}")
                        if cur_status == "Закрыт" and tg_cfg.get("notify_order_closed", True):
                            msg = (
                                f"💰 <b>ПОКУПАТЕЛЬ ПОДТВЕРДИЛ ЗАКАЗ!</b>\n\n"
                                f"🆔 <b>Заказ:</b> <a href=\"https://funpay.com/orders/{oid}/\">#{oid}</a>\n"
                                f"💵 <b>Зачислено:</b> <code>+{order_obj['price']:.2f} {order_obj['unit']}</code>\n"
                                f"👤 <b>Покупатель:</b> <code>{order_obj['buyer']}</code>\n"
                                f"🎮 <b>Раздел:</b> {order_obj['cat']}\n"
                                f"🏦 <b>Баланс FunPay:</b> {self.balance}"
                            )
                            new_notifications.append(msg)
                        elif cur_status == "Возврат":
                            msg = (
                                f"⚠️ <b>ВОЗВРАТ ПО ЗАКАЗУ #{oid}</b>\n"
                                f"👤 Покупатель: {order_obj['buyer']} | Сумма: {order_obj['price']} ₽"
                            )
                            new_notifications.append(msg)

                known[oid] = cur_status

            self.orders = parsed_orders
            with self.lock:
                self.data["known_orders"] = known
                self.data["cached_orders"] = parsed_orders
                self.data["cached_balance"] = self.balance
            self.save_data()

            for notif_text in new_notifications:
                ok, info = self.send_telegram(notif_text)
                if ok:
                    self.log("✈️ Уведомление о заказе отправлено в Telegram!")
                else:
                    self.log(f"✈️ Не удалось отправить в Telegram: {info}")

        except Exception as e:
            self.log(f"Ошибка синхронизации заказов: {e}")

    def raise_single_category(self, cat_obj):
        trade_url = cat_obj["url"]
        cat_name = cat_obj["name"]
        t_page = self.fp_request(trade_url)

        btn = re.search(r'data-game="(\d+)"[^>]*data-node="(\d+)"', t_page)
        if not btn:
            cat_obj["last_status"] = "Кнопка не найдена"
            cat_obj["updated_at"] = datetime.now().strftime("%H:%M:%S")
            return "Кнопка не найдена"

        game_id, real_node_id = btn.group(1), btn.group(2)

        def send_raise(extra_node_ids=None):
            params = [("game_id", game_id), ("node_id", real_node_id)]
            if extra_node_ids:
                for nid in extra_node_ids:
                    params.append(("node_ids[]", nid))
            payload = urllib.parse.urlencode(params)
            raw = self.fp_request(
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
        mins = int(wait_sec) // 60 if wait_sec else 0

        now_t = time.time()
        cat_obj["last_status"] = msg_clean
        cat_obj["wait_mins"] = mins
        cat_obj["updated_at"] = now_msk().strftime("%H:%M:%S")

        # Точный таймштамп следующего поднятия для конкретного раздела
        if wait_sec and int(wait_sec) > 0:
            cat_obj["next_bump_ts"] = now_t + int(wait_sec)
        elif "подняты" in msg_clean.lower() or "успешно" in msg_clean.lower():
            cat_obj["next_bump_ts"] = now_t + 4 * 3600
            cat_obj["wait_mins"] = 240
        else:
            cat_obj["next_bump_ts"] = now_t + 180

        if wait_sec:
            self.log(f"[{cat_name}] -> {msg_clean} (~{mins} мин.)")
        else:
            self.log(f"[{cat_name}] -> {msg_clean}")
        return msg_clean

    def raise_all_categories(self, force_notify=False):
        if not self.categories:
            self.sync_profile_and_categories()
        self.log(f"🚀 Запуск проверки автоподнятия ({len(self.categories)} разделов)...")
        raised_any = []
        for c in self.categories:
            try:
                msg = self.raise_single_category(c)
                if "подняты" in msg.lower() or "успешно" in msg.lower():
                    raised_any.append(c["name"])
                time.sleep(1.0)
            except Exception as e:
                self.log(f"Ошибка поднятия [{c['name']}]: {e}")
                c["next_bump_ts"] = time.time() + 180
        self.last_raise_ts = time.time()

        # Ближайшее поднятие вычисляется по САМОМУ РАННЕМУ разделу, чтобы ни один лот не простаивал!
        future_ts = [c.get("next_bump_ts", 0) for c in self.categories if c.get("next_bump_ts", 0) > time.time()]
        self.next_bump_ts = min(future_ts) if future_ts else (self.last_raise_ts + 4 * 3600)
        self.next_sync_wait_sec = max(60, int(self.next_bump_ts - self.last_raise_ts))

        if raised_any or force_notify:
            tg_cfg = self.data.get("telegram", {})
            if tg_cfg.get("notify_bumps", True) or force_notify:
                if len(raised_any) == 1:
                    text = f"БОСС, ТВОЙ ИИ АГЕНТ ПОДНЯЛ ОБЬЯВЛЕНИЕ ({raised_any[0]}). ОТДЫХАЙ, Я РАБОТАЮ ЗА ТЕБЯ 😉🔥"
                elif len(raised_any) > 1:
                    text = f"БОСС, ТВОЙ ИИ АГЕНТ ПОДНЯЛ ОБЬЯВЛЕНИЯ ({len(raised_any)} шт.). ОТДЫХАЙ, Я РАБОТАЮ ЗА ТЕБЯ 😉🔥"
                else:
                    text = "БОСС, ТВОЙ ИИ АГЕНТ ПОДНЯЛ ОБЬЯВЛЕНИЯ. ОТДЫХАЙ, Я РАБОТАЮ ЗА ТЕБЯ 😉🔥"
                ok, _ = self.send_telegram(text)
                self.log(f"✈️ Отчет о поднятии ({len(raised_any)} шт.) отправлен в Telegram: {ok}")

    def match_period(self, date_str, mode):
        if not mode or mode in ("all", "1y", "year", "since_sept"):
            return True
        now = datetime.now()
        d = (date_str or "").lower().strip()
        m, y = now.month, now.year

        m_match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", d)
        if m_match:
            m = int(m_match.group(2))
            y = int(m_match.group(3))
        elif "сегодня" in d or "вчера" in d:
            m, y = now.month, now.year
        else:
            ru_months = [
                ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4),
                ("ма", 5), ("июн", 6), ("июл", 7), ("август", 8),
                ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12)
            ]
            for name, idx in ru_months:
                if name in d:
                    m = idx
                    break
            y_match = re.search(r"\b(202\d)\b", d)
            if y_match:
                y = int(y_match.group(1))

        months_diff = (now.year - y) * 12 + (now.month - m)
        if mode in ("1m", "month"):
            return months_diff == 0
        if mode in ("3m", "3months"):
            return 0 <= months_diff < 3
        if mode.startswith("m_"):
            try:
                target_m = int(mode.split("_")[1])
                return m == target_m and y == now.year
            except Exception:
                return True
        return True

    def compute_analytics(self):
        sales_mode = self.data.get("settings", {}).get("sales_mode", "1m")
        all_txs = [t for t in self.data.get("transactions", []) if t.get("type", "expense") == "expense"]
        txs = [t for t in all_txs if self.match_period(t.get("date", ""), sales_mode)]

        manual_expenses = sum(float(t.get("amount", 0)) for t in txs)

        fp_closed_revenue = 0.0
        fp_pending_revenue = 0.0
        fp_orders_counted = 0
        by_category = {}
        count_by_category = {}
        all_count_by_category = {}
        sold_cogs = 0.0  # Себестоимость именно проданных штук

        for o in self.orders:
            if o["status"] == "Возврат":
                continue

            title_up = o["title"].upper()
            cat_raw = o["cat"].split(",")[0].strip() or "Другое"
            if "9 ИГР В ПОДАРОК" in title_up or cat_raw in ("BeamNG.drive", "Forza Horizon", "Garry’s Mod", "Garry's Mod", "Terraria", "The Long Dark", "Прочие игры"):
                cat_key = "Steam Оффлайн (10 игр)"
                unit_cost = 0.0
            elif "КАЗАХСТАН" in title_up or "ТЕНГЕ" in title_up:
                cat_key = "Steam Казахстан"
                unit_cost = 2.0
            elif "GENSHIN" in cat_raw.upper():
                cat_key = "Genshin Impact"
                unit_cost = 30.0
            else:
                cat_key = cat_raw
                unit_cost = 0.0

            all_count_by_category[cat_key] = all_count_by_category.get(cat_key, 0) + 1

            if not self.match_period(o.get("date", ""), sales_mode):
                continue

            p = float(o["price"])
            fp_orders_counted += 1
            if o["status"] == "Оплачен":
                fp_pending_revenue += p
            else:
                fp_closed_revenue += p

            sold_cogs += unit_cost
            by_category[cat_key] = by_category.get(cat_key, 0.0) + p
            count_by_category[cat_key] = count_by_category.get(cat_key, 0) + 1

        expenses_by_cat = {}
        for t in txs:
            ck = t.get("category") or "Прочее"
            expenses_by_cat[ck] = expenses_by_cat.get(ck, 0.0) + float(t.get("amount", 0))

        total_revenue = fp_closed_revenue + fp_pending_revenue
        sales_profit = total_revenue - sold_cogs  # Чистый плюс с проданных товаров
        sales_roi = ((sales_profit / sold_cogs) * 100.0) if sold_cogs > 0 else 100.0
        net_overall = total_revenue - manual_expenses
        recoup_pct = round((total_revenue / manual_expenses) * 100.0, 1) if manual_expenses > 0 else (100.0 if total_revenue > 0 else 0.0)

        # Profit per category on sold items & Potential earnings from stock
        profit_by_category = {}
        potential_by_category = {}

        genshin_sold = all_count_by_category.get("Genshin Impact", 0)
        genshin_bought = sum(int(t.get("qty", max(1, round(float(t.get("amount", 0)) / 30.0)))) for t in all_txs if t.get("category") == "Genshin Impact")
        genshin_left = max(0, genshin_bought - genshin_sold)
        potential_by_category["Genshin Impact"] = {
            "stock_left": f"{genshin_left} шт.",
            "potential_rev": round(genshin_left * 69.0, 2),
            "total_expected_profit": round((by_category.get("Genshin Impact", 0.0) + genshin_left * 69.0) - expenses_by_cat.get("Genshin Impact", 0.0), 2),
            "desc": f"На складе {genshin_left} акков по 69 ₽ = +{genshin_left * 69:.0f} ₽"
        }

        kzt_sold = all_count_by_category.get("Steam Казахстан", 0)
        kzt_bought = sum(int(t.get("qty", max(1, round(float(t.get("amount", 0)) / 2.0)))) for t in all_txs if t.get("category") == "Steam Казахстан")
        kzt_left = max(0, kzt_bought - kzt_sold)
        potential_by_category["Steam Казахстан"] = {
            "stock_left": f"{kzt_left} шт.",
            "potential_rev": round(kzt_left * 13.0, 2),
            "total_expected_profit": round((by_category.get("Steam Казахстан", 0.0) + kzt_left * 13.0) - expenses_by_cat.get("Steam Казахстан", 0.0), 2),
            "desc": f"На складе {kzt_left} авторегов по 13 ₽ = +{kzt_left * 13:.0f} ₽"
        }

        offline_conservative_rev = 1295.0
        potential_by_category["Steam Оффлайн (10 игр)"] = {
            "stock_left": "∞ (7 игр × 30 слотов)",
            "potential_rev": offline_conservative_rev,
            "total_expected_profit": round((by_category.get("Steam Оффлайн (10 игр)", 0.0) + offline_conservative_rev) - expenses_by_cat.get("Steam Оффлайн (10 игр)", 0.0), 2),
            "desc": "Вечный товар (7 игр): ~1 295+ ₽ с первых 70 активаций (далее ∞)"
        }

        for ck in set(list(by_category.keys()) + list(expenses_by_cat.keys())):
            rev_c = by_category.get(ck, 0.0)
            cnt_c = count_by_category.get(ck, 0)
            if ck == "Genshin Impact":
                profit_by_category[ck] = rev_c - (cnt_c * 30.0)
            elif ck == "Steam Казахстан":
                profit_by_category[ck] = rev_c - (cnt_c * 2.0)
            else:
                profit_by_category[ck] = rev_c

        potential_physical_rev = round(genshin_left * 69.0 + kzt_left * 13.0, 2)
        potential_total_rev = round(potential_physical_rev + offline_conservative_rev, 2)
        expected_total_net_profit = round((total_revenue + potential_total_rev) - manual_expenses, 2)

        return {
            "total_revenue": round(total_revenue, 2),
            "fp_closed_revenue": round(fp_closed_revenue, 2),
            "fp_pending_revenue": round(fp_pending_revenue, 2),
            "manual_income": 0.0,
            "total_expenses": round(manual_expenses, 2),
            "sold_cogs": round(sold_cogs, 2),
            "sales_profit": round(sales_profit, 2),
            "sales_roi": round(sales_roi, 1),
            "net_profit": round(net_overall, 2),
            "recoup_pct": recoup_pct,
            "orders_counted": fp_orders_counted,
            "potential_physical_rev": potential_physical_rev,
            "potential_total_rev": potential_total_rev,
            "expected_total_net_profit": expected_total_net_profit,
            "genshin_left": genshin_left,
            "kzt_left": kzt_left,
            "revenue_by_category": by_category,
            "expenses_by_category": expenses_by_cat,
            "profit_by_category": profit_by_category,
            "count_by_category": count_by_category,
            "potential_by_category": potential_by_category,
            "filtered_transactions": txs
        }

    def get_full_snapshot(self):
        analytics = self.compute_analytics()
        now_t = time.time()
        for c in self.categories:
            nb = c.get("next_bump_ts", 0)
            if nb > now_t:
                c["wait_mins"] = max(1, int(round((nb - now_t) / 60.0)))
            else:
                c["wait_mins"] = 0

        future_ts = [c.get("next_bump_ts", 0) for c in self.categories if c.get("next_bump_ts", 0) > now_t]
        if future_ts:
            next_ts = min(future_ts)
        else:
            next_ts = getattr(self, "next_bump_ts", 0)
            if next_ts <= now_t:
                next_ts = now_t + 4 * 3600
        self.next_bump_ts = next_ts
        waves = []
        labels = [
            "1-я волна (Ближайшее поднятие)",
            "2-я волна (+4 часа)",
            "3-я волна (+8 часов)",
            "4-я волна (+12 часов)",
            "5-я волна (+16 часов)"
        ]
        for idx, lbl in enumerate(labels):
            w_ts = next_ts + idx * 4 * 3600
            rem_min = max(1, int(round((w_ts - now_t) / 60.0)))
            if rem_min >= 60:
                h_part = rem_min // 60
                m_part = rem_min % 60
                in_str = f"через {h_part} ч {m_part} мин" if m_part else f"через {h_part} ч"
            else:
                in_str = f"через {rem_min} мин"
            waves.append({
                "label": lbl,
                "time_msk": datetime.fromtimestamp(w_ts, MSK_TZ).strftime("%H:%M"),
                "in_text": in_str,
                "ts": w_ts
            })
        return {
            "profile": {
                "user_id": self.user_id,
                "username": self.username,
                "balance": self.balance,
                "balance_num": self.balance_num,
                "online": self.online_status,
                "last_ping": datetime.fromtimestamp(self.last_ping_ts, MSK_TZ).strftime("%H:%M:%S") if self.last_ping_ts else "-",
                "last_raise": datetime.fromtimestamp(self.last_raise_ts, MSK_TZ).strftime("%H:%M:%S") if self.last_raise_ts else "-",
                "last_orders": datetime.fromtimestamp(self.last_orders_ts, MSK_TZ).strftime("%H:%M:%S") if self.last_orders_ts else "-",
                "next_bump_ts": next_ts,
                "next_bump_msk": datetime.fromtimestamp(next_ts, MSK_TZ).strftime("%H:%M"),
                "schedule_waves": waves
            },
            "settings": self.data.get("settings", {}),
            "telegram": self.data.get("telegram", {}),
            "analytics": analytics,
            "transactions": analytics.get("filtered_transactions", self.data.get("transactions", [])),
            "categories": self.categories,
            "active_lots": self.active_lots,
            "orders": self.orders,
            "logs": self.logs[-80:]
        }


STATE = AppState()


def background_worker():
    STATE.log("⚡ Запуск фонового движка AEGIS FunPay Manager...")
    threading.Thread(target=STATE.start_mobile_tunnel, daemon=True).start()
    STATE.init_funpay_session()
    # Sync orders FIRST so dashboard numbers are immediately populated
    STATE.sync_orders(notify=False)
    STATE.sync_profile_and_categories()

    if STATE.data.get("settings", {}).get("auto_bump", True):
        STATE.raise_all_categories()

    last_category_check_ts = 0
    last_cat_sync_ts = time.time()

    while True:
        try:
            now = time.time()
            cfg = STATE.data.get("settings", {})

            # Poll Telegram commands every cycle
            STATE.poll_telegram_commands()

            # Check FunPay orders & keep online every 45s
            order_interval = max(20, int(cfg.get("order_check_sec", 45)))
            if now - STATE.last_orders_ts >= order_interval:
                if cfg.get("eternal_online", True):
                    STATE.last_ping_ts = now
                STATE.sync_orders(notify=True)

            # Periodically sync categories every 10 minutes to pick up newly added lots
            if now - last_cat_sync_ts >= 600:
                last_cat_sync_ts = now
                STATE.sync_profile_and_categories()

            # Self-ping cloud URL every 45s to prevent free cloud sleep
            ext_url = os.environ.get("RENDER_EXTERNAL_URL") or (
                f"https://{os.environ.get('SPACE_HOST')}" if os.environ.get("SPACE_HOST") else None
            )
            if ext_url and now - STATE.last_orders_ts < 6:
                try:
                    urllib.request.urlopen(f"{ext_url.rstrip('/')}/api/state", timeout=8).read()
                except Exception:
                    pass

            # Smart Auto-Bump: проверяем каждые 15 секунд, не подошел ли срок поднятия у ЛЮБОГО раздела
            if cfg.get("auto_bump", True) and (now - last_category_check_ts >= 15):
                last_category_check_ts = now
                ready_cats = [c for c in STATE.categories if c.get("next_bump_ts", 0) <= now]
                if ready_cats:
                    STATE.log(f"⏰ Наступило время поднятия для {len(ready_cats)} разделов: {', '.join([c['name'] for c in ready_cats])}")
                    raised_names = []
                    for c in ready_cats:
                        try:
                            msg = STATE.raise_single_category(c)
                            if "подняты" in msg.lower() or "успешно" in msg.lower():
                                raised_names.append(c["name"])
                            time.sleep(1.0)
                        except Exception as e:
                            STATE.log(f"Ошибка автоподнятия [{c.get('name')}]: {e}")
                            c["next_bump_ts"] = now + 180

                    # Пересчитываем ближайшую точку поднятия
                    future_ts = [c.get("next_bump_ts", 0) for c in STATE.categories if c.get("next_bump_ts", 0) > now]
                    STATE.next_bump_ts = min(future_ts) if future_ts else (now + 4 * 3600)
                    STATE.last_raise_ts = now

                    if raised_names and STATE.data.get("telegram", {}).get("notify_bumps", True):
                        if len(raised_names) == 1:
                            text = f"БОСС, ТВОЙ ИИ АГЕНТ ПОДНЯЛ ОБЬЯВЛЕНИЕ ({raised_names[0]}). ОТДЫХАЙ, Я РАБОТАЮ ЗА ТЕБЯ 😉🔥"
                        else:
                            text = f"БОСС, ТВОЙ ИИ АГЕНТ ПОДНЯЛ ОБЬЯВЛЕНИЯ ({len(raised_names)} шт.). ОТДЫХАЙ, Я РАБОТАЮ ЗА ТЕБЯ 😉🔥"
                        ok, _ = STATE.send_telegram(text)
                        STATE.log(f"✈️ Отчет о поднятии ({len(raised_names)} шт.) отправлен в Telegram: {ok}")

        except Exception as e:
            STATE.log(f"Ошибка в фоновом потоке: {e}")

        time.sleep(3)


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "ignore")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            if os.path.exists(HTML_FILE):
                with open(HTML_FILE, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "index.html not found")
            return

        if self.path.startswith("/api/state"):
            self._send_json(STATE.get_full_snapshot())
            return

        self.send_error(404)

    def do_POST(self):
        body = self._read_body()

        if self.path == "/api/bump_all":
            threading.Thread(target=lambda: STATE.raise_all_categories(force_notify=True), daemon=True).start()
            self._send_json({"ok": True, "msg": "Автоподнятие запущено!"})
            return

        if self.path == "/api/bump_one":
            cid = str(body.get("id", ""))
            for c in STATE.categories:
                if c["id"] == cid:
                    msg = STATE.raise_single_category(c)
                    self._send_json({"ok": True, "msg": msg, "state": STATE.get_full_snapshot()})
                    return
            self._send_json({"ok": False, "msg": "Раздел не найден"})
            return

        if self.path == "/api/sync_now":
            STATE.sync_orders(notify=True)
            STATE.sync_profile_and_categories()
            self._send_json({"ok": True, "state": STATE.get_full_snapshot()})
            return

        if self.path == "/api/transaction/add":
            try:
                amount = float(str(body.get("amount", 0)).replace(",", "."))
            except Exception:
                amount = 0.0
            try:
                qty = max(1, int(body.get("qty", 1) or 1))
            except Exception:
                qty = 1
            if amount <= 0:
                self._send_json({"ok": False, "msg": "Введите корректную сумму закупки больше 0 ₽"})
                return
            cat_val = body.get("category", "Другое").strip() or "Другое"
            note_val = body.get("note", "").strip()
            unit_cost = round(amount / qty, 2)
            if not note_val:
                note_val = f"Закупка пачки: {qty} шт. (по {unit_cost:g} ₽/шт.)"
            elif qty > 1 and "шт" not in note_val.lower():
                note_val = f"{note_val} ({qty} шт. по {unit_cost:g} ₽)"
            tx = {
                "id": f"tx_{int(time.time()*1000)}",
                "type": "expense",
                "amount": round(amount, 2),
                "qty": qty,
                "unit_cost": unit_cost,
                "category": cat_val,
                "note": note_val,
                "date": body.get("date") or datetime.now().strftime("%d.%m.%Y %H:%M")
            }
            with STATE.lock:
                STATE.data["transactions"].insert(0, tx)
            STATE.save_data()
            STATE.log(f"📦 Закупка склада: -{amount} ₽ ({cat_val}, {qty} шт.)")
            self._send_json({"ok": True, "state": STATE.get_full_snapshot()})
            return

        if self.path == "/api/transaction/delete":
            tx_id = body.get("id")
            with STATE.lock:
                STATE.data["transactions"] = [
                    t for t in STATE.data.get("transactions", []) if t.get("id") != tx_id
                ]
            STATE.save_data()
            self._send_json({"ok": True, "state": STATE.get_full_snapshot()})
            return

        if self.path == "/api/settings/save":
            with STATE.lock:
                if "settings" in body:
                    STATE.data["settings"].update(body["settings"])
                if "telegram" in body:
                    STATE.data["telegram"].update(body["telegram"])
                if "golden_key" in body and body["golden_key"].strip():
                    new_gk = body["golden_key"].strip()
                    if new_gk != STATE.data.get("golden_key"):
                        STATE.data["golden_key"] = new_gk
                        STATE._init_cookie_opener()
                        threading.Thread(target=STATE.init_funpay_session, daemon=True).start()
            STATE.save_data()
            self._send_json({"ok": True, "state": STATE.get_full_snapshot()})
            return

        if self.path == "/api/telegram/detect":
            token = body.get("bot_token", "")
            ok, result = STATE.detect_telegram_chat_id(token)
            self._send_json({"ok": ok, "result": result, "state": STATE.get_full_snapshot()})
            return

        if self.path == "/api/telegram/test":
            with STATE.lock:
                if "telegram" in body:
                    STATE.data["telegram"].update(body["telegram"])
            STATE.save_data()
            analytics = STATE.compute_analytics()
            test_msg = (
                f"✅ <b>AEGIS FunPay Terminal подключен!</b>\n\n"
                f"👤 <b>Профиль:</b> {STATE.username} (ID {STATE.user_id})\n"
                f"🏦 <b>Баланс FunPay:</b> {STATE.balance}\n"
                f"📈 <b>Заработано с продаж:</b> +{analytics['total_revenue']} ₽ ({analytics['orders_counted']} шт.)\n"
                f"💎 <b>Чистый плюс с проданного:</b> +{analytics['sales_profit']} ₽ (Маржа +{analytics['sales_roi']}%)\n"
                f"📦 <b>Окупаемость банка:</b> {analytics['total_revenue']} / {analytics['total_expenses']} ₽ ({analytics['recoup_pct']}%)\n\n"
                f"🔔 <i>Уведомления о новых продажах и кнопки управления активны 24/7!</i>"
            )
            ok, msg = STATE.send_telegram(test_msg)
            if ok:
                STATE.log("✈️ Тестовое сообщение успешно отправлено в Telegram!")
            else:
                STATE.log(f"✈️ Ошибка отправки в Telegram: {msg}")
            self._send_json({"ok": ok, "msg": msg})
            return

        if self.path == "/api/open_url":
            target_url = body.get("url", "")
            if target_url.startswith("http"):
                try:
                    os.startfile(target_url)
                except Exception:
                    webbrowser.open(target_url)
            self._send_json({"ok": True})
            return

        self.send_error(404)


def open_app_window():
    url = f"http://127.0.0.1:{PORT}"
    try:
        os.startfile(url)
    except Exception:
        webbrowser.open(url)


if __name__ == "__main__":
    if "--once" in sys.argv:
        STATE.init_funpay_session()
        STATE.sync_orders(notify=True)
        STATE.sync_profile_and_categories()
        STATE.raise_all_categories()
        sys.exit(0)
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    already_running = (sock.connect_ex(("127.0.0.1", PORT)) == 0)
    sock.close()

    if already_running:
        if "--no-browser" not in sys.argv and os.name == "nt":
            open_app_window()
        sys.exit(0)

    worker = threading.Thread(target=background_worker, daemon=True)
    worker.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), RequestHandler)
    if sys.stdout:
        print(f"AEGIS FunPay Terminal запущен на порту {PORT}", flush=True)

    if "--no-browser" not in sys.argv and os.name == "nt":
        threading.Timer(1.0, open_app_window).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
