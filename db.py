"""База данных «Кудри»: схема, тестовые данные, расчёт свободных слотов.
Только тестовые данные. Реальных клиентов и записей здесь нет."""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA, "kudri.db")
STEP = 30  # минут

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches(id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, active INTEGER DEFAULT 1, ext_id TEXT);
CREATE TABLE IF NOT EXISTS masters(id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL REFERENCES branches(id), name TEXT NOT NULL, active INTEGER DEFAULT 1, ext_id TEXT);
CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL, duration INTEGER NOT NULL, active INTEGER DEFAULT 1, ext_id TEXT);
CREATE TABLE IF NOT EXISTS master_services(master_id INTEGER, service_id INTEGER, PRIMARY KEY(master_id, service_id));
CREATE TABLE IF NOT EXISTS schedules(master_id INTEGER, weekday INTEGER, start TEXT, end TEXT, PRIMARY KEY(master_id, weekday));
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY, branch_id INTEGER, master_id INTEGER, client_name TEXT, client_phone TEXT,
  date TEXT, start TEXT, duration INTEGER, total INTEGER,
  status TEXT DEFAULT 'new', sync_state TEXT DEFAULT 'pending', ext_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS visit_services(visit_id INTEGER, service_id INTEGER);
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, email TEXT DEFAULT '',
  note TEXT DEFAULT '', tags TEXT DEFAULT '', no_sms INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS offers(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT NOT NULL, body TEXT DEFAULT '', kind TEXT NOT NULL,
  value INTEGER DEFAULT 0, trigger_cat TEXT DEFAULT '', target_cat TEXT DEFAULT '', rule TEXT NOT NULL,
  valid_days INTEGER DEFAULT 30, max_rub INTEGER DEFAULT 0, budget INTEGER DEFAULT 0, priority INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS grants(
  id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, client_id INTEGER NOT NULL, visit_id INTEGER, code TEXT UNIQUE NOT NULL,
  status TEXT DEFAULT 'issued', issued_at TEXT, expires_at TEXT, accepted INTEGER DEFAULT 0, used_visit_id INTEGER,
  discount INTEGER DEFAULT 0, uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'bank');
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL, account_id INTEGER NOT NULL, amount INTEGER NOT NULL, paid_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, date TEXT NOT NULL, category TEXT NOT NULL, amount INTEGER NOT NULL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, login TEXT UNIQUE, salt TEXT, pw_hash TEXT);
"""


def connect():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


# Статусы посещения: отдельными значениями (в YClients один -1 смешивает неявку и отмену)
STATUSES = {"waiting": "Ожидание", "confirmed": "Подтвердил", "arrived": "Пришёл",
            "no_show": "Не пришёл", "cancelled": "Отменена"}


def migrate(con):
    cols = {r["name"] for r in con.execute("PRAGMA table_info(visits)")}
    if "client_id" not in cols:
        con.execute("ALTER TABLE visits ADD COLUMN client_id INTEGER")
    if "source" not in cols:
        con.execute("ALTER TABLE visits ADD COLUMN source TEXT DEFAULT 'site'")
    if "comment" not in cols:
        con.execute("ALTER TABLE visits ADD COLUMN comment TEXT DEFAULT ''")
    con.execute("UPDATE visits SET status='waiting' WHERE status='new'")
    vcols = {r["name"] for r in con.execute("PRAGMA table_info(visits)")}
    for col, ddl in (("token", "TEXT"), ("discount", "INTEGER DEFAULT 0"), ("grant_code", "TEXT DEFAULT ''"), ("ref_code", "TEXT DEFAULT ''")):
        if col not in vcols:
            con.execute(f"ALTER TABLE visits ADD COLUMN {col} {ddl}")
    scols = {r["name"] for r in con.execute("PRAGMA table_info(services)")}
    mcols = {r["name"] for r in con.execute("PRAGMA table_info(masters)")}
    if "pay_pct" not in mcols:
        con.execute("ALTER TABLE masters ADD COLUMN pay_pct INTEGER DEFAULT 50")
    if not con.execute("SELECT 1 FROM accounts").fetchone():
        # кассы как в YClients у салона: наличные, терминал, QR, мобильный банк
        con.executemany("INSERT INTO accounts(name,kind) VALUES(?,?)",
                        [("Наличные", "cash"), ("Терминал (карта)", "card"), ("Сбер QR", "qr"), ("Мобильный банк", "bank")])
    for col, ddl in (("category", "TEXT DEFAULT ''"), ("cost", "INTEGER DEFAULT 0")):
        if col not in scols:
            con.execute(f"ALTER TABLE services ADD COLUMN {col} {ddl}")


def seed_offers(con):
    """Тестовый каталог категорий и сетка допродаж. Реальные категории и цены придут из YClients."""
    if con.execute("SELECT 1 FROM offers").fetchone():
        return
    for sid, cat, cost in ((1, "Стрижки", 500), (2, "Стрижки", 250), (3, "Окрашивание", 1200)):
        con.execute("UPDATE services SET category=?, cost=? WHERE id=? AND category=''", (cat, cost, sid))
    extra = [(4, "Тестовый маникюр", 1800, 60, "Маникюр", 500), (5, "Тестовая коррекция бровей", 900, 30, "Брови", 250),
             (6, "Тестовое ламинирование ресниц", 2500, 60, "Ресницы", 700), (7, "Тестовый педикюр", 2200, 75, "Педикюр", 600)]
    for sid, name, price, dur, cat, cost in extra:
        if not con.execute("SELECT 1 FROM services WHERE id=?", (sid,)).fetchone():
            con.execute("INSERT INTO services(id,name,price,duration,category,cost) VALUES(?,?,?,?,?,?)", (sid, name, price, dur, cat, cost))
        for m in con.execute("SELECT id FROM masters"):
            con.execute("INSERT OR IGNORE INTO master_services VALUES(?,?)", (m["id"], sid))
    offers = [
        # code, title, body, kind, value, trigger, target, rule, days, max_rub, budget, priority
        ("cross_manicure_brows", "Подарок: −15% на коррекцию бровей",
         "Вы записались на маникюр. Скидка на коррекцию бровей действует 30 дней, если вы ещё не были у нас на бровях.",
         "percent", 15, "Маникюр", "Брови", "cross_new_target", 30, 400, 15000, 10),
        ("cross_brows_lashes", "Подарок: −10% на ламинирование ресниц",
         "Вы записались на брови. Скидка на ламинирование ресниц действует 30 дней, если вы ещё не были у нас на ресницах.",
         "percent", 10, "Брови", "Ресницы", "cross_new_target", 30, 500, 15000, 9),
        ("cross_manicure_pedicure", "Подарок: −10% на педикюр",
         "Вы записались на маникюр. Скидка на педикюр действует 30 дней, если вы ещё не были у нас на педикюре.",
         "percent", 10, "Маникюр", "Педикюр", "cross_new_target", 30, 400, 15000, 8),
        ("welcome_second_visit", "Подарок: −10% на следующий визит",
         "Спасибо, что выбрали нас впервые. Скидка на любую услугу действует 21 день.",
         "percent", 10, "", "", "welcome", 21, 500, 20000, 5),
        ("friend_invite", "Подарок подруге: −10% на первый визит",
         "Отправьте код подруге. Ей скидка 10% на первый визит, вам бесплатная услуга до 500 ₽, когда она придёт.",
         "percent", 10, "", "", "referral_invite", 60, 500, 0, 1),
        ("friend_reward", "Бесплатная услуга до 500 ₽",
         "Спасибо, что привели подругу. Бесплатная услуга до 500 ₽ действует 60 дней.",
         "free", 100, "", "", "referral_reward", 60, 500, 20000, 0),
    ]
    con.executemany("INSERT INTO offers(code,title,body,kind,value,trigger_cat,target_cat,rule,valid_days,max_rub,budget,priority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", offers)
    con.execute("INSERT OR IGNORE INTO settings VALUES('min_markup_pct','20')")


def upsert_client(con, name, phone):
    """Клиент ключуется по телефону. Возвращает id."""
    row = con.execute("SELECT id FROM clients WHERE phone=?", (phone,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute("INSERT INTO clients(name,phone,created_at) VALUES(?,?,?)",
                      (name, phone, datetime.now().isoformat(timespec="seconds")))
    return cur.lastrowid


def init():
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    if not con.execute("SELECT 1 FROM branches").fetchone():
        seed(con)
    seed_offers(con)
    if not con.execute("SELECT 1 FROM admins").fetchone():
        pw = os.environ.get("KUDRI_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        salt = secrets.token_hex(16)
        con.execute("INSERT INTO admins(login,salt,pw_hash) VALUES('admin',?,?)", (salt, hash_pw(pw, salt)))
        # пароль кладём в локальный файл, не в консоль и не в код
        with open(os.path.join(DATA, "admin_password.txt"), "w", encoding="utf-8") as f:
            f.write(pw)
    con.commit()
    con.close()


def seed(con):
    con.executemany("INSERT INTO branches(id,name,address) VALUES(?,?,?)",
                    [(1, "Тестовый филиал 1", "ул. Тестовая, 1"), (2, "Тестовый филиал 2", "ул. Образцовая, 2")])
    con.executemany("INSERT INTO masters(id,branch_id,name) VALUES(?,?,?)",
                    [(1, 1, "Мастер Тест-1"), (2, 1, "Мастер Тест-2"), (3, 2, "Мастер Тест-3")])
    con.executemany("INSERT INTO services(id,name,price,duration) VALUES(?,?,?,?)",
                    [(1, "Тестовая стрижка", 1500, 60), (2, "Тестовая укладка", 1000, 30), (3, "Тестовое окрашивание", 3500, 120)])
    for m in (1, 2, 3):
        for s in (1, 2, 3):
            con.execute("INSERT INTO master_services VALUES(?,?)", (m, s))
        for wd in range(0, 6):  # пн-сб
            con.execute("INSERT INTO schedules VALUES(?,?,?,?)", (m, wd, "10:00", "19:00"))
    con.execute("INSERT INTO settings VALUES('title','Кудри')")
    con.execute("INSERT INTO settings VALUES('tagline','Салон красоты')")
    con.execute("INSERT INTO settings VALUES('messenger_url','')")


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt(mins):
    return f"{mins // 60:02d}:{mins % 60:02d}"


def free_slots(con, master_id, date, duration):
    """Свободные времена начала для мастера на дату под услугу длительностью duration."""
    d = datetime.strptime(date, "%Y-%m-%d")
    row = con.execute("SELECT start,end FROM schedules WHERE master_id=? AND weekday=?",
                      (master_id, d.weekday())).fetchone()
    if not row:
        return []
    busy = [(hm(v["start"]), hm(v["start"]) + v["duration"]) for v in con.execute(
        "SELECT start,duration FROM visits WHERE master_id=? AND date=? AND status!='cancelled'", (master_id, date))]
    now = datetime.now()
    out = []
    t, end = hm(row["start"]), hm(row["end"])
    while t + duration <= end:
        in_past = d.date() == now.date() and t <= now.hour * 60 + now.minute
        if not in_past and d.date() >= now.date() and all(t + duration <= b0 or t >= b1 for b0, b1 in busy):
            out.append(fmt(t))
        t += STEP
    return out


def nearest(con, master_id, duration=STEP, days=30):
    today = datetime.now().date()
    for i in range(days):
        day = (today + timedelta(days=i)).isoformat()
        s = free_slots(con, master_id, day, duration)
        if s:
            return day, s[:3]
    return None, []
