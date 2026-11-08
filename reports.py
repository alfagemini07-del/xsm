"""Paginated reports and multi-sheet styled numeric XLSX / embedded-font PDF exports."""

import io
from decimal import Decimal
from xml.sax.saxutils import escape

from flask import abort, g, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
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
        "Soatlik tushum va oqim tahlili",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM-DD HH24:00')",
    ),
    "day": (
        "Kunlik tushum va operatsiyalar tahlili",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM-DD')",
    ),
    "week": (
        "Haftalik tushum tahlili",
        "TO_CHAR(DATE_TRUNC('week',w.occurred AT TIME ZONE 'Asia/Tashkent'),'YYYY-MM-DD')",
    ),
    "month": (
        "Oylik tushum tahlili",
        "TO_CHAR(w.occurred AT TIME ZONE 'Asia/Tashkent','YYYY-MM')",
    ),
    "operator": ("Xodimlar (Operatorlar) kesimida tushum", "w.operator_name"),
    "method": (
        "To‘lov turlari kesimida tushum",
        "CASE w.method WHEN 'cash' THEN 'Naqd pul' WHEN 'card' THEN 'Plastik karta' WHEN 'bank' THEN 'Hisob raqam' ELSE 'To‘lovsiz' END",
    ),
    "vehicle": ("Avtomobillar faolligi reyestri", "w.plate"),
    "point": (
        "Nuqtalar (Postlar) kesimida tushum",
        "CASE w.point WHEN 'scale' THEN 'Tarozi (Free Zone)' WHEN 'parkovka_tashqi' THEN 'E-navbat parkovka' WHEN 'kpp2' THEN 'O‘zbekiston darvozasi (KPP2)' ELSE 'Afg‘oniston darvozasi (KPP1)' END",
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
                + " AS label,COUNT(*) count,COALESCE(SUM(amount),0) total,COALESCE(SUM(weighing_fee),0) weighing,COALESCE(SUM(entry_fee),0) entry,COALESCE(SUM(reload_fee),0) reload,COALESCE(SUM(parking_fee),0) parking,COALESCE(SUM(amount) FILTER(WHERE method='cash'),0) cash,COALESCE(SUM(amount) FILTER(WHERE method='card'),0) card,COALESCE(SUM(amount) FILTER(WHERE method='bank'),0) bank"
                + base
                + " AND status='paid' GROUP BY 1"
            )
            headers = [
                "Kesim / Guruh",
                "Amallar",
                "Jami (so‘m)",
                "Tarozi",
                "Hududga kirish",
                "Qayta yuklash",
                "Parkovka / KPP",
                "Naqd pul",
                "Plastik karta",
                "Hisob raqam",
            ]
            keys = ["label", "count", "total", "weighing", "entry", "reload", "parking", "cash", "card", "bank"]
            order = "total DESC, label" if mode in {"operator", "vehicle", "point", "method"} else "label DESC"
        elif mode == "services":
            title = "Xizmatlar kesimida hisobot"
            query = (
                "SELECT reason label,COUNT(*) count,COALESCE(SUM(amount),0) total,COALESCE(SUM(amount) FILTER(WHERE method='cash'),0) cash,COALESCE(SUM(amount) FILTER(WHERE method='card'),0) card,COALESCE(SUM(amount) FILTER(WHERE method='bank'),0) bank"
                + base
                + " AND status='paid' GROUP BY 1"
            )
            headers = ["Xizmat / To‘lov sababi", "Amallar", "Jami (so‘m)", "Naqd pul", "Plastik karta", "Hisob raqam"]
            keys = ["label", "count", "total", "cash", "card", "bank"]
            order = "total DESC, label"
        elif mode == "detailed":
            title = "Batafsil operatsiyalar ro‘yxati"
            query = (
                """SELECT receipt_no, COALESCE(NULLIF(queue_no,''),'—') AS queue_no, plate,
                          CASE point WHEN 'scale' THEN 'Tarozi' WHEN 'parkovka_tashqi' THEN 'E-navbat parkovka' WHEN 'kpp2' THEN 'O‘zbekiston darvozasi' ELSE 'Afg‘oniston darvozasi' END AS point_name,
                          operator_name,
                          TO_CHAR(occurred AT TIME ZONE 'Asia/Tashkent','DD.MM.YYYY HH24:MI:SS') AS time,
                          reason,
                          CASE method WHEN 'cash' THEN 'Naqd' WHEN 'card' THEN 'Karta' WHEN 'bank' THEN 'Hisob raqam' ELSE 'To‘lovsiz' END AS method_name,
                          amount, weight_kg
                """
                + base
                + " AND status='paid'"
            )
            headers = ["Chek ID", "Navbat", "Avtomobil", "Nuqta", "Xodim", "Sana va vaqt", "Xizmat", "To‘lov", "Summa (so‘m)", "Vazn (kg)"]
            keys = ["receipt_no", "queue_no", "plate", "point_name", "operator_name", "time", "reason", "method_name", "amount", "weight_kg"]
            order = "occurred DESC, operation_id DESC"
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
                "SELECT COUNT(*) FILTER(WHERE status='paid') paid,COUNT(*) FILTER(WHERE status='cancelled') cancelled,COALESCE(SUM(amount) FILTER(WHERE status='paid'),0) total,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='cash'),0) cash,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='card'),0) card,COALESCE(SUM(amount) FILTER(WHERE status='paid' AND method='bank'),0) bank,COALESCE(SUM(weighing_fee) FILTER(WHERE status='paid'),0) weighing,COALESCE(SUM(entry_fee+reload_fee) FILTER(WHERE status='paid'),0) extra,COALESCE(SUM(parking_fee) FILTER(WHERE status='paid'),0) parking"
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
            "mode": mode,
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

    def build_analytics_excel(data):
        """Builds a beautifully styled, multi-sheet Excel workbook for the selected Analytics slice + Summary."""
        book = Workbook()
        ws = book.active
        ws.title = "1. Tanlangan tahlil"

        font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        font_sub = Font(name="Calibri", size=11, italic=True, color="E2E8F0")
        font_th = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        font_body = Font(name="Calibri", size=11, color="0F172A")
        font_bold = Font(name="Calibri", size=11, bold=True, color="0F172A")
        font_green = Font(name="Calibri", size=11, bold=True, color="065F46")

        fill_navy = PatternFill("solid", fgColor="0F172A")
        fill_slate = PatternFill("solid", fgColor="1E293B")
        fill_th = PatternFill("solid", fgColor="1D4ED8")
        fill_zebra = PatternFill("solid", fgColor="F8FAFC")
        fill_white = PatternFill("solid", fgColor="FFFFFF")
        fill_total = PatternFill("solid", fgColor="D1FAE5")
        fill_kpi = PatternFill("solid", fgColor="EFF6FF")

        thin = Side(style="thin", color="CBD5E1")
        med = Side(style="medium", color="0F172A")
        dbl = Side(style="double", color="0F172A")
        border_cell = Border(left=thin, right=thin, top=thin, bottom=thin)
        border_total = Border(left=thin, right=thin, top=med, bottom=dbl)

        ncols = max(len(data["headers"]), 6)
        last_col = get_column_letter(len(data["headers"]))

        ws.append([f"{company} — {data['title'].upper()}"])
        ws.append([f"Davr: {data['start']} — {data['end']}  |  Operator: {data['operator_name']}  |  To‘lov: {data['method'] or 'Barchasi'}  |  Hisob vaqti: {data['as_of']} (Toshkent)"])
        ws.append([
            "To‘langan amallar:", data["totals"]["paid"],
            "Jami tushum (so‘m):", data["totals"]["total"],
            "Naqd pul:", data["totals"]["cash"],
            "Plastik karta:", data["totals"]["card"],
            "Hisob raqam:", data["totals"]["bank"],
        ])
        ws.append([])
        ws.append(data["headers"])

        ws.merge_cells(f"A1:{get_column_letter(ncols)}1")
        ws.merge_cells(f"A2:{get_column_letter(ncols)}2")
        ws["A1"].font = font_title
        ws["A1"].fill = fill_navy
        ws["A2"].font = font_sub
        ws["A2"].fill = fill_slate
        ws.row_dimensions[1].height = 28
        ws.row_dimensions[2].height = 22
        ws.row_dimensions[3].height = 22
        ws.row_dimensions[5].height = 26

        for cell in ws[3]:
            cell.fill = fill_kpi
            cell.border = border_cell
            if isinstance(cell.value, (int, float)):
                cell.font = font_green
                cell.number_format = "#,##0"
            else:
                cell.font = font_bold

        for cell in ws[5]:
            cell.font = font_th
            cell.fill = fill_th
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border_cell

        col_sums = [0] * len(data["headers"])
        numeric_cols = set()

        for r_idx, row in enumerate(data["rows"], start=6):
            ws.append(row)
            rf = fill_zebra if r_idx % 2 == 1 else fill_white
            for c_idx, cell in enumerate(ws[r_idx]):
                cell.fill = rf
                cell.border = border_cell
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                    cell.font = font_bold if c_idx == 0 else font_body
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0"
                    cell.font = font_green if c_idx == 2 else font_body
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    col_sums[c_idx] += cell.value
                    numeric_cols.add(c_idx)

        if data["rows"] and numeric_cols:
            tot_row = ["JAMI" if i == 0 else (col_sums[i] if i in numeric_cols else "") for i in range(len(data["headers"]))]
            ws.append(tot_row)
            last_r = ws.max_row
            ws.row_dimensions[last_r].height = 24
            for c_idx, cell in enumerate(ws[last_r]):
                cell.fill = fill_total
                cell.border = border_total
                cell.font = font_green if c_idx == 2 else font_bold
                if isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0"
                    cell.alignment = Alignment(horizontal="right", vertical="center")

        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = max((len(str(c.value or "")) for c in col[4:]), default=14)
            ws.column_dimensions[col_letter].width = max(15, min(38, max_len + 5))

        ws.freeze_panes = "A6"
        ws.auto_filter.ref = f"A5:{last_col}{max(5, 5 + len(data['rows']))}"

        stream = io.BytesIO()
        book.save(stream)
        stream.seek(0)
        return stream

    def pdf_report(title, subtitle, headers, rows, summary="", closing=False):
        output = io.BytesIO()
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            style.fontName = "Tarozi"
        styles["Normal"].fontSize = 10
        styles["Normal"].leading = 14
        styles["Heading1"].fontName = "TaroziBold"
        doc = SimpleDocTemplate(
            output,
            pagesize=landscape(A4),
            rightMargin=22,
            leftMargin=22,
            topMargin=22,
            bottomMargin=28,
        )
        p = lambda value: Paragraph(
            escape(f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value if value is not None else "—")),
            styles["Normal"],
        )
        story = [
            Paragraph(escape(title), styles["Heading1"]),
            p(company),
            p(subtitle),
            p(summary),
            Spacer(1, 12),
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
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
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
        mode = request.args.get("group", "day")
        if kind == "xlsx" and mode == "detailed":
            return app.view_functions["operations_export"]()
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
            stream = build_analytics_excel(data)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return send_file(
            stream,
            mimetype=mime,
            as_attachment=True,
            download_name=f"AIRITOM-{mode}-{data['start']}-{data['end']}.{kind}",
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
