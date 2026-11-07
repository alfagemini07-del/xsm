"""Paginated reports and numeric XLSX / embedded-font PDF exports."""

import io
from decimal import Decimal
from xml.sax.saxutils import escape

from flask import abort, g, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from receipt_render import font_path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

pdfmetrics.registerFont(TTFont("Tarozi", font_path()))
pdfmetrics.registerFont(TTFont("TaroziBold", font_path(True)))

MODES = {
    "hour": (
        "Soatlik tushum",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM-DD HH24:00')",
    ),
    "day": (
        "Kunlik tushum",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM-DD')",
    ),
    "week": (
        "Haftalik tushum",
        "TO_CHAR(DATE_TRUNC('week',w.occurred AT TIME ZONE 'Asia/Tashkent'),'YYYY-MM-DD')",
    ),
    "month": (
        "Oylik tushum",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM')",
    ),
    "operator": ("Xodimlar tushumi", "w.operator_name"),
    "method": (
        "To‘lov turlari",
        "CASE w.method WHEN 'cash' THEN 'Naqd' WHEN 'card' THEN 'Karta' WHEN 'bank' THEN 'Hisob raqam' ELSE 'To‘lovsiz' END",
    ),
    "vehicle": ("Avtomobillar", "w.plate"),
    "point": (
        "Nuqtalar",
        "CASE w.point WHEN 'scale' THEN 'Tarozi' WHEN 'parkovka_tashqi' THEN 'E-navbat parkovka' WHEN 'kpp2' THEN 'O‘zbekiston darvozasi' ELSE 'Afg‘oniston darvozasi' END",
    ),
}


