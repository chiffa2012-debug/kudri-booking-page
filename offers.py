"""Движок допродаж и подарков «Кудри».

Принципы (чтобы акции не «съедали» прибыль):
1. Подарок выдаётся по правилу, а не по жребию: слова «выиграли/розыгрыш» не используем (это уже лотерея по закону).
2. Один подарок на запись, и не больше одного раза на клиента по каждому предложению.
3. Подарки между собой и с другими скидками (карты, сертификаты) не суммируются: на визит один код.
4. Скидка ограничена потолком в рублях (max_rub) и наценкой: цена после скидки не ниже себестоимость × (1 + мин. наценка).
5. У каждого предложения месячный бюджет: выданные и ещё не использованные подарки считаются по потолку.
6. Награда за подругу начисляется только когда подруга пришла (статус «пришёл»), и только за нового клиента.
"""
import secrets
from datetime import datetime, timedelta

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих символов (0/O, 1/I)
REFERRAL_MAX_USES = 3


def _now():
    return datetime.now()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def setting_int(con, key, default):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    try:
        return int(r["value"]) if r else default
    except ValueError:
        return default


def new_code(con, prefix):
    while True:
        code = prefix + "-" + "".join(secrets.choice(ALPHABET) for _ in range(5))
        if not con.execute("SELECT 1 FROM grants WHERE code=?", (code,)).fetchone():
            return code


def prev_visits(con, client_id, exclude_visit=0):
    r = con.execute("SELECT COUNT(*) AS n FROM visits WHERE client_id=? AND id!=? AND status NOT IN ('cancelled','no_show')",
                    (client_id, exclude_visit)).fetchone()
    return r["n"]


def had_category(con, client_id, cat, exclude_visit=0):
    r = con.execute("SELECT 1 FROM visits v JOIN visit_services vs ON vs.visit_id=v.id JOIN services s ON s.id=vs.service_id "
                    "WHERE v.client_id=? AND s.category=? AND v.id!=? AND v.status NOT IN ('cancelled','no_show') LIMIT 1",
                    (client_id, cat, exclude_visit)).fetchone()
    return bool(r)


def markup_ok(price_after, cost, min_pct):
    return cost <= 0 or price_after >= cost * (1 + min_pct / 100)


def committed(con, offer):
    """Сколько бюджета уже занято: использованные скидки + невостребованные подарки по потолку."""
    used = con.execute("SELECT COALESCE(SUM(discount),0) AS s FROM grants WHERE offer_id=? AND status='used'", (offer["id"],)).fetchone()["s"]
    open_n = con.execute("SELECT COUNT(*) AS n FROM grants WHERE offer_id=? AND status='issued' AND expires_at>?",
                         (offer["id"], _iso(_now()))).fetchone()["n"]
    return used + open_n * offer["max_rub"]


def budget_left(con, offer):
    return offer["budget"] <= 0 or committed(con, offer) + offer["max_rub"] <= offer["budget"]


def create_grant(con, offer, client_id, visit_id):
    code = new_code(con, "KD" if offer["rule"] != "referral_invite" else "FR")
    now = _now()
    cur = con.execute("INSERT INTO grants(offer_id,client_id,visit_id,code,issued_at,expires_at) VALUES(?,?,?,?,?,?)",
                      (offer["id"], client_id, visit_id, code, _iso(now), _iso(now + timedelta(days=offer["valid_days"]))))
    return con.execute("SELECT * FROM grants WHERE id=?", (cur.lastrowid,)).fetchone()


def _target_service_ok(con, offer, min_pct):
    """Есть ли активная услуга в целевой категории, на которой скидка не уводит цену ниже допустимой."""
    rows = con.execute("SELECT price, cost FROM services WHERE active=1 AND category=?", (offer["target_cat"],)).fetchall()
    for r in rows:
        disc = min(round(r["price"] * offer["value"] / 100), offer["max_rub"])
        if markup_ok(r["price"] - disc, r["cost"], min_pct):
            return True
    return False


def issue_for_visit(con, visit_id):
    """После записи выдаёт клиенту подарок (по правилам) и код для подруги. Возвращает (gift, invite)."""
    v = con.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
    if not v or not v["client_id"]:
        return None, None
    cid = v["client_id"]
    cats = {r["category"] for r in con.execute(
        "SELECT DISTINCT s.category FROM visit_services vs JOIN services s ON s.id=vs.service_id WHERE vs.visit_id=?", (visit_id,)) if r["category"]}
    min_pct = setting_int(con, "min_markup_pct", 20)
    gift = None
    for o in con.execute("SELECT * FROM offers WHERE active=1 AND rule IN ('cross_new_target','welcome') ORDER BY priority DESC, id"):
        if con.execute("SELECT 1 FROM grants WHERE offer_id=? AND client_id=?", (o["id"], cid)).fetchone():
            continue
        if o["rule"] == "cross_new_target":
            if o["trigger_cat"] not in cats or o["target_cat"] in cats or had_category(con, cid, o["target_cat"], visit_id):
                continue
            if not _target_service_ok(con, o, min_pct):
                continue
        elif o["rule"] == "welcome":
            if prev_visits(con, cid, visit_id) > 0:
                continue
        if not budget_left(con, o):
            continue
        gift = create_grant(con, o, cid, visit_id)
        break
    invite = None
    inv_offer = con.execute("SELECT * FROM offers WHERE active=1 AND rule='referral_invite' LIMIT 1").fetchone()
    if inv_offer:
        invite = con.execute("SELECT * FROM grants WHERE offer_id=? AND client_id=?", (inv_offer["id"], cid)).fetchone() \
                 or create_grant(con, inv_offer, cid, visit_id)
    return gift, invite


