"""One financial ledger for scale, parking and both checkpoints."""

import io
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

TZ = ZoneInfo("Asia/Tashkent")
POINTS = {
    "scale": "Tarozi",
    "parkovka_tashqi": "Tashqi parkovka",
    "ayritom": "KPP Ayritom",
    "kpp2": "KPP2",
}
METHODS = {"cash": "Naqd", "card": "Karta", "bank": "Hisob raqam", "none": "To‘lovsiz"}


def selection(args, dates=True):
    today = datetime.now(TZ).date()
    try:
        start = datetime.fromisoformat(args.get("start") or today.isoformat()).date()
        end = datetime.fromisoformat(args.get("end") or today.isoformat()).date()
        page = max(1, int(args.get("page", 1)))
        size = max(10, min(100, int(args.get("per_page", 20))))
        operator = int(args.get("operator") or 0)
    except (ValueError, TypeError):
        abort(400, description="Sana yoki sahifa noto‘g‘ri")
    if (dates or (args.get("start") and args.get("end"))) and (
        start > end or (dates and (end - start).days > 366)
    ):
        abort(400, description="Oraliq 1–367 kun bo‘lsin")
    where = []
    params = []
    if dates or args.get("start"):
        where.append("occurred >= %s")
        params.append(datetime.combine(start, time.min, TZ))
    if dates or args.get("end"):
        where.append("occurred < %s")
        params.append(datetime.combine(end + timedelta(days=1), time.min, TZ))
    for key, allowed in [
        ("point", POINTS),
        ("method", METHODS),
        ("status", {"paid", "pending", "cancelled"}),
    ]:
        value = args.get(key, "")
        if value:
            if value not in allowed:
                abort(400, description="Filtr noto‘g‘ri")
            where.append(("point" if key == "point" else key) + "=%s")
            params.append(value)
    if operator:
        where.append("operator_id=%s")
        params.append(operator)
    plate = (args.get("plate") or "").strip().upper()[:100]
    if plate:
        pattern = (
            "%"
            + plate.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        where.append(
            "(REPLACE(REPLACE(UPPER(plate),' ',''),'-','') LIKE %s OR UPPER(receipt_no) LIKE %s OR UPPER(queue_no) LIKE %s)"
        )
        params.extend([pattern.replace(" ", "").replace("-", ""), pattern, pattern])
    return " AND ".join(where) or "TRUE", params, start, end, page, size


def stamp(conn):
    return conn.execute(
        "SELECT transaction_timestamp() AS at,GREATEST((SELECT MAX(received_at) FROM weighings),(SELECT MAX(received_at) FROM zone_events)) AS received"
    ).fetchone()


def as_time(value):
    return value.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S") if value else "—"


def export_book(rows, meta, start, end, print_rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "Operatsiyalar"
    sheet.append(["UMUMIY OPERATSIYALAR HISOBOTI"])
    sheet.append([f"Davr: {start} — {end}; vaqt: Asia/Tashkent (UTC+05:00)"])
    sheet.append(
        [
            "Ma’lumot olingan vaqt",
            as_time(meta["at"]),
            "Oxirgi kelgan yozuv",
            as_time(meta["received"]),
        ]
    )
    columns = [
        ("operation_id", "ID"),
        ("receipt_no", "Chek"),
        ("queue_no", "Navbat"),
        ("plate", "Avtomobil"),
        ("point", "Nuqta"),
        ("operator_name", "Pulni olgan xodim"),
        ("occurred", "Amal vaqti"),
        ("received_at", "Serverga kelgan vaqt"),
        ("reason", "To‘lov sababi / xizmat"),
        ("method", "To‘lov usuli"),
        ("amount", "Summa (so‘m)"),
        ("status", "Holat"),
        ("weight_kg", "Vazn (kg)"),
        ("weighing_fee", "Tarozi"),
        ("entry_fee", "Hudud xizmati"),
        ("reload_fee", "Qayta yuklash"),
        ("parking_fee", "Parkovka / KPP"),
        ("entry_time", "Tashrif boshlangan"),
        ("exit_time", "Chiqish / to‘lov vaqti"),
        ("days", "Kun"),
        ("daily_rate", "Kunlik tarif"),
        ("location", "Tashrif holati"),
    ]
    sheet.append([label for _, label in columns])
    workers = {}
    points = {}
    for row in rows:
        values = []
        for key, _ in columns:
            value = row[key]
            if isinstance(value, datetime):
                value = value.astimezone(TZ).replace(tzinfo=None)
            if isinstance(value, Decimal):
                value = int(value)
            if key == "point":
                value = POINTS[value]
            if key == "method":
                value = METHODS[value]
            values.append(value)
        sheet.append(values)
        if row["status"] == "paid":
            for target, key in [
                (
                    workers,
                    (
                        row["operator_name"],
                        POINTS[row["point"]],
                        METHODS[row["method"]],
                    ),
                ),
                (points, (POINTS[row["point"]], METHODS[row["method"]])),
            ]:
                count, amount = target.get(key, (0, 0))
                target[key] = (count + 1, amount + int(row["amount"]))
    for title, group, headers in [
        ("Xodimlar", workers, ["Xodim", "Nuqta", "To‘lov", "Operatsiya", "Jami so‘m"]),
        ("Nuqtalar", points, ["Nuqta", "To‘lov", "Operatsiya", "Jami so‘m"]),
    ]:
        ws = book.create_sheet(title)
        ws.append(headers)
        for key, (count, amount) in sorted(group.items()):
            ws.append([*key, count, amount])
    journal = book.create_sheet("Chop jurnali")
    journal.append(
        [
            "Chek",
            "Xodim",
            "Urinish vaqti",
            "Natija vaqti",
            "Printer",
            "Holat",
            "Tafsilot",
            "Urinish ID",
        ]
    )
    states = {
        "requested": "So‘ralgan",
        "sent": "Yuborilgan",
        "confirmed": "Tasdiqlangan",
        "unknown": "Noma’lum",
        "failed": "Chiqmadi",
        "cancelled": "Bekor",
        "spooled": "Chop navbatida",
        "dialog_closed": "Oyna yopilgan",
    }
    for attempt in print_rows:
        journal.append(
            [
                attempt["receipt_no"],
                attempt["username"],
                attempt["created_at"].astimezone(TZ).replace(tzinfo=None),
                attempt["updated_at"].astimezone(TZ).replace(tzinfo=None),
                attempt["method"],
                states.get(attempt["state"], attempt["state"]),
                attempt["detail"],
                str(attempt["id"]),
            ]
        )
    note = book.create_sheet("Izoh")
    note.append(["Hisob vaqti", as_time(meta["at"])])
    note.append(["Vaqt mintaqasi", "Asia/Tashkent (UTC+05:00)"])
    note.append(
        [
            "Hisoblash",
            "Tushum faqat paid holatidagi yozuvlardan. Bekor va kutilayotgan yozuvlar jamiga qo‘shilmaydi.",
        ]
    )
    note.append(
        [
            "Aloqa",
            "Offline terminalning yuborilmagan amallari server hisobotida hali ko‘rinmaydi.",
        ]
    )
    note.append(
        [
            "Oldingi yozuvlar",
            "Serverga kelgan vaqti bo‘sh eski yozuvlarda ushbu vaqt ilgari qayd qilinmagan.",
        ]
    )
    for ws in book:
        header = 4 if ws == sheet else 1
        for row in ws:
            for cell in row:
                cell.font = Font(name="Calibri", size=12)
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0"
                if isinstance(cell.value, datetime):
                    cell.number_format = "dd.mm.yyyy hh:mm:ss"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in ws[header]:
            cell.fill = PatternFill("solid", fgColor="DCEAF7")
            cell.font = Font(name="Calibri", size=12, bold=True)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 26
        ws.freeze_panes = "A" + str(header + 1)
        ws.auto_filter.ref = (
            f"A{header}:{ws.cell(ws.max_row, ws.max_column).coordinate}"
        )
        ws.print_title_rows = f"1:{header}"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    output = io.BytesIO()
    book.save(output)
    output.seek(0)
    return output


def register_operations(app, pool, roles):
    @app.get("/api/admin/operations")
    @roles("admin", "techadmin")
    def operations_search():
        where, args, _start, _end, page, size = selection(request.args, False)
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            total = conn.execute(
                "SELECT count(*) n,COALESCE(SUM(amount) FILTER(WHERE status='paid'),0) total FROM operation_ledger WHERE "
                + where,
                args,
            ).fetchone()
            pages = max(1, (total["n"] + size - 1) // size)
            page = min(page, pages)
            rows = conn.execute(
                "SELECT * FROM operation_ledger WHERE "
                + where
                + " ORDER BY occurred DESC,operation_id LIMIT %s OFFSET %s",
                [*args, size, (page - 1) * size],
            ).fetchall()
            meta = stamp(conn)
        results = [
            {
                "id": r["id"],
                "kind": r["kind"],
                "receipt_no": r["receipt_no"],
                "queue_no": r["queue_no"],
                "plate_number": r["plate"],
                "price": int(r["amount"]),
                "price_fmt": f"{r['amount']:,}",
                "status": r["status"],
                "created_at": as_time(r["occurred"]),
                "operator": r["operator_name"],
                "point": POINTS[r["point"]],
                "reason": r["reason"],
                "received_at": as_time(r["received_at"]),
                "weight_fmt": f"{r['weight_kg']:,}",
                "weighing_fee_fmt": f"{r['weighing_fee']:,}",
                "entry_service": r["entry_service"],
                "entry_fee_fmt": f"{r['entry_fee']:,}",
                "reload_service": r["reload_service"],
                "reload_fee_fmt": f"{r['reload_fee']:,}",
                "total_fmt": f"{r['amount']:,}",
                "payment_method": METHODS.get(r["method"], r["method"]),
                "source": r["source"],
            }
            for r in rows
        ]
        return jsonify(
            success=True,
            results=results,
            page=page,
            per_page=size,
            total_pages=pages,
            total=total["n"],
            amount=int(total["total"]),
            as_of=as_time(meta["at"]),
            last_received=as_time(meta["received"]),
        )

    @app.get("/api/admin/operations/export.xlsx")
    @roles("admin", "techadmin")
    def operations_export():
        where, args, start, end, _, _ = selection(request.args, False)
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rows = conn.execute(
                "SELECT * FROM operation_ledger WHERE "
                + where
                + " ORDER BY occurred,operation_id LIMIT 20001",
                args,
            ).fetchall()
            if len(rows) > 20000:
                abort(
                    400,
                    description="20 000 qatordan ko‘p. Sana yoki nuqtani toraytiring.",
                )
            meta = stamp(conn)
            print_rows = conn.execute(
                """
                SELECT p.*,u.username,w.receipt_no FROM (
                    SELECT weighing_id AS entity_id,'scale' AS kind,id,user_id,created_at,updated_at,
                           method,state,'' AS detail FROM print_attempts WHERE weighing_id=ANY(%s::bigint[])
                    UNION ALL
                    SELECT event_id,'zone',id,user_id,created_at,updated_at,method,state,detail
                    FROM receipt_jobs WHERE event_id=ANY(%s::bigint[])
                ) p JOIN operation_ledger w ON w.kind=p.kind AND w.id=p.entity_id
                LEFT JOIN users u ON u.id=p.user_id ORDER BY p.created_at,p.id LIMIT 50001""",
                (
                    [r["id"] for r in rows if r["kind"] == "scale"],
                    [r["id"] for r in rows if r["kind"] == "zone"],
                ),
            ).fetchall()
            if len(print_rows) > 50000:
                abort(
                    400,
                    description="50 000 dan ko‘p chop urinishi bor. Davrni toraytiring.",
                )
        return send_file(
            export_book(
                rows,
                meta,
                start if request.args.get("start") else "Barcha sanalar",
                end if request.args.get("end") else "Hozirgacha",
                print_rows,
            ),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="XSM-umumiy-hisobot.xlsx",
        )

    @app.get("/api/admin/operations/report")
    @roles("admin", "techadmin")
    def operations_report():
        where, args, _start, _end, page, size = selection(request.args)
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with conn.pipeline():
                daily = conn.execute(
                    "SELECT (occurred AT TIME ZONE 'Asia/Tashkent')::date AS day,COUNT(*) count,SUM(amount) total FROM operation_ledger WHERE "
                    + where
                    + " AND status='paid' GROUP BY 1 ORDER BY 1 DESC",
                    args,
                )
                methods = conn.execute(
                    "SELECT method,COUNT(*) count,SUM(amount) total FROM operation_ledger WHERE "
                    + where
                    + " AND status='paid' GROUP BY 1",
                    args,
                )
                services = conn.execute(
                    "SELECT COALESCE(SUM(weighing_fee),0) weighing,COALESCE(SUM(entry_fee),0) entry,COALESCE(SUM(reload_fee),0) reload,COALESCE(SUM(parking_fee),0) parking FROM operation_ledger WHERE "
                    + where
                    + " AND status='paid'",
                    args,
                )
            days = daily.fetchall()
            methods = methods.fetchall()
            services = services.fetchone()
            meta = stamp(conn)
        pages = max(1, (len(days) + size - 1) // size)
        page = min(page, pages)
        amount = sum(int(d["total"]) for d in days)
        return jsonify(
            success=True,
            total_count=sum(d["count"] for d in days),
            total=amount,
            total_fmt=f"{amount:,}",
            total_days=len(days),
            page=page,
            total_pages=pages,
            as_of=as_time(meta["at"]),
            last_received=as_time(meta["received"]),
            daily=[
                {
                    "date": d["day"].isoformat(),
                    "label": d["day"].strftime("%d.%m"),
                    "count": d["count"],
                    "total": int(d["total"]),
                    "total_fmt": f"{d['total']:,}",
                }
                for d in days[(page - 1) * size : page * size]
            ],
            payment_methods={
                r["method"]: {"count": r["count"], "total": int(r["total"])}
                for r in methods
            },
            service_totals={k + "_fmt": f"{v:,}" for k, v in services.items()},
        )