def register_reports(
    app, db, roles_required, now_local, parse_date, timezone_name, company
):
    from operations import as_time, selection, stamp

    def filters():
        where, params, start, end, page, size = selection(request.args)
        return (
            start,
            end,
            int(request.args.get("operator") or 0),
            request.args.get("method", ""),
            where,
            params,
            page,
            size,
        )

    def query_report(export=False, export_limit=20000):
        start, end, operator, method, where, params, page, size = filters()
        mode = request.args.get("group", "day")
        base = " FROM operation_ledger w WHERE " + where
        args = list(params)
        if mode in MODES:
            title, expr = MODES[mode]
            query = (
                "SELECT "
                + expr
                + " AS label,COUNT(*) count,SUM(amount) total,SUM(weighing_fee) weighing,SUM(entry_fee) entry,SUM(reload_fee) reload,SUM(parking_fee) parking"
                + base
                + " AND status='paid' GROUP BY 1"
            )
            headers = [
                "Guruh",
                "Amallar",
                "Jami so‘m",
                "Tarozi",
                "Hudud xizmati",
                "Qayta yuklash",
                "Parkovka / KPP",
            ]
            keys = ["label", "count", "total", "weighing", "entry", "reload", "parking"]
            order = "label"
        elif mode == "services":
            title = "Xizmatlar hisoboti"
            query = (
                "SELECT reason label,COUNT(*) count,SUM(amount) total"
                + base
                + " AND status='paid' GROUP BY 1"
            )
            headers = ["To‘lov sababi", "Amallar", "Jami so‘m"]
            keys = ["label", "count", "total"]
            order = "label"
        elif mode == "cancelled":
            title = "Bekor qilingan cheklar"
            query = (
                "SELECT receipt_no,plate,operator_name,TO_CHAR(occurred AT TIME ZONE 'Asia/Tashkent','DD.MM.YYYY HH24:MI:SS') time,amount"
                + base
                + " AND status='cancelled'"
            )
            headers = ["Chek", "Avtomobil", "Xodim", "Vaqt", "Summa"]
            keys = ["receipt_no", "plate", "operator_name", "time", "amount"]
            order = "time DESC,receipt_no"
        elif mode == "reprints":
            title = "Chop etish urinishlari"
            query = (
                "SELECT w.receipt_no,w.plate,COUNT(p.id) attempts,COUNT(*) FILTER(WHERE p.state='confirmed') confirmed,TO_CHAR(MAX(p.created_at) AT TIME ZONE 'Asia/Tashkent','DD.MM.YYYY HH24:MI:SS') time FROM operation_ledger w JOIN (SELECT 'S-'||weighing_id operation_id,id::text,state,created_at FROM print_attempts UNION ALL SELECT 'Z-'||event_id,id::text,state,created_at FROM receipt_jobs) p ON p.operation_id=w.operation_id WHERE "
                + where
                + " GROUP BY w.operation_id,w.receipt_no,w.plate"
            )
            headers = ["Chek", "Avtomobil", "Urinish", "Tasdiqlangan", "Oxirgi vaqt"]
            keys = ["receipt_no", "plate", "attempts", "confirmed", "time"]
            order = "time DESC,receipt_no"
        else:
            abort(400)
        with db().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            total = conn.execute(
                "SELECT COUNT(*) n FROM (" + query + ") q", args
            ).fetchone()["n"]
            if export and total > export_limit:
                abort(400, description="Eksport chegarasi oshdi. Davrni toraytiring.")
            page = min(page, max(1, (total + size - 1) // size))
            rows = conn.execute(
                query + " ORDER BY " + order + " LIMIT %s OFFSET %s",
                args
                + [
                    max(total, 1) if export else size,
                    0 if export else (page - 1) * size,
                ],
            ).fetchall()
            totals = conn.execute(
                "SELECT COUNT(*) FILTER(WHERE status='paid') paid,COUNT(*) FILTER(WHERE status='cancelled') cancelled,COALESCE(SUM(amount) FILTER(WHERE status='paid'),0) total,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='cash'),0) cash,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='card'),0) card,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='bank'),0) bank"
                + base,
                params,
            ).fetchone()
            operator_row = (
                conn.execute(
                    "SELECT username FROM users WHERE id=%s", (operator,)
                ).fetchone()
                if operator
                else None
            )
            meta = stamp(conn)
        return {
            "title": title,
            "headers": headers,
            "rows": [
                [int(r[k]) if isinstance(r[k], Decimal) else r[k] for k in keys]
                for r in rows
            ],
            "total": total,
            "page": page,
            "per_page": size,
            "pages": max(1, (total + size - 1) // size),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "operator": operator,
            "operator_name": operator_row["username"] if operator_row else "Barchasi",
            "method": method,
            "totals": {k: int(v) for k, v in totals.items()},
            "as_of": as_time(meta["at"]),
            "last_received": as_time(meta["received"]),
        }

    def pdf_report(title, subtitle, headers, rows, summary="", closing=False):
        output = io.BytesIO()
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            style.fontName = "Tarozi"
        styles["Normal"].fontSize = 11
        styles["Normal"].leading = 15
        styles["Heading1"].fontName = "TaroziBold"
        doc = SimpleDocTemplate(
            output,
            pagesize=landscape(A4),
            rightMargin=25,
            leftMargin=25,
            topMargin=25,
            bottomMargin=30,
        )
        p = lambda value: Paragraph(
            escape(str(value if value is not None else "—")), styles["Normal"]
        )
        story = [
            Paragraph(escape(title), styles["Heading1"]),
            p(company),
            p(subtitle),
            p(summary),
            Spacer(1, 14),
        ]
        table = Table(
            [[p(v) for v in headers]] + [[p(v) for v in row] for row in rows],
            repeatRows=1,
            colWidths=[doc.width / len(headers)] * len(headers),
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(table)
        if closing:
            story += [
                Spacer(1, 25),
                p("Topshirdi: __________________    Qabul qildi: __________________"),
                p("Imzo: __________________      Imzo: __________________"),
            ]

        def footer(canvas, document):
            canvas.setFont("Tarozi", 9)
            canvas.drawRightString(815, 15, f"Sahifa {document.page}")

        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        output.seek(0)
        return output

    @app.get("/api/admin/analytics")
    @roles_required("admin", "techadmin")
    def analytics():
        return jsonify(success=True, **query_report())

    @app.get("/api/admin/report-operators")
    @roles_required("admin", "techadmin")
    def report_operators():
        with db().connection() as conn:
            rows = conn.execute(
                "SELECT id,username FROM users ORDER BY username"
            ).fetchall()
        return jsonify(success=True, users=rows)

    @app.get("/api/admin/analytics/export/<kind>")
    @roles_required("admin", "techadmin")
    def export_analytics(kind):
        if kind not in {"xlsx", "pdf"}:
            abort(404)
        if kind == "xlsx":
            return app.view_functions["operations_export"]()
        # PDF layout keeps Paragraph objects in memory; bound it separately on small servers.
        data = query_report(export=True, export_limit=2000 if kind == "pdf" else 20000)
        subtitle = f"{data['start']} — {data['end']}; operator: {data['operator_name']}; to'lov: {data['method'] or 'Barchasi'}"
        subtitle += f"; Hisob: {data['as_of']} (Toshkent); oxirgi kelgan: {data['last_received']}"
        summary = f"To'langan: {data['totals']['paid']}; tushum: {data['totals']['total']:,} so'm; bekor: {data['totals']['cancelled']}".replace(
            ",", " "
        )
        if kind == "pdf":
            stream = pdf_report(
                data["title"], subtitle, data["headers"], data["rows"], summary
            )
            mime = "application/pdf"
        else:
            book = Workbook()
            sheet = book.active
            sheet.title = "Hisobot"
            sheet.append([data["title"]])
            sheet.append([subtitle])
            sheet.append([summary])
            sheet.append(data["headers"])
            for row in data["rows"]:
                sheet.append(row)
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = (
                            "s"  # User text must never become an Excel formula.
                        )
                    elif isinstance(cell.value, (int, float)):
                        cell.number_format = "#,##0"
            for cell in sheet[4]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="DBEAFE")
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width = 26
            sheet.freeze_panes = "A5"
            sheet.auto_filter.ref = (
                f"A4:{sheet.cell(sheet.max_row, sheet.max_column).coordinate}"
            )
            stream = io.BytesIO()
            book.save(stream)
            stream.seek(0)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return send_file(
            stream,
            mimetype=mime,
            as_attachment=True,
            download_name=f"hisobot-{data['start']}-{data['end']}.{kind}",
        )

    @app.get("/api/admin/cash-closing.pdf")
    @roles_required("admin", "techadmin")
    def cash_closing():
        start, end, operator, method, where, params, _, _ = filters()
        if start != end or method:
            abort(
                400,
                description="Dalolatnoma uchun bitta kun va barcha to'lov turlarini tanlang",
            )
        try:
            actual = int(request.args["actual_cash"])
            if not 0 <= actual <= 10**15:
                raise ValueError()
        except (KeyError, ValueError):
            abort(400, description="Kassadagi haqiqiy naqd summani kiriting")
        with db().connection() as conn:
            rows = conn.execute(
                "SELECT method AS payment_method,COUNT(*) AS count,SUM(amount) AS total FROM operation_ledger w WHERE "
                + where
                + " AND status='paid' GROUP BY method",
                params,
            ).fetchall()
            operator_name = (
                conn.execute(
                    "SELECT username FROM users WHERE id=%s", (operator,)
                ).fetchone()
                if operator
                else None
            )
        values = {r["payment_method"]: int(r["total"]) for r in rows}
        table = [
            [
                {
                    "cash": "Naqd",
                    "card": "Karta",
                    "bank": "Hisob raqam",
                    "none": "To‘lovsiz",
                }[r["payment_method"]],
                r["count"],
                int(r["total"]),
            ]
            for r in rows
        ]
        table += [
            ["Jami", sum(r["count"] for r in rows), sum(values.values())],
            ["Haqiqiy naqd", "", actual],
            ["Farq (haqiqiy − hisob)", "", actual - values.get("cash", 0)],
        ]
        subtitle = f"Sana: {start}; operator: {operator_name['username'] if operator_name else 'Barchasi'}; tuzdi: {g.user['username']}; {now_local():%d.%m.%Y %H:%M:%S}"
        stream = pdf_report(
            "Kunlik kassa yopilish dalolatnomasi",
            subtitle,
            ["To'lov turi", "Soni", "So'm"],
            table,
            "Yaratilgan paytdagi ma'lumotlar. Keyingi operatsiyalar bu nusxaga kirmaydi.",
            True,
        )
        return send_file(
            stream,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"kassa-{start}.pdf",
        )
