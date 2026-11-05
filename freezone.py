"""Airitom movement journal. A visit carries its tariff across checkpoints."""
import io
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import abort, g, redirect, render_template, request, send_file, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

TZ = ZoneInfo("Asia/Tashkent")
POINTS = {"parkovka_tashqi": "Tashqi parkovka", "kpp2": "KPP2 · O‘zbekiston", "ayritom": "Ayritom darvozasi"}
LABEL = {**POINTS, "scale": "Tarozi", "border": "Ayritom postiga navbat", "zone": "Free Zone",
         "outside": "Tashqaridagi barchasi", "parking": "Parkovkada", "transfer": "KPP2 yo‘lida", "inside": "Hudud ichida",
         "closed": "Tashrif yakunlandi", "in": "Kirish", "out": "Chiqish", "weigh": "Tarozi xizmati",
         "cash": "Naqd", "card": "Karta", "bank": "Hisob raqam", "none": "To‘lovsiz"}
ADMINS = ("admin", "techadmin")


def transition(point, action, visit, purpose):
    place = visit["location"] if visit else None
    if action == "in":
        if point == "kpp2":
            if visit and place in ("parking", "transfer") and visit["purpose"] == "zone":
                return "inside"
            raise ValueError("KPP2 kirishi uchun tashqi parkovkadan Free Zone navbati kerak.")
        if visit:
            raise ValueError("Avtomobilning ochiq tashrifi bor: " + LABEL[place])
        if point == "parkovka_tashqi" and purpose not in ("zone", "border"):
            raise ValueError("Navbat maqsadini tanlang.")
        return "parking" if point == "parkovka_tashqi" else "inside"
    if action == "out" and visit:
        if point == "parkovka_tashqi" and place == "parking":
            return "transfer" if visit["purpose"] == "zone" else "closed"
        if point in ("ayritom", "kpp2") and place == "inside":
            return "closed"
    raise ValueError("Bu nuqtada chiqish uchun mos ochiq tashrif topilmadi.")


def quote(conn, point, action, plate, key, purpose, now):
    visit = conn.execute("SELECT * FROM zone_visits WHERE plate_key=%s AND location<>'closed'", (key,)).fetchone()
    location = transition(point, action, visit, purpose)
    if visit:
        history = conn.execute("SELECT COALESCE(SUM(amount),0) paid,MAX(id) version FROM zone_events WHERE visit_id=%s",
                               (visit["id"],)).fetchone()
        paid, version = int(history["paid"]), history["version"]
        rate = visit["daily_rate"]
        days = (now.date() - visit["started"].astimezone(TZ).date()).days + 1
    else:
        tariff_rows = conn.execute(
            "SELECT key,value FROM settings WHERE key IN ('zone_daily_rate','zone_daily_enabled')"
        ).fetchall()
        tariff = {row["key"]: row["value"] for row in tariff_rows}
        rate = int(tariff.get("zone_daily_rate", "30000")) if tariff.get("zone_daily_enabled", "true") == "true" else 0
        version = conn.execute("SELECT MAX(id) n FROM zone_visits WHERE plate_key=%s",(key,)).fetchone()["n"]
        days, paid = 1, 0
    # Free Zone transfers carry the first payment forward. Settle only at final exit.
    amount = max(0, days * rate - paid) if not visit or location == "closed" else 0
    return dict(point=point, action=action, plate=visit["plate"] if visit else plate, key=key,
                purpose=visit["purpose"] if visit else ("zone" if point=="ayritom" else purpose),
                visit=visit["id"] if visit else None, version=version, location=location,
                day=now.date().isoformat(), days=days, rate=rate, paid=paid, amount=amount)


