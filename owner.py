"""Страницы владельца: обзор, финансы, аналитика, допродажи. Только чтение и простые формы, всё по своей базе."""
import html
from datetime import date, datetime, timedelta

import db
import offers

esc = html.escape
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
EXPENSE_CATS = ["Аренда", "Материалы", "Реклама", "Зарплата администраторов", "Коммунальные", "Налоги", "Прочее"]


def rub(n):
    return f"{int(round(n or 0)):,}".replace(",", " ") + " ₽"


def pct(a, b):
    return f"{round(100 * a / b)}%" if b else "—"


def period(q):
    d = q.get("days", "30")
    days = int(d) if d in ("1", "7", "30", "90") else 30
    end = date.today()
    return days, (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def chips(days, path):
    items = [("1", "Сегодня"), ("7", "7 дней"), ("30", "30 дней"), ("90", "90 дней")]
    return "<div class=chips>" + "".join(
        f"<a href='{path}?days={k}'{' aria-current=true' if str(days) == k else ''}>{label}</a>" for k, label in items) + "</div>"


def kpi(label, value, note=""):
    n = f"<small>{esc(note)}</small>" if note else ""
    return f"<div class=kpi><span>{esc(label)}</span><b>{value}</b>{n}</div>"


def bars(rows, fmt=rub):
    """rows: [(label, value)] -> горизонтальные полосы."""
    top = max([v for _, v in rows] + [1])
    return "".join(f"<div class=bar-row><span>{esc(str(l))}</span><i style='width:{max(2, round(100 * v / top))}%'></i><b>{fmt(v)}</b></div>" for l, v in rows) \
        or "<p class=muted>Пока нет данных за период</p>"


def capacity_minutes(con, start, end):
    """Сколько минут работы заложено в графиках мастеров за период."""
    sched = {}
    for r in con.execute("SELECT s.master_id, s.weekday, s.start, s.end FROM schedules s JOIN masters m ON m.id=s.master_id WHERE m.active=1"):
        sched.setdefault(r["weekday"], []).append(db.hm(r["end"]) - db.hm(r["start"]))
    d, e, total = date.fromisoformat(start), date.fromisoformat(end), 0
    while d <= e:
        total += sum(sched.get(d.weekday(), []))
        d += timedelta(days=1)
    return total


def overview_page(con, q):
    days, s, e = period(q)
    v = con.execute("SELECT COUNT(CASE WHEN status!='cancelled' THEN 1 END) AS booked, COALESCE(SUM(status='arrived'),0) AS arrived, "
                    "COALESCE(SUM(status='no_show'),0) AS ns, COALESCE(SUM(status='cancelled'),0) AS cx, "
                    "COALESCE(SUM(CASE WHEN status='arrived' THEN total END),0) AS rev, "
                    "COALESCE(SUM(CASE WHEN status='arrived' THEN discount END),0) AS disc, COUNT(*) AS allv "
                    "FROM visits WHERE date BETWEEN ? AND ?", (s, e)).fetchone()
    new_clients = con.execute("SELECT COUNT(*) AS n FROM clients WHERE substr(created_at,1,10) BETWEEN ? AND ?", (s, e)).fetchone()["n"]
    booked_min = con.execute("SELECT COALESCE(SUM(duration),0) AS m FROM visits WHERE date BETWEEN ? AND ? AND status!='cancelled'", (s, e)).fetchone()["m"]
    cap = capacity_minutes(con, s, e)
    avg = v["rev"] / v["arrived"] if v["arrived"] else 0
    by_master = [(r["name"], r["rev"]) for r in con.execute(
        "SELECT m.name, COALESCE(SUM(CASE WHEN v.status='arrived' THEN v.total END),0) AS rev FROM masters m LEFT JOIN visits v "
        "ON v.master_id=m.id AND v.date BETWEEN ? AND ? WHERE m.active=1 GROUP BY m.id ORDER BY rev DESC", (s, e))]
    by_cat = [(r["c"] or "Без категории", r["rev"]) for r in con.execute(
        "SELECT s.category AS c, SUM(s.price) AS rev FROM visits v JOIN visit_services vs ON vs.visit_id=v.id JOIN services s ON s.id=vs.service_id "
        "WHERE v.status='arrived' AND v.date BETWEEN ? AND ? GROUP BY s.category ORDER BY rev DESC LIMIT 8", (s, e))]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    unconf = con.execute("SELECT v.*, m.name AS master FROM visits v JOIN masters m ON m.id=v.master_id WHERE v.date=? AND v.status='waiting' ORDER BY v.start", (tomorrow,)).fetchall()
    today_left = con.execute("SELECT COUNT(*) AS n FROM visits WHERE date=? AND status IN ('waiting','confirmed')", (date.today().isoformat(),)).fetchone()["n"]
    stale = con.execute("SELECT COUNT(*) AS n FROM visits WHERE date<? AND status IN ('waiting','confirmed')", (date.today().isoformat(),)).fetchone()["n"]
    todo = ""
    if stale:
        todo += f"<li><b>{stale}</b> прошедших записей без итогового статуса. Закройте их в <a href=/admin/visits>записях</a>: иначе выручка и неявки считаются неверно.</li>"
    if unconf:
        todo += f"<li><b>{len(unconf)}</b> записей на завтра не подтверждены. Позвоните или напишите клиентам.</li>"
    unpaid = con.execute("SELECT COUNT(*) AS n FROM visits v WHERE v.status='arrived' AND v.total > COALESCE((SELECT SUM(amount) FROM payments WHERE visit_id=v.id),0)").fetchone()["n"]
    if unpaid:
        todo += f"<li><b>{unpaid}</b> визитов со статусом «пришёл» без оплаты. Внесите оплату в <a href=/admin/finance>финансах</a>.</li>"
    todo = f"<ul class=todo>{todo}</ul>" if todo else "<p class=muted>Срочных дел нет.</p>"
    return (f"<h2>Обзор</h2>{chips(days, '/admin/overview')}"
            f"<div class='kpis fill8'>{kpi('Выручка', rub(v['rev']), 'по визитам «пришёл», после скидок')}"
            f"{kpi('Визитов состоялось', v['arrived'], f'записей всего: {v['allv']}')}"
            f"{kpi('Средний чек', rub(avg))}{kpi('Неявки', pct(v['ns'], v['allv'] - v['cx']), f'отмены: {pct(v['cx'], v['allv'])}')}"
            f"{kpi('Загрузка мастеров', pct(booked_min, cap), 'занято / заложено в графиках')}{kpi('Новые клиенты', new_clients)}"
            f"{kpi('Скидок отдано', rub(v['disc']), 'акции и подарки')}{kpi('Сегодня осталось', today_left, 'записей впереди')}</div>"
            f"<div class=cols><section><h3>Нужно сделать</h3>{todo}</section>"
            f"<section><h3>Выручка по мастерам</h3>{bars(by_master)}</section>"
            f"<section><h3>Выручка по направлениям</h3>{bars(by_cat)}</section></div>")


def analytics_page(con, q):
    days, s, e = period(q)
    wd = [0] * 7
    for r in con.execute("SELECT date FROM visits WHERE date BETWEEN ? AND ? AND status!='cancelled'", (s, e)):
        wd[date.fromisoformat(r["date"]).weekday()] += 1
    clients = con.execute("SELECT COUNT(DISTINCT client_id) AS n FROM visits WHERE status='arrived'").fetchone()["n"]
    repeat = con.execute("SELECT COUNT(*) AS n FROM (SELECT client_id FROM visits WHERE status='arrived' GROUP BY client_id HAVING COUNT(*)>=2)").fetchone()["n"]
    ltv = con.execute("SELECT COALESCE(AVG(t),0) AS a FROM (SELECT SUM(total) AS t FROM visits WHERE status='arrived' GROUP BY client_id)").fetchone()["a"]
    src = [(("Сайт" if r["s"] == "site" else "Админ"), r["n"]) for r in con.execute(
        "SELECT COALESCE(source,'site') AS s, COUNT(*) AS n FROM visits WHERE date BETWEEN ? AND ? AND status!='cancelled' GROUP BY s", (s, e))]
    svc = con.execute("SELECT s.name, s.category, s.price, s.cost, COUNT(*) AS n FROM visits v JOIN visit_services vs ON vs.visit_id=v.id "
                      "JOIN services s ON s.id=vs.service_id WHERE v.status='arrived' AND v.date BETWEEN ? AND ? GROUP BY s.id ORDER BY n*s.price DESC LIMIT 12", (s, e)).fetchall()
    svc_tr = "".join(f"<tr><td>{esc(r['name'])}<br><span class=muted>{esc(r['category'] or '')}</span></td><td>{r['n']}</td><td>{rub(r['n'] * r['price'])}</td>"
                     f"<td>{pct(r['price'] - r['cost'], r['price']) if r['cost'] else '—'}</td></tr>" for r in svc) or "<tr><td colspan=4 class=empty>Нет данных за период</td></tr>"
    lost_cut = (date.today() - timedelta(days=60)).isoformat()
    lost = con.execute("SELECT c.id, c.name, c.phone, MAX(v.date) AS last, COUNT(*) AS n, SUM(v.total) AS spent FROM clients c JOIN visits v "
                       "ON v.client_id=c.id AND v.status='arrived' GROUP BY c.id HAVING last < ? ORDER BY spent DESC LIMIT 15", (lost_cut,)).fetchall()
    lost_tr = "".join(f"<tr><td><a href='/admin/client?id={r['id']}'>{esc(r['name'])}</a></td><td>{esc(r['phone'])}</td><td>{esc(r['last'])}</td><td>{r['n']}</td><td>{rub(r['spent'])}</td></tr>" for r in lost) \
        or "<tr><td colspan=5 class=empty>Нет клиентов, которые пропали больше 60 дней назад</td></tr>"
    ns = con.execute("SELECT c.id, c.name, c.phone, COUNT(*) AS n FROM visits v JOIN clients c ON c.id=v.client_id WHERE v.status='no_show' GROUP BY c.id HAVING n>=2 ORDER BY n DESC LIMIT 10").fetchall()
    ns_tr = "".join(f"<tr><td><a href='/admin/client?id={r['id']}'>{esc(r['name'])}</a></td><td>{esc(r['phone'])}</td><td>{r['n']}</td></tr>" for r in ns) or "<tr><td colspan=3 class=empty>Нет клиентов с повторными неявками</td></tr>"
    return (f"<h2>Аналитика</h2>{chips(days, '/admin/analytics')}"
            f"<div class='kpis flat'>{kpi('Клиентов с визитом', clients)}{kpi('Вернулись (2+ визита)', pct(repeat, clients))}{kpi('Средняя выручка с клиента', rub(ltv), 'за всё время')}</div>"
            f"<div class=cols><section><h3>Записи по дням недели</h3>{bars(list(zip(WD, wd)), fmt=str)}</section>"
            f"<section><h3>Откуда записи</h3>{bars(src, fmt=str)}</section></div>"
            f"<h3 style='margin-top:28px'>Услуги: что продаётся и что приносит</h3>"
            f"<table><thead><tr><th>Услуга</th><th>Раз</th><th>Выручка по прайсу</th><th>Наценка</th></tr></thead><tbody>{svc_tr}</tbody></table>"
            f"<h3 style='margin-top:28px'>Пропавшие клиенты (нет визита 60+ дней)</h3><p class=muted>Это те, кого дешевле вернуть, чем искать новых. Список по сумме трат.</p>"
            f"<table><thead><tr><th>Клиент</th><th>Телефон</th><th>Последний визит</th><th>Визитов</th><th>Потратил</th></tr></thead><tbody>{lost_tr}</tbody></table>"
            f"<h3 style='margin-top:28px'>Повторные неявки</h3>"
            f"<table><thead><tr><th>Клиент</th><th>Телефон</th><th>Неявок</th></tr></thead><tbody>{ns_tr}</tbody></table>")


def finance_page(con, csrf, q):
    days, s, e = period(q)
    c = f"<input type=hidden name=csrf value='{csrf}'>"
    accounts = con.execute("SELECT * FROM accounts").fetchall()
    aopts = "".join(f"<option value={a['id']}>{esc(a['name'])}</option>" for a in accounts)
    rev = con.execute("SELECT COALESCE(SUM(total),0) AS r FROM visits WHERE status='arrived' AND date BETWEEN ? AND ?", (s, e)).fetchone()["r"]
    by_acc = [(r["name"], r["t"]) for r in con.execute(
        "SELECT a.name, COALESCE(SUM(p.amount),0) AS t FROM accounts a LEFT JOIN payments p ON p.account_id=a.id AND substr(p.paid_at,1,10) BETWEEN ? AND ? GROUP BY a.id", (s, e))]
    received = sum(v for _, v in by_acc)
    masters = con.execute("SELECT m.id, m.name, m.pay_pct, COALESCE(SUM(CASE WHEN v.status='arrived' THEN v.total END),0) AS rev FROM masters m LEFT JOIN visits v "
                          "ON v.master_id=m.id AND v.date BETWEEN ? AND ? WHERE m.active=1 GROUP BY m.id", (s, e)).fetchall()
    pay_masters = sum(r["rev"] * r["pay_pct"] / 100 for r in masters)
    materials = con.execute("SELECT COALESCE(SUM(s.cost),0) AS c FROM visits v JOIN visit_services vs ON vs.visit_id=v.id JOIN services s ON s.id=vs.service_id "
                            "WHERE v.status='arrived' AND v.date BETWEEN ? AND ?", (s, e)).fetchone()["c"]
    exp = con.execute("SELECT COALESCE(SUM(amount),0) AS a FROM expenses WHERE date BETWEEN ? AND ?", (s, e)).fetchone()["a"]
    profit = rev - pay_masters - materials - exp
    unpaid = con.execute("SELECT v.id, v.date, v.start, v.client_name, v.total, v.total - COALESCE((SELECT SUM(amount) FROM payments WHERE visit_id=v.id),0) AS due "
                         "FROM visits v WHERE v.status='arrived' AND v.total > COALESCE((SELECT SUM(amount) FROM payments WHERE visit_id=v.id),0) ORDER BY v.date DESC LIMIT 30").fetchall()
    un_tr = "".join(f"<tr><td>{esc(r['date'])} {esc(r['start'])}</td><td>{esc(r['client_name'])}</td><td>{rub(r['due'])} из {rub(r['total'])}</td><td>"
                    f"<form class=row method=post action=/admin/pay style='margin:0'>{c}<input type=hidden name=visit_id value={r['id']}>"
                    f"<select name=account_id aria-label='Касса'>{aopts}</select><input name=amount type=number min=1 max={r['due']} value={r['due']} style='width:90px' aria-label='Сумма'>"
                    f"<button>Принять оплату</button></form></td></tr>" for r in un) if (un := unpaid) else "<tr><td colspan=4 class=empty>Все визиты «пришёл» оплачены</td></tr>"
    m_tr = "".join(f"<tr><td>{esc(r['name'])}</td><td>{rub(r['rev'])}</td><td><form class=row method=post action=/admin/master/pay style='margin:0'>{c}"
                   f"<input type=hidden name=id value={r['id']}><input name=pct type=number min=0 max=100 value={r['pay_pct']} style='width:70px' aria-label='Процент'> %"
                   f"<button class=ghost>Сохранить</button></form></td><td>{rub(r['rev'] * r['pay_pct'] / 100)}</td></tr>" for r in masters)
    eopts = "".join(f"<option>{esc(x)}</option>" for x in EXPENSE_CATS)
    ex_rows = con.execute("SELECT * FROM expenses ORDER BY date DESC, id DESC LIMIT 15").fetchall()
    ex_tr = "".join(f"<tr><td>{esc(r['date'])}</td><td>{esc(r['category'])}</td><td>{rub(r['amount'])}</td><td class=muted>{esc(r['note'])}</td><td>"
                    f"<form class=inline method=post action=/admin/expense/del>{c}<input type=hidden name=id value={r['id']}><button class=ghost>Удалить</button></form></td></tr>" for r in ex_rows) \
        or "<tr><td colspan=5 class=empty>Расходов пока нет. Добавьте аренду, материалы и рекламу: без них прибыль не посчитать.</td></tr>"
    return (f"<h2>Финансы</h2>{chips(days, '/admin/finance')}"
            f"<div class='kpis flat'>{kpi('Выручка', rub(rev), 'по визитам «пришёл»')}{kpi('Получено в кассы', rub(received), 'по внесённым оплатам')}"
            f"{kpi('Мастерам', rub(pay_masters), 'по их процентам')}{kpi('Материалы', rub(materials), 'по себестоимости услуг')}"
            f"{kpi('Расходы', rub(exp))}{kpi('Прибыль', rub(profit), 'выручка − мастера − материалы − расходы')}</div>"
            f"<div class=cols><section><h3>Поступления по кассам</h3>{bars(by_acc)}</section></div>"
            f"<h3 style='margin-top:28px'>Ждут оплаты</h3><table><thead><tr><th>Когда</th><th>Клиент</th><th>К оплате</th><th></th></tr></thead><tbody>{un_tr}</tbody></table>"
            f"<h3 style='margin-top:28px'>Мастера и проценты</h3><table><thead><tr><th>Мастер</th><th>Выручка</th><th>Процент мастеру</th><th>К выплате</th></tr></thead><tbody>{m_tr}</tbody></table>"
            f"<h3 style='margin-top:28px'>Расходы салона</h3><form class=row method=post action=/admin/expense/add>{c}"
            f"<input type=date name=date value='{date.today().isoformat()}' required><select name=category aria-label='Категория'>{eopts}</select>"
            f"<input name=amount type=number min=1 placeholder='Сумма, ₽' required><input name=note placeholder='Комментарий' size=24><button>Добавить расход</button></form>"
            f"<table><thead><tr><th>Дата</th><th>Категория</th><th>Сумма</th><th>Комментарий</th><th></th></tr></thead><tbody>{ex_tr}</tbody></table>")


def offers_page(con, csrf):
    c = f"<input type=hidden name=csrf value='{csrf}'>"
    markup = offers.setting_int(con, "min_markup_pct", 20)
    st = offers.stats(con)
    rules = {"cross_new_target": "Допродажа: клиент записался на одну услугу, а на соседней ещё не был",
             "welcome": "Возврат: подарок на второй визит новичку", "referral_invite": "Подруга: код для приглашения",
             "referral_reward": "Награда пригласившей, когда подруга пришла"}
    tr = ""
    for x in st:
        o = x["offer"]
        cond = f"{esc(o['trigger_cat'])} → {esc(o['target_cat'])}" if o["rule"] == "cross_new_target" else ""
        tr += (f"<tr><td><b>{esc(o['title'])}</b><br><span class=muted>{esc(rules.get(o['rule'], o['rule']))} {cond}</span></td>"
               f"<td>{x['issued']}<br><span class=muted>приняли: {x['accepted']}</span></td><td>{x['used']}<br><span class=muted>{pct(x['used'], x['issued'])}</span></td>"
               f"<td>{rub(x['revenue'])}<br><span class=muted>скидок: {rub(x['discount'])}</span></td>"
               f"<td>{rub(x['committed'])}<br><span class=muted>{('из ' + rub(o['budget'])) if o['budget'] else 'лимит не задан'}</span></td>"
               f"<td><form class=row method=post action=/admin/offer/save style='margin:0;flex-wrap:nowrap'>{c}<input type=hidden name=id value={o['id']}>"
               f"<input name=value type=number min=0 max=100 value={o['value']} title='Скидка, %' style='width:62px'>"
               f"<input name=valid_days type=number min=1 value={o['valid_days']} title='Дней действует' style='width:62px'>"
               f"<input name=max_rub type=number min=0 value={o['max_rub']} title='Потолок скидки, ₽' style='width:76px'>"
               f"<input name=budget type=number min=0 value={o['budget']} title='Бюджет, ₽ (0 = без лимита)' style='width:84px'><button class=ghost>Сохранить</button></form>"
               f"<form class=inline method=post action=/admin/toggle>{c}<input type=hidden name=table value=offers><input type=hidden name=id value={o['id']}>"
               f"<button class=ghost style='margin-top:6px'>{'Выключить' if o['active'] else 'Включить'}</button></form> "
               f"<span class='st {'arrived' if o['active'] else 'cancelled'}'>{'работает' if o['active'] else 'выключено'}</span></td></tr>")
    gr = con.execute("SELECT g.*, o.title, c.name AS cname FROM grants g JOIN offers o ON o.id=g.offer_id JOIN clients c ON c.id=g.client_id ORDER BY g.id DESC LIMIT 15").fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    gr_tr = "".join(f"<tr><td>{esc(r['code'])}</td><td>{esc(r['title'])}</td><td>{esc(r['cname'])}</td>"
                    f"<td>{'использован' if r['status'] == 'used' else ('истёк' if r['expires_at'] < now else 'выдан')}</td><td>{esc(r['expires_at'][:10])}</td></tr>" for r in gr) \
        or "<tr><td colspan=5 class=empty>Подарков пока не выдано. Они появятся после первых записей с сайта.</td></tr>"
    return (f"<h2>Допродажи и подарки</h2>"
            f"<div class=card><h3>Как это работает</h3><ul class=todo>"
            f"<li>Клиент записался на услугу, и сразу на странице благодарности видит подарок на <b>соседнюю услугу</b>, на которой ещё не был. Это дешёвый способ продать вторую услугу.</li>"
            f"<li>Новичку дарим скидку на <b>второй визит</b>: цель — чтобы он вернулся, а не остался разовым клиентом.</li>"
            f"<li>Клиент получает код для подруги. Подруге скидка на первый визит, ему бесплатная услуга, когда подруга пришла.</li>"
            f"<li>Подарок выдаётся по условиям, а не случайно. Слова «выиграли» и «розыгрыш» не используем: это уже лотерея по закону.</li></ul></div>"
            f"<div class=card><h3>Защита от убытков</h3><p class=muted>Скидка не может превысить потолок в рублях, цена после скидки не опускается ниже себестоимости с наценкой, у каждой акции месячный бюджет, подарки не суммируются между собой и с картами YClients.</p>"
            f"<form class=row method=post action=/admin/offers/settings style='margin:0'>{c}<label>Минимальная наценка над себестоимостью, % <input name=min_markup_pct type=number min=0 max=300 value={markup} style='width:80px'></label><button>Сохранить</button></form></div>"
            f"<table><thead><tr><th>Предложение</th><th>Выдано</th><th>Использовано</th><th>Выручка</th><th>Бюджет занят</th><th>Настройки: скидка %, дней, потолок ₽, бюджет ₽</th></tr></thead><tbody>{tr}</tbody></table>"
            f"<h3 style='margin-top:28px'>Последние подарки</h3><table><thead><tr><th>Код</th><th>Предложение</th><th>Клиент</th><th>Статус</th><th>До</th></tr></thead><tbody>{gr_tr}</tbody></table>")