def apply_code(con, code, client_id, services, total):
    """Проверка кода при записи. services: список dict(id, name, price, category, cost).
    Возвращает (ok, discount, message, grant_row, field) где field: 'grant_code' или 'ref_code'."""
    code = (code or "").strip().upper()
    if not code:
        return True, 0, "", None, ""
    g = con.execute("SELECT * FROM grants WHERE code=?", (code,)).fetchone()
    if not g:
        return False, 0, "Код не найден. Проверьте, что он введён без ошибок.", None, ""
    o = con.execute("SELECT * FROM offers WHERE id=?", (g["offer_id"],)).fetchone()
    if not o["active"] and o["rule"] != "referral_reward":
        return False, 0, "Предложение закончилось.", None, ""
    if g["expires_at"] < _iso(_now()):
        return False, 0, "Срок действия кода истёк.", None, ""
    min_pct = setting_int(con, "min_markup_pct", 20)
    if o["rule"] == "referral_invite":
        if client_id and g["client_id"] == client_id:
            return False, 0, "Свой код использовать нельзя. Отправьте его подруге.", None, ""
        if client_id and prev_visits(con, client_id) > 0:
            return False, 0, "Код подруги действует только на первый визит.", None, ""
        if g["uses"] >= REFERRAL_MAX_USES:
            return False, 0, "Этот код уже использован максимальное число раз.", None, ""
        disc = min(round(total * o["value"] / 100), o["max_rub"])
        cost = sum(s["cost"] for s in services)
        while disc > 0 and not markup_ok(total - disc, cost, min_pct):
            disc -= 10
        return True, max(disc, 0), f"Скидка {max(disc, 0)} ₽ на первый визит", g, "ref_code"
    if not client_id or g["client_id"] != client_id:
        return False, 0, "Этот подарок привязан к другому номеру телефона.", None, ""
    if g["status"] != "issued":
        return False, 0, "Подарок уже использован.", None, ""
    pool = [s for s in services if not o["target_cat"] or s["category"] == o["target_cat"]]
    if not pool:
        return False, 0, f"Подарок действует на услугу: {o['target_cat']}. Добавьте её в запись.", None, ""
    best = 0
    for s in pool:
        d = min(s["price"], o["max_rub"]) if o["kind"] == "free" else min(round(s["price"] * o["value"] / 100), o["max_rub"])
        if o["kind"] == "percent" and not markup_ok(s["price"] - d, s["cost"], min_pct):
            continue
        best = max(best, d)
    if best <= 0:
        return False, 0, "К этой услуге подарок не применяется.", None, ""
    return True, best, f"Подарок: −{best} ₽", g, "grant_code"


def mark_used(con, g, o_rule, visit_id, discount):
    if o_rule == "referral_invite":
        con.execute("UPDATE grants SET uses=uses+1 WHERE id=?", (g["id"],))
    else:
        con.execute("UPDATE grants SET status='used', used_visit_id=?, discount=? WHERE id=?", (visit_id, discount, g["id"]))


def reward_for_arrived(con, visit_id):
    """Подруга пришла: начисляем награду пригласившей (один раз на визит)."""
    v = con.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
    if not v or not v["ref_code"]:
        return None
    inv = con.execute("SELECT * FROM grants WHERE code=?", (v["ref_code"],)).fetchone()
    reward = con.execute("SELECT * FROM offers WHERE active=1 AND rule='referral_reward' LIMIT 1").fetchone()
    if not inv or not reward or inv["client_id"] == v["client_id"]:
        return None
    if con.execute("SELECT 1 FROM grants WHERE offer_id=? AND visit_id=?", (reward["id"], visit_id)).fetchone():
        return None
    if not budget_left(con, reward):
        return None
    return create_grant(con, reward, inv["client_id"], visit_id)


def stats(con):
    """Сводка по каждому предложению для админки."""
    out = []
    for o in con.execute("SELECT * FROM offers ORDER BY priority DESC, id"):
        g = con.execute("SELECT COUNT(*) AS issued, COALESCE(SUM(accepted),0) AS accepted, "
                        "COALESCE(SUM(status='used'),0) AS used, COALESCE(SUM(discount),0) AS disc, COALESCE(SUM(uses),0) AS ref_uses "
                        "FROM grants WHERE offer_id=?", (o["id"],)).fetchone()
        field = "ref_code" if o["rule"] == "referral_invite" else "grant_code"
        rev = con.execute(f"SELECT COALESCE(SUM(v.total),0) AS r, COALESCE(SUM(v.discount),0) AS d FROM visits v "
                          f"WHERE v.status='arrived' AND v.{field} IN (SELECT code FROM grants WHERE offer_id=?)", (o["id"],)).fetchone()
        out.append({"offer": o, "issued": g["issued"], "accepted": g["accepted"], "used": g["used"] or g["ref_uses"],
                    "discount": rev["d"], "revenue": rev["r"], "committed": committed(con, o)})
    return out
