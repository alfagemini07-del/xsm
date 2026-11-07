"""Airitom movement journal. A visit carries its tariff across checkpoints."""

import io
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import (
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

TZ = ZoneInfo("Asia/Tashkent")
POINTS = {
    "parkovka_tashqi": "Tashqi parkovka",
    "kpp2": "KPP2 · O‘zbekiston",
    "ayritom": "Ayritom darvozasi",
}
LABEL = {
    **POINTS,
    "scale": "Tarozi",
    "border": "Ayritom postiga navbat",
    "zone": "Free Zone",
    "outside": "Tashqi parkovka",
    "parking": "Tashqi parkovka",
    "inside": "Free Zone ichida",
    "closed": "Tashrif yakunlandi",
    "in": "Kirish",
    "out": "Chiqish",
    "weigh": "Tarozi xizmati",
    "cash": "Naqd",
    "card": "Karta",
    "bank": "Hisob raqam",
    "none": "To‘lovsiz",
}
ADMINS = ("admin", "techadmin")


def transition(point, action, visit, purpose):
    place = visit["location"] if visit else None
    if action == "in":
        if point == "kpp2":
            if visit and place == "parking" and visit["purpose"] == "zone":
                return "inside", "kpp2", True, True
            raise ValueError(
                "KPP2 kirishi uchun tashqi parkovkadan Free Zone navbati kerak."
            )
        if visit:
            raise ValueError("Avtomobilning ochiq tashrifi bor: " + LABEL[place])
        if point == "parkovka_tashqi" and purpose not in ("zone", "border"):
            raise ValueError("Navbat maqsadini tanlang.")
        if point == "parkovka_tashqi":
            return "parking", None, False, False
        return "inside", "ayritom", False, True
    if action == "out" and visit:
        if point == "parkovka_tashqi" and place == "parking":
            if visit["purpose"] == "zone":
                if visit.get("parking_departed_at"):
                    raise ValueError(
                        "Bu transportning parkovkadan chiqishi avval qayd etilgan."
                    )
                return "parking", None, True, False
            return "closed", None, True, False
        if point in ("ayritom", "kpp2") and place == "inside":
            return "closed", visit.get("entry_gate"), False, False
    raise ValueError("Bu nuqtada chiqish uchun mos ochiq tashrif topilmadi.")


def quote(conn, point, action, plate, key, purpose, now):
    snapshot = conn.execute(
        """SELECT v.*,
        (SELECT COALESCE(SUM(amount),0) FROM zone_events WHERE visit_id=v.id) AS collected,
        (SELECT MAX(id) FROM zone_events WHERE visit_id=v.id) AS event_version,
        (SELECT MAX(id) FROM zone_visits WHERE plate_key=%s) AS visit_version,
        (SELECT jsonb_object_agg(key,value) FROM settings
           WHERE key IN ('zone_daily_rate','zone_daily_enabled')) AS tariff
        FROM (VALUES(1)) seed(n) LEFT JOIN LATERAL
          (SELECT * FROM zone_visits WHERE plate_key=%s AND location<>'closed') v ON TRUE""",
        (key, key),
    ).fetchone()
    visit = snapshot if snapshot["id"] is not None else None
    location, entry_gate, mark_departed, mark_entered = transition(
        point, action, visit, purpose
    )
    if visit:
        paid, version = int(snapshot["collected"]), snapshot["event_version"]
        rate = visit["daily_rate"]
        days = (now.date() - visit["started"].astimezone(TZ).date()).days + 1
    else:
        tariff = snapshot["tariff"] or {}
        rate = (
            int(tariff.get("zone_daily_rate", "30000"))
            if tariff.get("zone_daily_enabled", "true") == "true"
            else 0
        )
        version = snapshot["visit_version"]
        days, paid = 1, 0
    # A Free Zone visit carries its first parking payment into the zone; settle only at final exit.
    amount = max(0, days * rate - paid) if not visit or location == "closed" else 0
    return dict(
        point=point,
        action=action,
        plate=visit["plate"] if visit else plate,
        key=key,
        purpose=visit["purpose"]
        if visit
        else ("zone" if point == "ayritom" else purpose),
        visit=visit["id"] if visit else None,
        version=version,
        location=location,
        entry_gate=entry_gate,
        mark_departed=mark_departed,
        mark_entered=mark_entered,
        day=now.date().isoformat(),
        days=days,
        rate=rate,
        paid=paid,
        amount=amount,
    )


def period_bounds(args):
    today = datetime.now(TZ).date()
    starts = {
        "day": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
    }
    period = args.get("period", "day")
    try:
        first = (
            date.fromisoformat(args["start"]) if period == "custom" else starts[period]
        )
        last = date.fromisoformat(args["end"]) if period == "custom" else today
        if not 0 <= (last - first).days <= 366:
            raise ValueError()
        return datetime.combine(first, time.min, TZ), datetime.combine(
            last + timedelta(days=1), time.min, TZ
        )
    except (ValueError, KeyError, OverflowError):
        abort(400, description="Sanalarni tekshiring. Eng katta davr 367 kun.")


# Both sides use the instant money was recorded. Weighing charges are included once.
LEDGER = """
 SELECT e.occurred ts,e.point,e.action,v.plate,e.operator_name worker,e.method,e.amount,
        'AFZ-'||e.id ref,'AFZ-V'||v.id visit FROM zone_events e JOIN zone_visits v ON v.id=e.visit_id
 WHERE e.occurred >= %s AND e.occurred < %s
 UNION ALL
 SELECT COALESCE(w.paid_at,w.created_at),'scale','weigh',w.plate_number,COALESCE(u.username,'Import'),
        w.payment_method,w.price,w.receipt_no,'' FROM weighings w LEFT JOIN users u ON u.id=w.created_by
 WHERE w.status='paid' AND COALESCE(w.paid_at,w.created_at) >= %s AND COALESCE(w.paid_at,w.created_at) < %s
"""


def register_zone(app, pool, roles, clean_plate, qr):
    signer = URLSafeTimedSerializer(app.secret_key, salt="zone-confirm-v1")

    def page(**fields):
        return render_template(
            "zone.html" if fields.get("mode") == "dashboard" else "kpp.html",
            labels=LABEL,
            points=POINTS,
            user=g.user,
            **fields,
        )

    def money(value):
        return f"{int(value):,}".replace(",", " ")

    app.jinja_env.filters["som"] = money
    app.jinja_env.filters["zone_time"] = lambda dt: dt.astimezone(TZ).strftime(
        "%d.%m.%Y %H:%M"
    )

    @app.get("/zone")
    @roles(*POINTS, *ADMINS)
    def zone_home():
        point = g.user["role"]
        if point == "parkovka_tashqi":
            abort(403, description="Kompyuterdagi Parkovka dasturidan kiring")
        if point in POINTS:
            return page(mode="station")
        return overview(False)

    @app.get("/api/kpp/journal")
    @roles("ayritom", "kpp2")
    def kpp_journal():
        point = g.user["role"]
        number = max(1, request.args.get("page", 1, type=int))
        size = 10
        with pool().connection() as conn:
            with conn.pipeline():
                recent_query = conn.execute(
                    """SELECT e.id,e.occurred,e.action,e.amount,v.plate FROM zone_events e
                    JOIN zone_visits v ON v.id=e.visit_id WHERE point=%s ORDER BY e.id DESC
                    LIMIT %s OFFSET %s""",
                    (point, size + 1, (number - 1) * size),
                )
                queue_query = conn.execute(
                    """SELECT id,plate,purpose,queue_no FROM zone_visits
                    WHERE location='parking' AND purpose='zone' AND %s='kpp2' ORDER BY id LIMIT 25""",
                    (point,),
                )
            recent = recent_query.fetchall()
            queue = queue_query.fetchall()
        return jsonify(
            recent=[dict(r, occurred=r["occurred"].isoformat()) for r in recent[:size]],
            queue=queue,
            page=number,
            more=len(recent) > size,
        )

    @app.get("/api/kpp/stats")
    @roles("ayritom", "kpp2")
    def kpp_stats():
        start = datetime.combine(datetime.now(TZ).date(), time.min, TZ)
        with pool().connection() as conn:
            result = conn.execute(
                """SELECT
                (SELECT count(*) FROM zone_visits WHERE location='inside') AS active,
                (SELECT count(*) FROM zone_visits WHERE location='parking' AND purpose='zone') AS waiting,
                (SELECT count(*) FROM zone_visits WHERE location='parking' AND purpose='zone' AND parking_departed_at IS NOT NULL) AS departed,
                count(*) FILTER(WHERE action='out') AS count,COALESCE(sum(amount),0) AS total
                FROM zone_events WHERE point=%s AND occurred >= %s""",
                (g.user["role"], start),
            ).fetchone()
        return jsonify(result)

    def problem(message, status):
        if request.is_json:
            return jsonify(message=message), status
        return page(mode="error", message=message), status

    def saved_response(conn, eid):
        if request.is_json:
            return receipt_response(read_receipt(conn, eid))
        return redirect(url_for("zone_receipt", eid=eid), 303)

    @app.post("/zone/quote")
    @roles(*POINTS)
    def zone_quote():
        if g.user["role"] == "parkovka_tashqi":
            abort(403)
        data = request.get_json() if request.is_json else request.form
        if request.is_json and not isinstance(data, dict):
            return problem("So‘rovni tekshiring.", 400)
        try:
            plate, key = clean_plate(data.get("plate", ""))
            if not re.fullmatch(r"[0-9A-ZА-ЯЁЎҚҒҲ -]+", plate) or len(key) < 2:
                raise ValueError("Davlat raqamini tekshiring.")
            with pool().connection() as conn:
                q = quote(
                    conn,
                    g.user["role"],
                    data.get("action"),
                    plate,
                    key,
                    data.get("purpose"),
                    datetime.now(TZ),
                )
        except ValueError as exc:
            return problem(str(exc), 400)
        token = signer.dumps([g.user["id"], str(uuid.uuid4()), q])
        if request.is_json:
            return jsonify(quote=q, ticket=token)
        return page(mode="confirm", q=q, token=token)

    @app.post("/zone/confirm")
    @roles(*POINTS)
    def zone_commit():
        if g.user["role"] == "parkovka_tashqi":
            abort(403)
        data = request.get_json() if request.is_json else request.form
        if request.is_json and not isinstance(data, dict):
            return problem("So‘rovni tekshiring.", 400)
        ticket = data.get("ticket", "")
        expired = False
        try:
            try:
                user_id, rid, expected = signer.loads(ticket, max_age=900)
            except SignatureExpired:
                user_id, rid, expected = signer.loads(ticket)
                expired = True
        except BadData:
            return problem(
                "Tasdiqlash ma’lumoti yaroqsiz. Raqamni qayta kiriting.", 400
            )
        point = g.user["role"]
        if g.user["id"] != user_id or expected["point"] != point:
            abort(403)
        with pool().connection() as conn:
            key = expected["key"]
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))", (key,))
            existing = conn.execute(
                "SELECT id FROM zone_events WHERE request_key=%s", (rid,)
            ).fetchone()
            if existing:
                return saved_response(conn, existing["id"])
            if expired:
                return problem("Hisob muddati tugadi. Raqamni qayta kiriting.", 409)
            now = datetime.now(TZ)
            try:
                q = quote(
                    conn,
                    point,
                    expected["action"],
                    expected["plate"],
                    key,
                    expected["purpose"],
                    now,
                )
                if q != expected:
                    raise ValueError(
                        "Tashrif yoki tarif o‘zgardi. Hisobni qayta tekshiring."
                    )
                method = "none"
                if q["amount"]:
                    method = data.get("method")
                    if (
                        method not in ("cash", "bank", "card")
                        or data.get("received") != "yes"
                        or str(data.get("amount")) != str(q["amount"])
                    ):
                        raise ValueError(
                            "Qabul qilingan summani va to‘lov turini tasdiqlang."
                        )
            except ValueError as exc:
                return problem(str(exc), 409)
            vid = q["visit"]
            if vid:
                conn.execute(
                    """UPDATE zone_visits SET location=%s,
                    entry_gate=CASE WHEN %s THEN %s ELSE entry_gate END,
                    parking_departed_at=CASE WHEN %s THEN COALESCE(parking_departed_at,%s) ELSE parking_departed_at END,
                    entered_at=CASE WHEN %s THEN COALESCE(entered_at,%s) ELSE entered_at END
                    WHERE id=%s""",
                    (
                        q["location"],
                        q["mark_entered"],
                        q["entry_gate"],
                        q["mark_departed"],
                        now,
                        q["mark_entered"],
                        now,
                        vid,
                    ),
                )
            else:
                vid = conn.execute(
                    """INSERT INTO zone_visits
                    (plate,plate_key,purpose,location,entry_gate,entered_at,started,daily_rate)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        q["plate"],
                        key,
                        q["purpose"],
                        q["location"],
                        q["entry_gate"],
                        now if q["mark_entered"] else None,
                        now,
                        q["rate"],
                    ),
                ).fetchone()["id"]
            event = conn.execute(
                """INSERT INTO zone_events(request_key,visit_id,occurred,point,action,location,
                operator_name,days,previous_paid,amount,method) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    rid,
                    vid,
                    now,
                    point,
                    q["action"],
                    q["location"],
                    g.user["username"],
                    q["days"],
                    q["paid"],
                    q["amount"],
                    method,
                ),
            ).fetchone()
            app.logger.info(
                "ZONE_EVENT id=%s point=%s action=%s amount=%s",
                event["id"],
                point,
                q["action"],
                q["amount"],
            )
            return saved_response(conn, event["id"])

    @app.get("/zone/receipt/<int:eid>")
    @roles(*POINTS, *ADMINS)
    def zone_receipt(eid):
        with pool().connection() as conn:
            e = read_receipt(conn, eid)
        return receipt_response(e)

    def read_receipt(conn, eid):
        return conn.execute(
            """SELECT e.*,v.plate,v.started,v.daily_rate,v.purpose,v.entry_gate,v.queue_no,
            (SELECT COUNT(*) FROM receipt_jobs j WHERE j.event_id=e.id) print_attempts,
            (SELECT COUNT(*) FROM receipt_jobs j WHERE j.event_id=e.id AND j.state='confirmed') print_confirmed,
            (SELECT concat_ws(' · ',j.method,j.state,NULLIF(j.detail,'')) FROM receipt_jobs j WHERE j.event_id=e.id ORDER BY j.created_at DESC LIMIT 1) print_last
            FROM zone_events e
            JOIN zone_visits v ON v.id=e.visit_id WHERE e.id=%s""",
            (eid,),
        ).fetchone()

    def receipt_response(e):
        if not e or g.user["role"] not in (*ADMINS, e["point"]):
            abort(404)
        eid = e["id"]
        fields = [
            ("Chek", f"AFZ-{eid}"),
            ("Navbat / tashrif", e["queue_no"] or str(e["visit_id"])),
            ("Avtomobil", e["plate"]),
            ("Maqsad", LABEL[e["purpose"]]),
            ("Nuqta", LABEL[e["point"]]),
            ("Amal", LABEL[e["action"]]),
            ("Holat", LABEL[e["location"]]),
            ("Kirish vaqti", app.jinja_env.filters["zone_time"](e["started"])),
            ("Chek vaqti", app.jinja_env.filters["zone_time"](e["occurred"])),
            ("Kun", str(e["days"])),
            ("Kunlik tarif", money(e["daily_rate"])),
            ("Oldin to‘langan", money(e["previous_paid"])),
            ("Hozir olindi", money(e["amount"]) + " so‘m"),
            ("Jami to‘langan", money(e["previous_paid"] + e["amount"])),
            ("To‘lov turi", LABEL[e["method"]]),
            ("Xodim", e["operator_name"]),
        ]
        if e["entry_gate"]:
            fields.insert(7, ("Free Zone kirish darvozasi", POINTS[e["entry_gate"]]))
        code = qr(
            "Airitom Logistics Center MCHJ\n"
            + "\n".join(f"{k}: {v}" for k, v in fields),
            border=4,
        )
        from printing import artwork
        picture = artwork(fields, code)
        if request.is_json or request.accept_mimetypes.best == "application/json":
            return jsonify(id=eid, fields=fields, qr=code, plate=e["plate"], image=picture,print_attempts=e["print_attempts"],print_confirmed=e["print_confirmed"],print_last=e["print_last"])
        return page(mode="receipt", fields=fields, receipt=e, qr=code, picture=picture, receipt_id=eid)

    @app.get("/zone/report.xlsx")
    @roles(*ADMINS)
    def zone_export():
        return overview(True)

    def overview(export):
        start, end = period_bounds(request.args)
        selected = request.args.get("point", "")
        if selected and selected not in (*POINTS, "scale"):
            abort(400)
        sql = " FROM (" + LEDGER + ") ledger"
        args = [start, end, start, end]
        if selected:
            sql += " WHERE point=%s"
            args.append(selected)
        place = request.args.get("location", "")
        if place and place not in ("parking", "inside", "outside"):
            abort(400)
        search = re.sub(
            r"[^0-9A-ZА-ЯЁЎҚҒҲ]", "", request.args.get("plate", "").upper()
        )[:20]
        where = " WHERE location<>'closed' AND plate_key LIKE %s"
        params = [search + "%"]
        if place:
            where += " AND location=%s"
            params.append("parking" if place == "outside" else place)
        purpose = request.args.get("purpose", "")
        if purpose:
            if purpose not in ("border", "zone"):
                abort(400)
            where += " AND purpose=%s"
            params.append(purpose)
        gate = request.args.get("gate", "")
        if gate:
            if gate not in ("kpp2", "ayritom"):
                abort(400)
            where += " AND entry_gate=%s"
            params.append(gate)
        summary_sql = """SELECT point,count(*) operations,sum(amount) total,
            COALESCE(sum(amount) FILTER(WHERE method='cash'),0) cash,
            COALESCE(sum(amount) FILTER(WHERE method='card'),0) card,
            COALESCE(sum(amount) FILTER(WHERE method='bank'),0) bank,
            count(*) FILTER(WHERE action='in') arrivals,count(*) FILTER(WHERE action='out') departures"""
        with pool().connection() as conn:
            # Keep one consistent snapshot while batching independent reads over a remote database.
            with conn.pipeline():
                conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                summary_query = conn.execute(
                    summary_sql + sql + " GROUP BY point ORDER BY point", args
                )
                if not export:
                    trend_query = conn.execute(
                        "SELECT (ts AT TIME ZONE 'Asia/Tashkent')::date AS day,sum(amount) AS revenue"
                        + sql
                        + " GROUP BY 1 ORDER BY 1",
                        args,
                    )
                    stats_query = conn.execute("""SELECT
                        COUNT(*) FILTER(WHERE location='parking' AND purpose='border') AS border,
                        COUNT(*) FILTER(WHERE location='parking' AND purpose='zone') AS zone,
                        COUNT(*) FILTER(WHERE location='inside') AS inside,
                        COUNT(*) FILTER(WHERE location='inside' AND entry_gate='kpp2') AS inside_kpp2,
                        COUNT(*) FILTER(WHERE location='inside' AND entry_gate='ayritom') AS inside_ayritom,
                        COUNT(*) FILTER(WHERE location='inside' AND entry_gate IS NULL) AS inside_unknown
                        FROM zone_visits WHERE location<>'closed'""")
                    count_query = conn.execute(
                        "SELECT count(*) n FROM zone_visits" + where, params
                    )
            summary = summary_query.fetchall()
            event_total = sum(row["operations"] for row in summary)
            if export:
                if event_total > 25000:
                    abort(
                        400,
                        description="25 000 qatordan ko‘p. Davr yoki nuqtani toraytiring.",
                    )
                rows = conn.execute(
                    "SELECT *" + sql + " ORDER BY ts DESC,ref", args
                ).fetchall()
                return workbook(summary, rows, start, end, selected)
            trend = trend_query.fetchall()
            stats = stats_query.fetchone()
            total = count_query.fetchone()["n"]
            event_pages = max(1, (event_total + 24) // 25)
            event_page = min(max(1, request.args.get("ep", 1, type=int)), event_pages)
            pages = max(1, (total + 29) // 30)
            pg = min(max(1, request.args.get("p", 1, type=int)), pages)
            ordering = {
                "queue": "id",
                "latest": "started DESC, id DESC",
                "plate": "plate_key, id",
            }
            sort = ordering.get(request.args.get("sort", "queue"), "id")
            with conn.pipeline():
                rows_query = conn.execute(
                    "SELECT *" + sql + " ORDER BY ts DESC,ref LIMIT 25 OFFSET %s",
                    [*args, (event_page - 1) * 25],
                )
                active_query = conn.execute(
                    "SELECT * FROM zone_visits"
                    + where
                    + " ORDER BY "
                    + sort
                    + " LIMIT 30 OFFSET %s",
                    [*params, (pg - 1) * 30],
                )
            rows = rows_query.fetchall()
            active = active_query.fetchall()
        return page(
            mode="dashboard",
            summary=summary,
            trend=trend,
            rows=rows,
            active=active,
            stats=stats,
            pg=pg,
            pages=pages,
            event_page=event_page,
            event_pages=event_pages,
            event_total=event_total,
            start=start.date(),
            end=end.date() - timedelta(days=1),
            selected=selected,
            total=sum(r["total"] for r in summary),
        )

    @app.get("/api/zone/live")
    @roles(*ADMINS)
    def zone_live():
        with pool().connection() as conn:
            stats = conn.execute("""SELECT
                COUNT(*) FILTER(WHERE location='parking' AND purpose='border') AS border,
                COUNT(*) FILTER(WHERE location='parking' AND purpose='zone') AS zone,
                COUNT(*) FILTER(WHERE location='inside') AS inside,
                COUNT(*) FILTER(WHERE location='inside' AND entry_gate='kpp2') AS inside_kpp2,
                COUNT(*) FILTER(WHERE location='inside' AND entry_gate='ayritom') AS inside_ayritom,
                COUNT(*) FILTER(WHERE location='inside' AND entry_gate IS NULL) AS inside_unknown
                FROM zone_visits WHERE location<>'closed'""").fetchone()
            events = conn.execute("""SELECT e.action,e.point,e.occurred,v.plate FROM zone_events e
                JOIN zone_visits v ON v.id=e.visit_id ORDER BY e.id DESC LIMIT 4""").fetchall()
        return jsonify(
            success=True,
            stats={key: int(value or 0) for key, value in stats.items()},
            events=[
                {
                    "action": row["action"],
                    "point": POINTS[row["point"]],
                    "plate": row["plate"],
                    "time": row["occurred"].astimezone(TZ).strftime("%H:%M"),
                }
                for row in events
            ],
        )


def workbook(summary, rows, start, end, point):
    book = Workbook()
    sheet = book.active
    sheet.title = "Nuqtalar"
    sheet.append(
        [
            "Airitom Free Zone",
            str(start.date()),
            str(end.date() - timedelta(days=1)),
            LABEL.get(point, "Barcha nuqtalar"),
        ]
    )
    sheet.append(
        [
            "Nuqta",
            "Amallar",
            "Kirish",
            "Chiqish",
            "Naqd",
            "Karta",
            "Hisob raqam",
            "Jami so‘m",
        ]
    )
    for r in summary:
        sheet.append(
            [
                LABEL[r["point"]],
                *[
                    int(r[k])
                    for k in (
                        "operations",
                        "arrivals",
                        "departures",
                        "cash",
                        "card",
                        "bank",
                        "total",
                    )
                ],
            ]
        )
    sheet.append(
        [
            "Jami",
            *[
                sum(int(r[k]) for r in summary)
                for k in (
                    "operations",
                    "arrivals",
                    "departures",
                    "cash",
                    "card",
                    "bank",
                    "total",
                )
            ],
        ]
    )
    log = book.create_sheet("Operatsiyalar")
    log.append(
        [
            "Vaqt (Toshkent)",
            "Chek",
            "Tashrif",
            "Davlat raqami",
            "Nuqta",
            "Amal",
            "Xodim",
            "To‘lov turi",
            "Summa (so‘m)",
        ]
    )
    for r in rows:
        log.append(
            [
                r["ts"].astimezone(TZ).replace(tzinfo=None),
                r["ref"],
                r["visit"],
                r["plate"],
                LABEL[r["point"]],
                LABEL[r["action"]],
                r["worker"],
                LABEL[r["method"]],
                int(r["amount"]),
            ]
        )
    for tab, header in ((sheet, 2), (log, 1)):
        tab.freeze_panes = f"B{header + 1}"
        tab.auto_filter.ref = (
            f"A{header}:{get_column_letter(tab.max_column)}{tab.max_row}"
        )
        for col in tab[header]:
            col.font = Font(bold=True, color="FFFFFF")
            col.fill = PatternFill("solid", fgColor="12675E")
            tab.column_dimensions[col.column_letter].width = 25
        for row in tab.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                elif isinstance(cell.value, int):
                    cell.number_format = "#,##0"
    stream = io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"Airitom-{start.date()}-{end.date() - timedelta(days=1)}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