def period_bounds(args):
    today = datetime.now(TZ).date()
    starts = {"day": today, "week": today-timedelta(days=today.weekday()),
              "month": today.replace(day=1), "year": today.replace(month=1,day=1)}
    period = args.get("period", "day")
    try:
        first = date.fromisoformat(args["start"]) if period=="custom" else starts[period]
        last = date.fromisoformat(args["end"]) if period=="custom" else today
        if not 0 <= (last-first).days <= 366:
            raise ValueError()
        return datetime.combine(first,time.min,TZ), datetime.combine(last+timedelta(days=1),time.min,TZ)
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
        return render_template("zone.html", labels=LABEL, points=POINTS, user=g.user, **fields)

    def money(value):
        return f"{int(value):,}".replace(",", " ")
    app.jinja_env.filters["som"] = money
    app.jinja_env.filters["zone_time"] = lambda dt: dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M")

    @app.get("/zone")
    @roles(*POINTS, *ADMINS)
    def zone_home():
        point = g.user["role"]
        if point in POINTS:
            with pool().connection() as conn:
                recent = conn.execute("""SELECT e.id,e.occurred,e.action,e.amount,v.plate FROM zone_events e
                    JOIN zone_visits v ON v.id=e.visit_id WHERE point=%s ORDER BY e.id DESC LIMIT 12""",(point,)).fetchall()
                queue = conn.execute("""SELECT id,plate,purpose FROM zone_visits WHERE location IN ('parking','transfer')
                    AND (purpose='zone' OR %s='parkovka_tashqi') ORDER BY id LIMIT 25""",(point,)).fetchall() if point!="ayritom" else []
            return page(mode="station",recent=recent,queue=queue)
        return overview(False)

    @app.post("/zone/quote")
    @roles(*POINTS)
    def zone_quote():
        try:
            plate, key = clean_plate(request.form.get("plate",""))
            if not re.fullmatch(r"[0-9A-ZА-ЯЁЎҚҒҲ -]+",plate) or len(key)<2:
                raise ValueError("Davlat raqamini tekshiring.")
            with pool().connection() as conn:
                q = quote(conn,g.user["role"],request.form.get("action"),plate,key,
                          request.form.get("purpose"),datetime.now(TZ))
        except ValueError as exc:
            return page(mode="error",message=str(exc)), 400
        token = signer.dumps([g.user["id"],str(uuid.uuid4()),q])
        return page(mode="confirm", q=q, token=token)

    @app.post("/zone/confirm")
    @roles(*POINTS)
    def zone_commit():
        try:
            user_id, rid, expected = signer.loads(request.form.get("ticket",""), max_age=900)
        except BadData:
            return page(mode="error", message="Tasdiqlash muddati tugadi. Raqamni qayta kiriting."), 400
        point = g.user["role"]
        if g.user["id"]!=user_id or expected["point"]!=point:
            abort(403)
        with pool().connection() as conn:
            key = expected["key"]
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",(key,))
            existing = conn.execute("SELECT id FROM zone_events WHERE request_key=%s",(rid,)).fetchone()
            if existing:
                return redirect(url_for("zone_receipt", eid=existing["id"]),303)
            now = datetime.now(TZ)
            try:
                q = quote(conn,point,expected["action"],expected["plate"],key,expected["purpose"],now)
                if q != expected:
                    raise ValueError("Tashrif yoki tarif o‘zgardi. Hisobni qayta tekshiring.")
                method = "none"
                if q["amount"]:
                    method = request.form.get("method")
                    if method not in ("cash","bank","card") or request.form.get("received")!="yes" or request.form.get("amount")!=str(q["amount"]):
                        raise ValueError("Qabul qilingan summani va to‘lov turini tasdiqlang.")
            except ValueError as exc:
                return page(mode="error",message=str(exc)),409
            vid = q["visit"]
            if vid:
                conn.execute("UPDATE zone_visits SET location=%s WHERE id=%s",(q["location"],vid))
            else:
                vid = conn.execute("""INSERT INTO zone_visits(plate,plate_key,purpose,location,started,daily_rate)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (q["plate"],key,q["purpose"],q["location"],now,q["rate"])).fetchone()["id"]
            event = conn.execute("""INSERT INTO zone_events(request_key,visit_id,occurred,point,action,location,
                operator_name,days,previous_paid,amount,method) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (rid,vid,now,point,q["action"],q["location"],g.user["username"],q["days"],q["paid"],q["amount"],method)).fetchone()
        return redirect(url_for("zone_receipt",eid=event["id"],print_now=1),303)

    @app.get("/zone/receipt/<int:eid>")
    @roles(*POINTS,*ADMINS)
    def zone_receipt(eid):
        with pool().connection() as conn:
            e = conn.execute("""SELECT e.*,v.plate,v.started,v.daily_rate,v.purpose FROM zone_events e
                JOIN zone_visits v ON v.id=e.visit_id WHERE e.id=%s""",(eid,)).fetchone()
        if not e or g.user["role"] not in (*ADMINS,e["point"]):
            abort(404)
        fields = [("Chek",f"AFZ-{eid}"),("Navbat / tashrif",str(e["visit_id"])),("Avtomobil",e["plate"]),
                  ("Maqsad",LABEL[e["purpose"]]),("Nuqta",LABEL[e["point"]]),("Amal",LABEL[e["action"]]),
                  ("Holat",LABEL[e["location"]]),("Kirish vaqti",app.jinja_env.filters["zone_time"](e["started"])),
                  ("Chek vaqti",app.jinja_env.filters["zone_time"](e["occurred"])),("Kun",str(e["days"])),
                  ("Kunlik tarif",money(e["daily_rate"])),("Oldin to‘langan",money(e["previous_paid"])),
                  ("Hozir olindi",money(e["amount"])+" so‘m"),("Jami to‘langan",money(e["previous_paid"]+e["amount"])),
                  ("To‘lov turi",LABEL[e["method"]]),("Xodim",e["operator_name"])]
        return page(mode="receipt", fields=fields, receipt=e,
                    qr=qr("Airitom Logistics Center MCHJ\n"+"\n".join(f"{k}: {v}" for k,v in fields), border=4))

    @app.get("/zone/report.xlsx")
    @roles(*ADMINS)
    def zone_export():
        return overview(True)

    def overview(export):
        start,end = period_bounds(request.args)
        selected=request.args.get("point","")
        if selected and selected not in (*POINTS,"scale"): abort(400)
        sql=" FROM ("+LEDGER+") ledger"
        args=[start,end,start,end]
        if selected:
            sql+=" WHERE point=%s"
            args.append(selected)
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            summary=conn.execute("""SELECT point,count(*) operations,sum(amount) total,
                COALESCE(sum(amount) FILTER(WHERE method='cash'),0) cash,
                COALESCE(sum(amount) FILTER(WHERE method='card'),0) card,
                COALESCE(sum(amount) FILTER(WHERE method='bank'),0) bank,
                count(*) FILTER(WHERE action='in') arrivals,count(*) FILTER(WHERE action='out') departures"""
                +sql+" GROUP BY point ORDER BY point",args).fetchall()
            if export and sum(r["operations"] for r in summary)>25000:
                abort(400,description="25 000 qatordan ko‘p. Davr yoki nuqtani toraytiring.")
            rows=conn.execute("SELECT *"+sql+" ORDER BY ts DESC,ref"+("" if export else " LIMIT 80"),args).fetchall()
            if export:
                return workbook(summary,rows,start,end,selected)
            trend = conn.execute("""
                SELECT (ts AT TIME ZONE 'Asia/Tashkent')::date AS day, sum(amount) AS revenue
                """ + sql + " GROUP BY 1 ORDER BY 1", args).fetchall()
            counts=conn.execute("""SELECT location,purpose,count(*) n FROM zone_visits WHERE location<>'closed'
                GROUP BY location,purpose""").fetchall()
            place=request.args.get("location","")
            if place and place not in ("parking","transfer","inside","outside"): abort(400)
            search=re.sub(r"[^0-9A-ZА-ЯЁЎҚҒҲ]","",request.args.get("plate","").upper())[:20]
            where=" WHERE location<>'closed' AND plate_key LIKE %s"
            params=["%"+search+"%"]
            if place == "outside":
                where += " AND location IN ('parking','transfer')"
            elif place:
                where+=" AND location=%s"
                params.append(place)
            purpose=request.args.get("purpose","")
            if purpose:
                if purpose not in ("border","zone"): abort(400)
                where+=" AND purpose=%s"
                params.append(purpose)
            pg=max(1,request.args.get("p",1,type=int))
            total=conn.execute("SELECT count(*) n FROM zone_visits"+where,params).fetchone()["n"]
            pages=max(1,(total+29)//30)
            pg=min(pg,pages)
            sort = request.args.get("sort", "queue")
            ordering = {"queue": "id", "latest": "started DESC, id DESC", "plate": "plate_key, id"}
            active=conn.execute("SELECT * FROM zone_visits"+where+" ORDER BY "+ordering.get(sort, "id")
                                +" LIMIT 30 OFFSET %s",[*params,(pg-1)*30]).fetchall()
        stats={"border":0,"zone":0,"transfer":0,"inside":0}
        for r in counts:
            stats[r["purpose"] if r["location"]=="parking" else r["location"]]+=r["n"]
        return page(mode="dashboard",summary=summary,trend=trend,rows=rows,active=active,stats=stats,pg=pg,pages=pages,
                    start=start.date(),end=end.date()-timedelta(days=1),selected=selected,
                    total=sum(r["total"] for r in summary))


def workbook(summary,rows,start,end,point):
    book=Workbook()
    sheet=book.active
    sheet.title="Nuqtalar"
    sheet.append(["Airitom Free Zone",str(start.date()),str(end.date()-timedelta(days=1)),LABEL.get(point,"Barcha nuqtalar")])
    sheet.append(["Nuqta","Amallar","Kirish","Chiqish","Naqd","Karta","Hisob raqam","Jami so‘m"])
    for r in summary:
        sheet.append([LABEL[r["point"]],*[int(r[k]) for k in ("operations","arrivals","departures","cash","card","bank","total")]])
    sheet.append(["Jami",*[sum(int(r[k]) for r in summary) for k in ("operations","arrivals","departures","cash","card","bank","total")]])
    log=book.create_sheet("Operatsiyalar")
    log.append(["Vaqt (Toshkent)","Chek","Tashrif","Davlat raqami","Nuqta","Amal","Xodim","To‘lov turi","Summa (so‘m)"])
    for r in rows:
        log.append([r["ts"].astimezone(TZ).replace(tzinfo=None),r["ref"],r["visit"],r["plate"],LABEL[r["point"]],
                    LABEL[r["action"]],r["worker"],LABEL[r["method"]],int(r["amount"])])
    for tab,header in ((sheet,2),(log,1)):
        tab.freeze_panes=f"B{header+1}"
        tab.auto_filter.ref=f"A{header}:{get_column_letter(tab.max_column)}{tab.max_row}"
        for col in tab[header]:
            col.font=Font(bold=True,color="FFFFFF")
            col.fill=PatternFill("solid",fgColor="12675E")
            tab.column_dimensions[col.column_letter].width=25
        for row in tab.iter_rows():
            for cell in row:
                if isinstance(cell.value,str): cell.data_type="s"
                elif isinstance(cell.value,int): cell.number_format="#,##0"
    stream=io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return send_file(stream,as_attachment=True,download_name=f"Airitom-{start.date()}-{end.date()-timedelta(days=1)}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
