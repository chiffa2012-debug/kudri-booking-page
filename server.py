"""Локальный сервер «Кудри»: страница, форма записи, API и админка.
Запуск: python server.py  ->  http://127.0.0.1:8080  (админка: /admin)
Только стандартная библиотека Python."""
import html
import json
import os
import re
import secrets
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db
import offers
import owner
from yclients_sync import YClientsSync

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = {}  # token -> csrf
esc = html.escape
PHONE_RE = re.compile(r"^\+?\d{10,15}$")



VARIANT_RE = re.compile(r"^/v([1-3])(/.*)?$")
VARIANT_NAMES = {"1": "Молочный атлас", "2": "Графит и бронза", "3": "Живой салон"}
LINK_RE = re.compile(r"""(href|action)=(["']?)/(admin|book|thanks|ui\.css)""")
IMG_TYPES = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".svg": "image/svg+xml"}


def split_variant(path):
    m = VARIANT_RE.match(path)
    if m:
        return m.group(1), (m.group(2) or "/")
    return "", path


def rewrite_html(html_text, var):
    """Внутри варианта все внутренние ссылки остаются в нём; сообщаем страницам префикс."""
    pref = f"/v{var}" if var else ""
    html_text = LINK_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}{pref}/{m.group(3)}", html_text)
    if pref:
        html_text = re.sub(r"""(href=)(["'])/\2""", lambda m: f"{m.group(1)}{m.group(2)}{pref}/{m.group(2)}", html_text)
    v = var or "1"
    inject = f'<script>window.KV="{pref}";window.KVAR="{v}";document.documentElement.classList.add("t{v}");</script>'
    html_text = html_text.replace("</head>", inject + "</head>", 1) if "</head>" in html_text else inject + html_text
    if pref and "<aside class=\"side\"" not in html_text and "/admin" not in html_text[:0]:
        bar = ('<style>#kvbar{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:60;display:flex;gap:4px;padding:5px;border-radius:99px;'
               'background:rgba(36,35,34,.92);color:#f3ece2;font:500 12px Montserrat,system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.3)}'
               '#kvbar span{padding:7px 10px;opacity:.7}#kvbar a{color:#f3ece2;text-decoration:none;padding:7px 14px;border-radius:99px}'
               '#kvbar a[aria-current]{background:#a87852;color:#fff}</style>'
               '<div id="kvbar" role="navigation" aria-label="Варианты дизайна"><span>Вариант</span></div>'
               '<script>(function(){var b=document.getElementById("kvbar"),p=location.pathname.replace(/^\\/v[1-3]/,"")||"/";'
               '[["1","1 Атлас"],["2","2 Графит"],["3","3 Салон"]].forEach(function(x){var a=document.createElement("a");a.href="/v"+x[0]+p+location.search;a.textContent=x[1];'
               'if(x[0]==="' + v + '")a.setAttribute("aria-current","true");b.appendChild(a)})})()</script>')
        html_text = html_text.replace("</body>", bar + "</body>", 1)
    return html_text


def read_static(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return f.read()


ICONS = {
    "journal": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
    "visits": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "clients": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5M17 11a3 3 0 0 0 0-6M18.5 15c2 .6 3.3 2.2 3.5 5"/>',
    "catalog": '<path d="M4 7l8-4 8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4M4 17l8 4 8-4"/>',
    "settings": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "sync": '<path d="M20 7H8a4 4 0 0 0-4 4M4 17h12a4 4 0 0 0 4-4M17 4l3 3-3 3M7 14l-3 3 3 3"/>',
    "site": '<path d="M7 17L17 7M9 7h8v8"/>',
    "overview": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "finance": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
    "analytics": '<path d="M3 17l5-5 4 4 8-9"/><path d="M15 7h5v5"/>',
    "offers": '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v9H5v-9M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    "exit": '<path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M16 8l4 4-4 4M20 12H9"/>',
}
NAV = [("overview", "Обзор", "/admin/overview"), ("journal", "Журнал", "/admin"), ("visits", "Записи", "/admin/visits"), ("clients", "Клиенты", "/admin/clients"),
       ("catalog", "Каталог", "/admin/catalog"), ("finance", "Финансы", "/admin/finance"), ("analytics", "Аналитика", "/admin/analytics"), ("offers", "Допродажи", "/admin/offers"),
       ("settings", "Настройки", "/admin/settings"), ("sync", "YClients", "/admin/sync")]
ACTIVE = {"Обзор": "overview", "Финансы": "finance", "Аналитика": "analytics", "Допродажи": "offers", "Журнал": "journal", "Записи": "visits", "Клиенты": "clients", "Клиент": "clients",
          "Каталог": "catalog", "Настройки": "settings", "YClients": "sync"}


def icon(name):
    return f'<svg viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


def layout(title, body):
    cur = ACTIVE.get(title)
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if key == cur else ""}>{icon(key)}{label}</a>' for key, label, href in NAV)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Кудри</title><link rel="stylesheet" href="/ui.css"></head><body>
<div class="shell"><aside class="side"><div class="brand"><img src="/assets/kudri-mark-cream.png" alt=""><span>Кудри<small>СТУДИЯ КРАСОТЫ</small></span></div>
<nav class="nav" aria-label="Разделы">{nav}</nav><div class="grow"></div>
<div class="foot"><a href="/" target="_blank" rel="noopener">{icon("site")}Сайт для клиентов</a>
<form method="post" action="/admin/logout"><button type="submit">{icon("exit")}Выйти</button></form></div></aside>
<main class="main">{body}</main></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Kudri/0.1"
    var = ""

    # ---------- утилиты ----------
    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str) and ctype.startswith("text/html"):
            body = rewrite_html(body, self.var)
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def redirect(self, to, headers=None):
        if self.var and to.startswith("/") and not to.startswith("/v"):
            to = f"/v{self.var}{to}"
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def session(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        tok = c["sid"].value if "sid" in c else None
        return tok if tok in SESSIONS else None

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 100_000)).decode("utf-8", "replace")
        return {k: v[-1] for k, v in parse_qs(raw).items()}, parse_qs(raw)

    def log_message(self, fmt, *args):
        pass

    # ---------- маршруты ----------
    def do_GET(self):
        u = urlparse(self.path)
        self.var, p = split_variant(u.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        if p in ("/", "/index.html"):
            return self.send(200, read_static(f"index-{self.var or '1'}.html").decode("utf-8"))
        if p == "/variants":
            links = "".join(f'<li><a href="/v{k}/" target="_blank">{n}</a></li>' for k, n in VARIANT_NAMES.items())
            return self.send(200, f"<meta charset=utf-8><title>Варианты</title><ul>{links}</ul>")
        if p.startswith("/assets/") and re.fullmatch(r"/assets/[A-Za-z0-9._-]+", p):
            ext = os.path.splitext(p)[1].lower()
            try:
                return self.send(200, read_static(p.lstrip("/")), IMG_TYPES.get(ext, "application/octet-stream"), {"Cache-Control": "public, max-age=86400"})
            except FileNotFoundError:
                return self.send(404, "Не найдено", "text/plain; charset=utf-8")
        if p == "/book":
            return self.send(200, read_static("book.html").decode("utf-8"))
        if p == "/thanks":
            return self.send(200, read_static("thanks.html").decode("utf-8"))
        if p.startswith("/fonts/") and re.fullmatch(r"/fonts/[a-z0-9.-]+\.woff2", p):
            try:
                return self.send(200, read_static(p.lstrip("/")), "font/woff2", {"Cache-Control": "public, max-age=604800"})
            except FileNotFoundError:
                return self.send(404, "Не найдено", "text/plain; charset=utf-8")
        if p == "/ui.css":
            css = read_static("ui.css") + b"\n" + read_static(f"theme-{self.var or '1'}.css")
            return self.send(200, css, "text/css; charset=utf-8")
        if p.startswith("/api/"):
            return self.api_get(p, q)
        if p.startswith("/admin"):
            return self.admin_get(p, q)
        self.send(404, "Не найдено", "text/plain; charset=utf-8")

    def do_POST(self):
        self.var, p = split_variant(urlparse(self.path).path)
        if p == "/api/visits":
            return self.api_create_visit()
        if p == "/api/thanks/accept":
            return self.api_accept_gift()
        if p.startswith("/admin"):
            return self.admin_post(p)
        self.send(404, "Не найдено", "text/plain; charset=utf-8")

    # ---------- публичный API ----------
    def api_get(self, p, q):
        con = db.connect()
        try:
            if p == "/api/public":
                s = {r["key"]: r["value"] for r in con.execute("SELECT * FROM settings")}
                return self.json({"title": s.get("title"), "tagline": s.get("tagline"), "messenger_url": s.get("messenger_url")})
            if p == "/api/branches":
                return self.json([dict(r) for r in con.execute("SELECT id,name,address FROM branches WHERE active=1")])
            if p == "/api/masters":
                rows = con.execute("SELECT id,name FROM masters WHERE active=1 AND branch_id=?", (q.get("branch", 0),)).fetchall()
                out = []
                for r in rows:
                    day, slots = db.nearest(con, r["id"])
                    out.append({"id": r["id"], "name": r["name"], "nearest_date": day, "nearest_slots": slots})
                return self.json(out)
            if p == "/api/services":
                rows = con.execute("SELECT s.id,s.name,s.price,s.duration FROM services s JOIN master_services ms ON ms.service_id=s.id "
                                   "WHERE s.active=1 AND ms.master_id=?", (q.get("master", 0),)).fetchall()
                return self.json([dict(r) for r in rows])
            if p == "/api/slots":
                dur = self.duration_for(con, q.get("master"), q.get("services", ""))
                if not dur or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", q.get("date", "")):
                    return self.json({"error": "bad request"}, 400)
                return self.json({"slots": db.free_slots(con, int(q["master"]), q["date"], dur), "duration": dur})
            if p == "/api/code":
                phone = re.sub(r"[\s()-]", "", q.get("phone", ""))
                try:
                    ids = [int(x) for x in q.get("services", "").split(",") if x]
                except ValueError:
                    return self.json({"ok": False, "message": "Проверьте услуги"}, 400)
                if not ids:
                    return self.json({"ok": False, "message": "Сначала выберите услуги"})
                rows = self.service_rows(con, ids)
                cl = con.execute("SELECT id FROM clients WHERE phone=?", (phone,)).fetchone() if PHONE_RE.match(phone) else None
                ok, disc, msg, g, field = offers.apply_code(con, q.get("code", ""), cl["id"] if cl else None, rows, sum(r["price"] for r in rows))
                return self.json({"ok": ok, "discount": disc, "message": msg})
            if p == "/api/thanks":
                return self.json(*self.thanks_data(con, q.get("v"), q.get("t")))
            self.json({"error": "not found"}, 404)
        finally:
            con.close()

    @staticmethod
    def duration_for(con, master, services_csv):
        try:
            ids = [int(x) for x in services_csv.split(",") if x]
            master = int(master)
        except (TypeError, ValueError):
            return None
        if not ids:
            return None
        q = ",".join("?" * len(ids))
        rows = con.execute(f"SELECT s.id,s.duration,s.price FROM services s JOIN master_services ms ON ms.service_id=s.id "
                           f"WHERE s.active=1 AND ms.master_id=? AND s.id IN ({q})", [master] + ids).fetchall()
        if len(rows) != len(set(ids)):
            return None
        return sum(r["duration"] for r in rows)

    def api_create_visit(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(min(n, 20_000)) or b"{}")
            master = int(d["master_id"]); ids = [int(x) for x in d["services"]]
            date, start = str(d["date"]), str(d["start"])
            name, phone = str(d["name"]).strip()[:80], re.sub(r"[\s()-]", "", str(d["phone"]))
        except (ValueError, KeyError, TypeError):
            return self.json({"error": "Проверьте данные формы"}, 400)
        if not d.get("consent"):
            return self.json({"error": "Нужно согласие на обработку персональных данных"}, 400)
        if not name or not PHONE_RE.match(phone):
            return self.json({"error": "Укажите имя и телефон"}, 400)
        con = db.connect()
        try:
            ok, data, code = self.create_visit(con, master, ids, date, start, name, phone, "site", "", str(d.get("code") or ""))
            if ok:
                # подарок и код для подруги выдаются только при записи с сайта
                gift, invite = offers.issue_for_visit(con, data["id"])
                con.commit()
                data["thanks"] = f"/thanks?v={data['id']}&t={data['token']}"
            return self.json({k: v for k, v in data.items() if k != "token"} if ok else data, code)
        finally:
            con.close()

    def thanks_data(self, con, vid, token):
        """Данные страницы благодарности. Доступ по токену визита, а не по номеру записи."""
        try:
            v = con.execute("SELECT v.*, m.name AS master, b.name AS branch, b.address FROM visits v JOIN masters m ON m.id=v.master_id "
                            "JOIN branches b ON b.id=v.branch_id WHERE v.id=?", (int(vid),)).fetchone()
        except (TypeError, ValueError):
            v = None
        if not v or not token or not secrets.compare_digest(v["token"] or "", token):
            return {"error": "Запись не найдена"}, 404
        svc = [r["name"] for r in con.execute("SELECT s.name FROM visit_services vs JOIN services s ON s.id=vs.service_id WHERE vs.visit_id=?", (v["id"],))]

        def pack(g):
            if not g:
                return None
            o = con.execute("SELECT * FROM offers WHERE id=?", (g["offer_id"],)).fetchone()
            return {"code": g["code"], "title": o["title"], "body": o["body"], "kind": o["rule"], "expires": g["expires_at"][:10],
                    "accepted": bool(g["accepted"]), "max_rub": o["max_rub"], "value": o["value"], "uses_left": max(0, offers.REFERRAL_MAX_USES - g["uses"])}
        gift = con.execute("SELECT g.* FROM grants g JOIN offers o ON o.id=g.offer_id WHERE g.visit_id=? AND g.client_id=? "
                           "AND o.rule IN ('cross_new_target','welcome')", (v["id"], v["client_id"])).fetchone()
        invite = con.execute("SELECT g.* FROM grants g JOIN offers o ON o.id=g.offer_id WHERE g.client_id=? AND o.rule='referral_invite'",
                             (v["client_id"],)).fetchone()
        return {"visit": {"id": v["id"], "date": v["date"], "start": v["start"], "master": v["master"], "branch": v["branch"],
                          "address": v["address"], "total": v["total"], "discount": v["discount"], "services": svc, "name": v["client_name"]},
                "gift": pack(gift), "invite": pack(invite)}, 200

    def api_accept_gift(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(min(n, 2_000)) or b"{}")
            vid, token, code = int(d["v"]), str(d["t"]), str(d["code"]).upper()
        except (ValueError, KeyError, TypeError):
            return self.json({"error": "bad request"}, 400)
        con = db.connect()
        try:
            data, status = self.thanks_data(con, vid, token)
            if status != 200:
                return self.json(data, status)
            con.execute("UPDATE grants SET accepted=1 WHERE code=? AND client_id=(SELECT client_id FROM visits WHERE id=?)", (code, vid))
            con.commit()
            return self.json({"ok": True})
        finally:
            con.close()

    def service_rows(self, con, ids):
        return [dict(r) for r in con.execute(
            f"SELECT id,name,price,category,cost FROM services WHERE id IN ({','.join('?' * len(ids))})", ids)]

    def create_visit(self, con, master, ids, date, start, name, phone, source, comment, promo=""):
        """Общее создание записи в СВОЕЙ базе (сайт и админка). В YClients ничего не уходит."""
        con.execute("BEGIN IMMEDIATE")
        dur = self.duration_for(con, master, ",".join(map(str, ids)))
        if not dur or start not in db.free_slots(con, master, date, dur):
            con.execute("ROLLBACK")
            return False, {"error": "Это время занято или услуги не подходят мастеру"}, 409
        m = con.execute("SELECT branch_id FROM masters WHERE id=? AND active=1", (master,)).fetchone()
        rows = self.service_rows(con, ids)
        total = sum(r["price"] for r in rows)
        cid = db.upsert_client(con, name, phone)
        ok, discount, msg, grant, field = offers.apply_code(con, promo, cid, rows, total)
        if not ok:
            con.execute("ROLLBACK")
            return False, {"error": msg}, 400
        token = secrets.token_urlsafe(12)
        cur = con.execute("INSERT INTO visits(branch_id,master_id,client_id,client_name,client_phone,date,start,duration,total,discount,status,source,comment,token,grant_code,ref_code,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,'waiting',?,?,?,?,?,?)",
                          (m["branch_id"], master, cid, name, phone, date, start, dur, total - discount, discount, source, comment[:300], token,
                           grant["code"] if grant and field == "grant_code" else "", grant["code"] if grant and field == "ref_code" else "",
                           datetime.now().isoformat(timespec="seconds")))
        con.executemany("INSERT INTO visit_services VALUES(?,?)", [(cur.lastrowid, i) for i in ids])
        if grant:
            offers.mark_used(con, grant, con.execute("SELECT rule FROM offers WHERE id=?", (grant["offer_id"],)).fetchone()["rule"], cur.lastrowid, discount)
        con.commit()
        return True, {"ok": True, "id": cur.lastrowid, "total": total - discount, "discount": discount, "token": token}, 200

    # ---------- админка ----------
    def admin_get(self, p, q):
        if p == "/admin/login":
            return self.send(200, self.login_page(""))
        sid = self.session()
        if not sid:
            return self.redirect("/admin/login")
        csrf = SESSIONS[sid]
        con = db.connect()
        try:
            if p in ("/admin", "/admin/", "/admin/journal"):
                return self.send(200, layout("Журнал", self.journal_page(con, csrf, q)))
            if p == "/admin/overview":
                return self.send(200, layout("Обзор", owner.overview_page(con, q)))
            if p == "/admin/finance":
                return self.send(200, layout("Финансы", owner.finance_page(con, csrf, q)))
            if p == "/admin/analytics":
                return self.send(200, layout("Аналитика", owner.analytics_page(con, q)))
            if p == "/admin/offers":
                return self.send(200, layout("Допродажи", owner.offers_page(con, csrf)))
            if p == "/admin/visits":
                return self.send(200, layout("Записи", self.visits_page(con, csrf, q)))
            if p == "/admin/clients":
                return self.send(200, layout("Клиенты", self.clients_page(con, q)))
            if p == "/admin/client":
                return self.send(200, layout("Клиент", self.client_page(con, csrf, q)))
            if p == "/admin/catalog":
                return self.send(200, layout("Каталог", self.catalog_page(con, csrf)))
            if p == "/admin/settings":
                return self.send(200, layout("Настройки", self.settings_page(con, csrf)))
            if p == "/admin/sync":
                return self.send(200, layout("YClients", self.sync_page(con)))
            self.send(404, layout("404", "<p>Не найдено</p>"))
        finally:
            con.close()

    def login_page(self, err):
        msg = f'<p class="msg" role="alert">{esc(err)}</p>' if err else ""
        return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход · Кудри</title><link rel="stylesheet" href="/ui.css"></head><body class="pub">
<main style="max-width:440px;margin:10vh auto 0;padding:0 20px"><img src="/assets/kudri-mark-graphite.png" alt="Кудри" style="width:96px;height:96px"><p class="lead" style="margin-top:14px">Вход в рабочее место администратора</p>
<form method="post" action="/admin/login" style="margin-top:32px;display:grid;gap:12px">
<label class="field"><span>Логин</span><input name="login" autocomplete="username" required autofocus></label>
<label class="field"><span>Пароль</span><input name="password" type="password" autocomplete="current-password" required></label>
<button class="go" style="margin-top:4px">Войти</button>{msg}</form></main><footer style="text-align:center">Только для сотрудников салона</footer></body></html>"""

    def admin_post(self, p):
        f, _ = self.form()
        if p == "/admin/login":
            con = db.connect()
            row = con.execute("SELECT * FROM admins WHERE login=?", (f.get("login", ""),)).fetchone()
            con.close()
            if row and secrets.compare_digest(db.hash_pw(f.get("password", ""), row["salt"]), row["pw_hash"]):
                tok = secrets.token_urlsafe(24)
                SESSIONS[tok] = secrets.token_urlsafe(16)
                return self.redirect("/admin", {"Set-Cookie": f"sid={tok}; HttpOnly; SameSite=Strict; Path=/"})
            return self.send(401, self.login_page("Неверный логин или пароль"))
        sid = self.session()
        if not sid or not secrets.compare_digest(f.get("csrf", ""), SESSIONS[sid]) and p != "/admin/logout":
            return self.send(403, "Запрещено", "text/plain; charset=utf-8")
        if p == "/admin/logout":
            SESSIONS.pop(sid, None)
            return self.redirect("/admin/login", {"Set-Cookie": "sid=; Max-Age=0; Path=/"})
        con = db.connect()
        try:
            if p == "/admin/visit/cancel":
                con.execute("UPDATE visits SET status='cancelled' WHERE id=?", (int(f["id"]),))
            elif p == "/admin/visit/status":
                if f["status"] not in db.STATUSES:
                    raise ValueError("status")
                con.execute("UPDATE visits SET status=? WHERE id=?", (f["status"], int(f["id"])))
                if f["status"] == "arrived":
                    offers.reward_for_arrived(con, int(f["id"]))
            elif p == "/admin/visit/add":
                phone = re.sub(r"[\s()-]", "", f["phone"])
                ids = [int(x) for x in f["services"].split(",") if x.strip()]
                if not PHONE_RE.match(phone) or not f["name"].strip() or not ids:
                    raise ValueError("form")
                ok, data, code = self.create_visit(con, int(f["master_id"]), ids, f["date"], f["start"],
                                                   f["name"].strip()[:80], phone, "admin", f.get("comment", ""))
                if not ok:
                    con.close()
                    return self.send(code, layout("Ошибка", f"<p>{esc(data['error'])}</p><p><a href='javascript:history.back()'>Назад</a></p>"))
            elif p == "/admin/pay":
                vid, aid, amt = int(f["visit_id"]), int(f["account_id"]), int(f["amount"])
                due = con.execute("SELECT v.total - COALESCE((SELECT SUM(amount) FROM payments WHERE visit_id=v.id),0) AS d FROM visits v "
                                  "WHERE v.id=? AND v.status='arrived'", (vid,)).fetchone()
                if not due or amt <= 0 or amt > due["d"]:
                    raise ValueError("amount")
                con.execute("INSERT INTO payments(visit_id,account_id,amount,paid_at) VALUES(?,?,?,?)",
                            (vid, aid, amt, datetime.now().isoformat(timespec="seconds")))
            elif p == "/admin/master/pay":
                pc = int(f["pct"])
                if not 0 <= pc <= 100:
                    raise ValueError("pct")
                con.execute("UPDATE masters SET pay_pct=? WHERE id=?", (pc, int(f["id"])))
            elif p == "/admin/expense/add":
                amt = int(f["amount"])
                if amt <= 0 or f["category"] not in owner.EXPENSE_CATS:
                    raise ValueError("expense")
                datetime.strptime(f["date"], "%Y-%m-%d")
                con.execute("INSERT INTO expenses(date,category,amount,note) VALUES(?,?,?,?)", (f["date"], f["category"], amt, f.get("note", "")[:120]))
            elif p == "/admin/expense/del":
                con.execute("DELETE FROM expenses WHERE id=?", (int(f["id"]),))
            elif p == "/admin/offer/save":
                vals = [int(f[k]) for k in ("value", "valid_days", "max_rub", "budget")]
                if not (0 <= vals[0] <= 100 and vals[1] >= 1 and vals[2] >= 0 and vals[3] >= 0):
                    raise ValueError("offer")
                con.execute("UPDATE offers SET value=?, valid_days=?, max_rub=?, budget=? WHERE id=?", (*vals, int(f["id"])))
            elif p == "/admin/offers/settings":
                m = int(f["min_markup_pct"])
                if not 0 <= m <= 300:
                    raise ValueError("markup")
                con.execute("INSERT OR REPLACE INTO settings VALUES('min_markup_pct',?)", (str(m),))
            elif p == "/admin/client/save":
                con.execute("UPDATE clients SET name=?, email=?, note=?, tags=?, no_sms=? WHERE id=?",
                            (f["name"][:80], f.get("email", "")[:120], f.get("note", "")[:1000], f.get("tags", "")[:200],
                             1 if f.get("no_sms") else 0, int(f["id"])))
            elif p == "/admin/toggle":
                tbl = {"branches": "branches", "masters": "masters", "services": "services", "offers": "offers"}[f["table"]]
                con.execute(f"UPDATE {tbl} SET active=1-active WHERE id=?", (int(f["id"]),))
            elif p == "/admin/branch/add":
                con.execute("INSERT INTO branches(name,address) VALUES(?,?)", (f["name"][:80], f["address"][:120]))
            elif p == "/admin/master/add":
                cur = con.execute("INSERT INTO masters(branch_id,name) VALUES(?,?)", (int(f["branch_id"]), f["name"][:80]))
                for s in con.execute("SELECT id FROM services"):
                    con.execute("INSERT INTO master_services VALUES(?,?)", (cur.lastrowid, s["id"]))
                for wd in range(0, 6):
                    con.execute("INSERT INTO schedules VALUES(?,?,?,?)", (cur.lastrowid, wd, "10:00", "19:00"))
            elif p == "/admin/service/add":
                cur = con.execute("INSERT INTO services(name,price,duration) VALUES(?,?,?)", (f["name"][:80], int(f["price"]), int(f["duration"])))
                for m in con.execute("SELECT id FROM masters"):
                    con.execute("INSERT INTO master_services VALUES(?,?)", (m["id"], cur.lastrowid))
            elif p == "/admin/settings":
                for k in ("title", "tagline", "messenger_url"):
                    con.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, f.get(k, "")[:200]))
            con.commit()
        except (KeyError, ValueError):
            con.close()
            return self.send(400, "Проверьте поля формы", "text/plain; charset=utf-8")
        con.close()
        self.redirect(self.headers.get("Referer", "/admin").replace("http://" + (self.headers.get("Host") or ""), "") or "/admin")

    # ---------- страницы админки ----------
    def post_btn(self, action, csrf, fields, label, ghost=True):
        hidden = "".join(f"<input type=hidden name='{esc(k)}' value='{esc(str(v))}'>" for k, v in fields.items())
        cls = " class=ghost" if ghost else ""
        return f"<form class=inline method=post action='{action}'><input type=hidden name=csrf value='{csrf}'>{hidden}<button{cls}>{esc(label)}</button></form>"

    def status_label(self, status):
        return f"<span class='st {esc(status)}'>{esc(db.STATUSES.get(status, status))}</span>"

    def status_form(self, csrf, r):
        opts = "".join(f"<option value={k}{' selected' if k == r['status'] else ''}>{esc(v)}</option>" for k, v in db.STATUSES.items())
        return (f"<form class=inline method=post action=/admin/visit/status><input type=hidden name=csrf value='{csrf}'>"
                f"<input type=hidden name=id value={r['id']}><select name=status aria-label='Статус посещения' onchange='this.form.submit()'>{opts}</select></form>")

    def visits_page(self, con, csrf, q):
        day = q.get("date", "")
        sql = ("SELECT v.*, m.name AS master, b.name AS branch FROM visits v JOIN masters m ON m.id=v.master_id "
               "JOIN branches b ON b.id=v.branch_id ")
        rows = con.execute(sql + ("WHERE v.date=? " if day else "") + "ORDER BY v.date DESC, v.start DESC LIMIT 200", (day,) if day else ()).fetchall()
        tr = "".join(
            f"<tr><td>{esc(r['date'])} <b>{esc(r['start'])}</b></td><td>{esc(r['branch'])}</td><td>{esc(r['master'])}</td>"
            f"<td><a href='/admin/client?id={r['client_id'] or 0}'>{esc(r['client_name'])}</a><br><span class=muted>{esc(r['client_phone'])}</span></td>"
            f"<td>{r['total']} ₽</td><td>{self.status_label(r['status'])}<br><span class=muted>синхронизация: {esc(r['sync_state'])}</span></td>"
            f"<td>{'' if r['status'] == 'cancelled' else self.post_btn('/admin/visit/cancel', csrf, {'id': r['id']}, 'Отменить')}</td></tr>"
            for r in rows) or "<tr><td colspan=7 class=empty>Записей нет</td></tr>"
        return (f"<h2>Записи</h2><form class=row method=get><input type=date name=date value='{esc(day)}' aria-label='Дата'>"
                f"<button>Показать</button></form>"
                f"<table><thead><tr><th>Когда</th><th>Филиал</th><th>Мастер</th><th>Клиент</th><th>Сумма</th><th>Статус</th><th></th></tr></thead><tbody>{tr}</tbody></table>")

    def journal_page(self, con, csrf, q):
        day = q.get("date") or datetime.now().strftime("%Y-%m-%d")
        branch = q.get("branch") or ""
        branches = con.execute("SELECT * FROM branches WHERE active=1").fetchall()
        bopts = "".join(f"<option value={b['id']}{' selected' if str(b['id']) == branch else ''}>{esc(b['name'])}</option>" for b in branches)
        masters = con.execute("SELECT * FROM masters WHERE active=1" + (" AND branch_id=?" if branch else ""), (branch,) if branch else ()).fetchall()
        cols = ""
        for m in masters:
            vs = con.execute("SELECT * FROM visits WHERE master_id=? AND date=? ORDER BY start", (m["id"], day)).fetchall()
            rows = "".join(
                f"<div class='appt{' off' if v['status'] == 'cancelled' else ''}'><time>{esc(v['start'])}</time><div>"
                f"<a href='/admin/client?id={v['client_id'] or 0}'>{esc(v['client_name'])}</a>"
                f"<div class=meta>{esc(v['client_phone'])} · {v['duration']} мин · {v['total']} ₽ · {'сайт' if (v['source'] or 'site') == 'site' else 'админ'}</div>"
                f"{self.status_form(csrf, v)}</div></div>" for v in vs) or "<div class=empty>Свободно</div>"
            cols += f"<section><h3>{esc(m['name'])}</h3>{rows}</section>"
        allm = con.execute("SELECT m.id, m.name, b.name AS b FROM masters m JOIN branches b ON b.id=m.branch_id WHERE m.active=1").fetchall()
        mopts = "".join(f"<option value={m['id']}>{esc(m['name'])} · {esc(m['b'])}</option>" for m in allm)
        sopts = "".join(f"<label class=consent style='margin:0 16px 6px 0'><input type=checkbox name=svc value={s['id']}> {esc(s['name'])}</label>"
                        for s in con.execute("SELECT * FROM services WHERE active=1"))
        return (f"<h2>Журнал</h2><form class=row method=get><input type=date name=date value='{esc(day)}' aria-label='Дата'> "
                f"<select name=branch aria-label='Филиал'><option value=''>Все филиалы</option>{bopts}</select> <button>Показать</button></form>"
                f"<div class=board>{cols or '<div class=empty>Нет мастеров</div>'}</div>"
                f"<div class=card><h3>Записать клиента</h3><form method=post action=/admin/visit/add onsubmit=\"this.services.value=[...this.querySelectorAll('[name=svc]:checked')].map(x=>x.value).join(',')\">"
                f"<input type=hidden name=csrf value='{csrf}'><input type=hidden name=services>"
                f"<div class=row><select name=master_id aria-label='Мастер'>{mopts}</select><input type=date name=date value='{esc(day)}' required aria-label='Дата'>"
                f"<input name=start placeholder='Время, 10:00' pattern='\\d\\d:\\d\\d' required size=10></div>"
                f"<div class=row>{sopts}</div>"
                f"<div class=row><input name=name placeholder=Имя required> <input name=phone placeholder='Телефон +7…' required> <input name=comment placeholder=Комментарий size=30></div>"
                f"<button>Записать</button></form></div>")

    def clients_page(self, con, q):
        s = q.get("q", "").strip()
        like = f"%{s}%"
        rows = con.execute(
            "SELECT c.*, COUNT(v.id) AS n, COALESCE(SUM(CASE WHEN v.status='arrived' THEN v.total END),0) AS spent, "
            "SUM(v.status='no_show') AS ns FROM clients c LEFT JOIN visits v ON v.client_id=c.id "
            + ("WHERE c.name LIKE ? OR c.phone LIKE ? " if s else "") + "GROUP BY c.id ORDER BY c.id DESC LIMIT 200",
            (like, like) if s else ()).fetchall()
        tr = "".join(f"<tr><td><a href='/admin/client?id={r['id']}'>{esc(r['name'])}</a></td><td>{esc(r['phone'])}</td><td>{esc(r['tags'])}</td>"
                     f"<td>{r['n']}</td><td>{r['spent']} ₽</td><td>{r['ns'] or 0}</td></tr>" for r in rows) or "<tr><td colspan=6 class=empty>Клиентов пока нет. Они появятся после первой записи.</td></tr>"
        return (f"<h2>Клиенты</h2><form class=row method=get><input name=q value='{esc(s)}' placeholder='Имя или телефон' aria-label='Поиск'> <button>Найти</button></form>"
                f"<table><thead><tr><th>Имя</th><th>Телефон</th><th>Метки</th><th>Визитов</th><th>Оплачено</th><th>Неявок</th></tr></thead><tbody>{tr}</tbody></table>")

    def client_page(self, con, csrf, q):
        try:
            c = con.execute("SELECT * FROM clients WHERE id=?", (int(q.get("id", 0)),)).fetchone()
        except ValueError:
            c = None
        if not c:
            return "<h2>Клиент не найден</h2><p><a href=/admin/clients>К списку клиентов</a></p>"
        hist = con.execute("SELECT v.*, m.name AS master FROM visits v JOIN masters m ON m.id=v.master_id WHERE v.client_id=? ORDER BY v.date DESC, v.start DESC", (c["id"],)).fetchall()
        tr = "".join(f"<tr><td>{esc(v['date'])} {esc(v['start'])}</td><td>{esc(v['master'])}</td><td>{v['total']} ₽</td><td>{self.status_label(v['status'])}</td></tr>" for v in hist) or "<tr><td colspan=4 class=empty>Визитов нет</td></tr>"
        return (f"<h2>{esc(c['name'])}</h2><p class=muted>{esc(c['phone'])}</p>"
                f"<form class=card method=post action=/admin/client/save style='max-width:560px'><input type=hidden name=csrf value='{csrf}'><input type=hidden name=id value={c['id']}>"
                f"<label class=field><span>Имя</span><input name=name value='{esc(c['name'])}' required></label>"
                f"<label class=field><span>Email</span><input name=email value='{esc(c['email'])}'></label>"
                f"<label class=field><span>Метки через запятую: VIP, Лояльный, Чёрный список</span><input name=tags value='{esc(c['tags'])}'></label>"
                f"<label class=field><span>Комментарий</span><textarea name=note rows=3>{esc(c['note'])}</textarea></label>"
                f"<label class=consent><input type=checkbox name=no_sms value=1{' checked' if c['no_sms'] else ''}> Не присылать сообщения</label>"
                f"<button style='margin-top:14px'>Сохранить</button></form>"
                f"<h3>История визитов</h3><table><thead><tr><th>Когда</th><th>Мастер</th><th>Сумма</th><th>Статус</th></tr></thead><tbody>{tr}</tbody></table>")

    def catalog_page(self, con, csrf):
        def rows(tbl, cols):
            out = ""
            for r in con.execute(f"SELECT * FROM {tbl}"):
                state = "активен" if r["active"] else "скрыт"
                out += ("<tr>" + "".join(f"<td>{esc(str(r[c]))}</td>" for c in cols) +
                        f"<td class=muted>{state}</td><td>{self.post_btn('/admin/toggle', csrf, {'table': tbl, 'id': r['id']}, 'Скрыть' if r['active'] else 'Показать')}</td></tr>")
            return out
        opts = "".join(f"<option value={b['id']}>{esc(b['name'])}</option>" for b in con.execute("SELECT * FROM branches"))
        c = f"<input type=hidden name=csrf value='{csrf}'>"
        return (f"<h2>Каталог</h2>"
                f"<div class=card><h3>Филиалы</h3><table><tbody>{rows('branches', ['name', 'address'])}</tbody></table>"
                f"<form class=row method=post action=/admin/branch/add style='margin:14px 0 0'>{c}<input name=name placeholder=Название required> <input name=address placeholder=Адрес required> <button>Добавить филиал</button></form></div>"
                f"<div class=card><h3>Мастера</h3><table><tbody>{rows('masters', ['name', 'branch_id'])}</tbody></table>"
                f"<form class=row method=post action=/admin/master/add style='margin:14px 0 0'>{c}<input name=name placeholder=Имя required> <select name=branch_id aria-label='Филиал'>{opts}</select> <button>Добавить мастера</button></form></div>"
                f"<div class=card><h3>Услуги</h3><table><tbody>{rows('services', ['name', 'price', 'duration'])}</tbody></table>"
                f"<form class=row method=post action=/admin/service/add style='margin:14px 0 0'>{c}<input name=name placeholder=Название required> <input name=price type=number min=0 placeholder='Цена, ₽' required> <input name=duration type=number min=15 step=15 placeholder='Минут' required> <button>Добавить услугу</button></form></div>")

    def settings_page(self, con, csrf):
        s = {r["key"]: r["value"] for r in con.execute("SELECT * FROM settings")}
        f = lambda k, label: f"<label class=field><span>{label}</span><input name={k} value='{esc(s.get(k, ''))}'></label>"
        return (f"<h2>Настройки сайта</h2><form class=card method=post action=/admin/settings style='max-width:560px'><input type=hidden name=csrf value='{csrf}'>"
                f"{f('title', 'Название')}{f('tagline', 'Подпись')}{f('messenger_url', 'Ссылка на мессенджер (https://…). Пусто: кнопка скрыта')}<button>Сохранить</button></form>")

    def sync_page(self, con):
        sy = YClientsSync()
        pend = sy.dry_run_push(con)
        rows = "".join(f"<tr><td>№{r['id']}</td><td>{esc(r['date'])} {esc(r['start'])}</td><td>{esc(r['branch'])}</td><td>{esc(r['master'])}</td></tr>" for r in pend) or "<tr><td colspan=4 class=empty>Нечего выгружать</td></tr>"
        return (f"<h2>Синхронизация с YClients</h2><div class=card><p>Подключение: <b>{'настроено' if sy.configured else 'не настроено'}</b></p>"
                f"<p class=muted style='margin:0'>Сейчас только предпросмотр. В YClients ничего не отправляется без явного подтверждения человека.</p></div>"
                f"<h3>Ожидают выгрузки: {len(pend)}</h3><table><thead><tr><th>Запись</th><th>Когда</th><th>Филиал</th><th>Мастер</th></tr></thead><tbody>{rows}</tbody></table>")


def main():
    db.init()
    host, port = "127.0.0.1", int(os.environ.get("PORT", "8080"))
    print(f"Кудри: http://{host}:{port}  (админка: /admin, пароль в data/admin_password.txt)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
