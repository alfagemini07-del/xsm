"""One financial ledger for scale, parking and both checkpoints with multi-sheet executive Excel export."""

import io
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

TZ = ZoneInfo("Asia/Tashkent")
POINTS = {
    "scale": "Tarozi (Free Zone)",
    "parkovka_tashqi": "E-navbat parkovka",
    "ayritom": "Afg‘oniston darvozasi (KPP1)",
    "kpp2": "O‘zbekiston darvozasi (KPP2)",
}
METHODS = {"cash": "Naqd pul", "card": "Plastik karta", "bank": "Hisob raqam", "none": "To‘lovsiz"}
STATUS_LABELS = {"paid": "To‘langan", "pending": "Kutilmoqda", "cancelled": "Bekor qilingan"}


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
    """
    Builds a rich, multi-sheet, color-coded Excel (.xlsx) workbook with:
    1. Umumiy xulosa (Executive Summary by KPIs, Stations, Services & Payment Methods)
    2. Kunlik hisobot (Daily Breakdown with service & payment splits + totals)
    3. Nuqtalar va Xodimlar (Station & Operator Breakdown + totals)
    4. Batafsil operatsiyalar (Full line-by-line ledger with separate Date & Time columns)
    5. Chop jurnali va Izoh (Print Audit & Notes)
    """
    book = Workbook(write_only=True)

    # Professional Palette & Typography
    font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    font_sub = Font(name="Calibri", size=11, italic=True, color="E2E8F0")
    font_sec = Font(name="Calibri", size=12, bold=True, color="0F172A")
    font_th = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_body = Font(name="Calibri", size=11, color="0F172A")
    font_bold = Font(name="Calibri", size=11, bold=True, color="0F172A")
    font_green = Font(name="Calibri", size=11, bold=True, color="065F46")
    font_red = Font(name="Calibri", size=11, bold=True, color="991B1B")

    fill_navy = PatternFill("solid", fgColor="0F172A")
    fill_slate = PatternFill("solid", fgColor="1E293B")
    fill_th = PatternFill("solid", fgColor="1D4ED8")
    fill_sec = PatternFill("solid", fgColor="DBEAFE")
    fill_zebra = PatternFill("solid", fgColor="F8FAFC")
    fill_white = PatternFill("solid", fgColor="FFFFFF")
    fill_total = PatternFill("solid", fgColor="D1FAE5")
    fill_paid = PatternFill("solid", fgColor="ECFDF5")
    fill_cancel = PatternFill("solid", fgColor="FEF2F2")

    thin_side = Side(style="thin", color="CBD5E1")
    med_top = Side(style="medium", color="0F172A")
    double_bottom = Side(style="double", color="0F172A")
    border_cell = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    border_total = Border(left=thin_side, right=thin_side, top=med_top, bottom=double_bottom)

    align_left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_right = Alignment(horizontal="right", vertical="center")

    def make_cell(ws, value, font=font_body, fill=None, align=None, border=border_cell, num_fmt=None):
        c = WriteOnlyCell(ws, value=value)
        c.font = font
        if fill:
            c.fill = fill
        if border:
            c.border = border
        if isinstance(value, str):
            c.data_type = "s"
            c.alignment = align or align_left
        elif isinstance(value, datetime):
            c.number_format = num_fmt or "dd.mm.yyyy hh:mm:ss"
            c.alignment = align or align_center
        elif isinstance(value, (int, float)):
            c.number_format = num_fmt or "#,##0"
            c.alignment = align or align_right
        else:
            c.alignment = align or align_left
        return c

    # Pre-aggregate statistics from rows in a single pass
    paid_count = 0
    cancel_count = 0
    pending_count = 0
    total_revenue = 0
    svc_weighing = 0
    svc_entry = 0
    svc_reload = 0
    svc_parking = 0
    by_method = {"cash": [0, 0], "card": [0, 0], "bank": [0, 0], "none": [0, 0]}
    by_point = {k: {"count": 0, "cash": 0, "card": 0, "bank": 0, "total": 0} for k in POINTS}
    by_day = {}
    by_worker = {}

    for r in rows:
        amt = int(r["amount"] or 0)
        st = r["status"]
        pt = r["point"]
        m = r["method"] if r["method"] in by_method else "none"
        occ_local = r["occurred"].astimezone(TZ)
        day_str = occ_local.strftime("%Y-%m-%d")

        if st == "paid":
            paid_count += 1
            total_revenue += amt
            w_fee = int(r["weighing_fee"] or 0)
            e_fee = int(r["entry_fee"] or 0)
            r_fee = int(r["reload_fee"] or 0)
            p_fee = int(r["parking_fee"] or 0)
            svc_weighing += w_fee
            svc_entry += e_fee
            svc_reload += r_fee
            svc_parking += p_fee

            by_method[m][0] += 1
            by_method[m][1] += amt

            if pt in by_point:
                by_point[pt]["count"] += 1
                by_point[pt]["total"] += amt
                if m in ("cash", "card", "bank"):
                    by_point[pt][m] += amt

            d_rec = by_day.setdefault(day_str, {
                "count": 0, "weighing": 0, "entry": 0, "reload": 0, "parking": 0,
                "cash": 0, "card": 0, "bank": 0, "total": 0
            })
            d_rec["count"] += 1
            d_rec["weighing"] += w_fee
            d_rec["entry"] += e_fee
            d_rec["reload"] += r_fee
            d_rec["parking"] += p_fee
            if m in ("cash", "card", "bank"):
                d_rec[m] += amt
            d_rec["total"] += amt

            w_key = (r["operator_name"] or "—", POINTS.get(pt, pt), METHODS.get(m, m))
            w_rec = by_worker.setdefault(w_key, {"count": 0, "scale": 0, "zone": 0, "total": 0})
            w_rec["count"] += 1
            w_rec["scale"] += w_fee + e_fee + r_fee
            w_rec["zone"] += p_fee
            w_rec["total"] += amt
        elif st == "cancelled":
            cancel_count += 1
        else:
            pending_count += 1

    # =========================================================================
    # SHEET 1: 1. Umumiy xulosa (Executive Summary)
    # =========================================================================
    ws_sum = book.create_sheet("1. Umumiy xulosa")
    for idx, w in enumerate([32, 18, 22, 22, 22, 22, 16], start=1):
        ws_sum.column_dimensions[get_column_letter(idx)].width = w

    ws_sum.append([make_cell(ws_sum, "AIRITOM LOGISTICS CENTER — UMUMIY MOLIYAVIY VA OPERATSION XULOSA", font_title, fill_navy, align_left, None)])
    ws_sum.append([make_cell(ws_sum, f"Hisobot davri: {start} — {end}  |  Vaqt mintaqasi: Asia/Tashkent (UTC+05:00)  |  Shakllangan vaqti: {as_time(meta['at'])}", font_sub, fill_slate, align_left, None)])
    ws_sum.append([])

    # Section A: Asosiy ko'rsatkichlar
    ws_sum.append([make_cell(ws_sum, "1. ASOSIY MOLIYAVIY KO‘RSATKICHLAR", font_sec, fill_sec, align_left)])
    ws_sum.append([
        make_cell(ws_sum, "Ko‘rsatkich nomi", font_th, fill_th, align_left),
        make_cell(ws_sum, "Soni / Miqdori", font_th, fill_th, align_right),
        make_cell(ws_sum, "Summasi (so‘m)", font_th, fill_th, align_right),
        make_cell(ws_sum, "Ulushi (%)", font_th, fill_th, align_right),
    ])
    kpi_rows = [
        ("Jami to‘langan operatsiyalar", paid_count, total_revenue, 1.0 if total_revenue else 0.0),
        ("Naqd pul orqali tushum", by_method["cash"][0], by_method["cash"][1], by_method["cash"][1] / total_revenue if total_revenue else 0.0),
        ("Plastik karta orqali tushum", by_method["card"][0], by_method["card"][1], by_method["card"][1] / total_revenue if total_revenue else 0.0),
        ("Bank hisob raqami orqali tushum", by_method["bank"][0], by_method["bank"][1], by_method["bank"][1] / total_revenue if total_revenue else 0.0),
        ("Tarozi (vazn o‘lchash) xizmati", "", svc_weighing, svc_weighing / total_revenue if total_revenue else 0.0),
        ("Hududga kirish xizmati", "", svc_entry, svc_entry / total_revenue if total_revenue else 0.0),
        ("Qayta yuklash xizmati", "", svc_reload, svc_reload / total_revenue if total_revenue else 0.0),
        ("Parkovka va KPP darvozalari", "", svc_parking, svc_parking / total_revenue if total_revenue else 0.0),
        ("Bekor qilingan / Kutilayotgan cheklar", cancel_count + pending_count, 0, 0.0),
    ]
    for i, (k_label, k_cnt, k_sum, k_pct) in enumerate(kpi_rows):
        row_fill = fill_total if i == 0 else (fill_zebra if i % 2 == 1 else fill_white)
        row_font = font_bold if i == 0 else font_body
        ws_sum.append([
            make_cell(ws_sum, k_label, row_font, row_fill),
            make_cell(ws_sum, k_cnt, row_font, row_fill),
            make_cell(ws_sum, k_sum, font_green if i == 0 else row_font, row_fill),
            make_cell(ws_sum, k_pct, row_font, row_fill, align_right, border_cell, "0.0%"),
        ])
    ws_sum.append([])

    # Section B: Nuqtalar (Postlar) kesimida
    ws_sum.append([make_cell(ws_sum, "2. NUQTALAR (POSTLAR) KESIMIDA TUSHUM", font_sec, fill_sec, align_left)])
    ws_sum.append([
        make_cell(ws_sum, "Nuqta / Post nomi", font_th, fill_th, align_left),
        make_cell(ws_sum, "Operatsiyalar", font_th, fill_th, align_right),
        make_cell(ws_sum, "Naqd pul (so‘m)", font_th, fill_th, align_right),
        make_cell(ws_sum, "Plastik karta (so‘m)", font_th, fill_th, align_right),
        make_cell(ws_sum, "Hisob raqam (so‘m)", font_th, fill_th, align_right),
        make_cell(ws_sum, "Jami tushum (so‘m)", font_th, fill_th, align_right),
        make_cell(ws_sum, "Ulushi (%)", font_th, fill_th, align_right),
    ])
    for i, (pt_code, pt_name) in enumerate(POINTS.items()):
        p_info = by_point[pt_code]
        rf = fill_zebra if i % 2 == 1 else fill_white
        pct = p_info["total"] / total_revenue if total_revenue else 0.0
        ws_sum.append([
            make_cell(ws_sum, pt_name, font_bold, rf),
            make_cell(ws_sum, p_info["count"], font_body, rf),
            make_cell(ws_sum, p_info["cash"], font_body, rf),
            make_cell(ws_sum, p_info["card"], font_body, rf),
            make_cell(ws_sum, p_info["bank"], font_body, rf),
            make_cell(ws_sum, p_info["total"], font_green, rf),
            make_cell(ws_sum, pct, font_body, rf, align_right, border_cell, "0.0%"),
        ])
    ws_sum.append([
        make_cell(ws_sum, "JAMI", font_bold, fill_total, align_left, border_total),
        make_cell(ws_sum, paid_count, font_bold, fill_total, align_right, border_total),
        make_cell(ws_sum, by_method["cash"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_sum, by_method["card"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_sum, by_method["bank"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_sum, total_revenue, font_green, fill_total, align_right, border_total),
        make_cell(ws_sum, 1.0 if total_revenue else 0.0, font_bold, fill_total, align_right, border_total, "0.0%"),
    ])

    # =========================================================================
    # SHEET 2: 2. Kunlik hisobot (Daily Breakdown)
    # =========================================================================
    ws_day = book.create_sheet("2. Kunlik hisobot")
    day_widths = [16, 15, 18, 18, 18, 20, 18, 18, 18, 22, 18]
    for idx, w in enumerate(day_widths, start=1):
        ws_day.column_dimensions[get_column_letter(idx)].width = w
    ws_day.freeze_panes = "A4"

    ws_day.append([make_cell(ws_day, "KUNLIK OPERATSIYALAR VA TUSHUM REYESTRI", font_title, fill_navy, align_left, None)])
    ws_day.append([make_cell(ws_day, f"Davr: {start} — {end}  |  Barcha nuqtalar va xizmatlar kesimida", font_sub, fill_slate, align_left, None)])
    day_headers = [
        "Sana", "Amallar soni", "Tarozi (so‘m)", "Hududga kirish", "Qayta yuklash",
        "Parkovka / KPP", "Naqd pul", "Plastik karta", "Hisob raqam", "Jami tushum (so‘m)", "O‘rtacha chek"
    ]
    ws_day.append([make_cell(ws_day, h, font_th, fill_th, align_center) for h in day_headers])

    sorted_days = sorted(by_day.items(), reverse=True)
    for i, (d_str, d_val) in enumerate(sorted_days):
        rf = fill_zebra if i % 2 == 1 else fill_white
        avg_chk = round(d_val["total"] / d_val["count"]) if d_val["count"] else 0
        ws_day.append([
            make_cell(ws_day, d_str, font_bold, rf, align_center),
            make_cell(ws_day, d_val["count"], font_body, rf),
            make_cell(ws_day, d_val["weighing"], font_body, rf),
            make_cell(ws_day, d_val["entry"], font_body, rf),
            make_cell(ws_day, d_val["reload"], font_body, rf),
            make_cell(ws_day, d_val["parking"], font_body, rf),
            make_cell(ws_day, d_val["cash"], font_body, rf),
            make_cell(ws_day, d_val["card"], font_body, rf),
            make_cell(ws_day, d_val["bank"], font_body, rf),
            make_cell(ws_day, d_val["total"], font_green, rf),
            make_cell(ws_day, avg_chk, font_body, rf),
        ])
    ws_day.append([
        make_cell(ws_day, "JAMI", font_bold, fill_total, align_center, border_total),
        make_cell(ws_day, paid_count, font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, svc_weighing, font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, svc_entry, font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, svc_reload, font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, svc_parking, font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, by_method["cash"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, by_method["card"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, by_method["bank"][1], font_bold, fill_total, align_right, border_total),
        make_cell(ws_day, total_revenue, font_green, fill_total, align_right, border_total),
        make_cell(ws_day, round(total_revenue / paid_count) if paid_count else 0, font_bold, fill_total, align_right, border_total),
    ])
    ws_day.auto_filter.ref = f"A3:K{len(sorted_days) + 3}"

    # =========================================================================
    # SHEET 3: 3. Nuqtalar va Xodimlar (Operators & Stations)
    # =========================================================================
    ws_op = book.create_sheet("3. Nuqtalar va Xodimlar")
    for idx, w in enumerate([24, 30, 18, 16, 20, 20, 22], start=1):
        ws_op.column_dimensions[get_column_letter(idx)].width = w
    ws_op.freeze_panes = "A4"

    ws_op.append([make_cell(ws_op, "XODIMLAR VA NUQTALAR KESIMIDA HISOBOT", font_title, fill_navy, align_left, None)])
    ws_op.append([make_cell(ws_op, f"Davr: {start} — {end}  |  Har bir operator va to‘lov usuli bo‘yicha", font_sub, fill_slate, align_left, None)])
    op_headers = ["Xodim (Operator)", "Nuqta / Post", "To‘lov usuli", "Amallar soni", "Tarozi xizmatlari", "Parkovka / KPP", "Jami tushum (so‘m)"]
    ws_op.append([make_cell(ws_op, h, font_th, fill_th, align_center) for h in op_headers])

    sorted_workers = sorted(by_worker.items(), key=lambda x: (-x[1]["total"], x[0][0]))
    for i, ((op_name, pt_label, m_label), w_val) in enumerate(sorted_workers):
        rf = fill_zebra if i % 2 == 1 else fill_white
        ws_op.append([
            make_cell(ws_op, op_name, font_bold, rf),
            make_cell(ws_op, pt_label, font_body, rf),
            make_cell(ws_op, m_label, font_body, rf, align_center),
            make_cell(ws_op, w_val["count"], font_body, rf),
            make_cell(ws_op, w_val["scale"], font_body, rf),
            make_cell(ws_op, w_val["zone"], font_body, rf),
            make_cell(ws_op, w_val["total"], font_green, rf),
        ])
    ws_op.append([
        make_cell(ws_op, "JAMI", font_bold, fill_total, align_left, border_total),
        make_cell(ws_op, "", font_bold, fill_total, align_left, border_total),
        make_cell(ws_op, "", font_bold, fill_total, align_center, border_total),
        make_cell(ws_op, paid_count, font_bold, fill_total, align_right, border_total),
        make_cell(ws_op, svc_weighing + svc_entry + svc_reload, font_bold, fill_total, align_right, border_total),
        make_cell(ws_op, svc_parking, font_bold, fill_total, align_right, border_total),
        make_cell(ws_op, total_revenue, font_green, fill_total, align_right, border_total),
    ])
    ws_op.auto_filter.ref = f"A3:G{len(sorted_workers) + 3}"

    # =========================================================================
    # SHEET 4: 4. Batafsil operatsiyalar (Full Detailed Ledger)
    # =========================================================================
    ws_ops = book.create_sheet("4. Batafsil operatsiyalar")
    ops_cols = [
        ("№", 7),
        ("ID", 11),
        ("Chek raqami", 16),
        ("Navbat", 12),
        ("Avtomobil raqami", 18),
        ("Nuqta / Post", 26),
        ("Xodim (Operator)", 18),
        ("Sana", 13),
        ("Vaqt", 11),
        ("Xizmat / To‘lov sababi", 32),
        ("To‘lov usuli", 15),
        ("Holati", 15),
        ("Jami summa (so‘m)", 19),
        ("Vazn (kg)", 14),
        ("Tarozi (so‘m)", 16),
        ("Hududga kirish", 16),
        ("Qayta yuklash", 16),
        ("Parkovka / KPP", 16),
        ("Kirish vaqti", 20),
        ("Chiqish / To‘lov vaqti", 20),
        ("Kun", 8),
        ("Kunlik tarif", 15),
    ]
    for idx, (_, w) in enumerate(ops_cols, start=1):
        ws_ops.column_dimensions[get_column_letter(idx)].width = w
    ws_ops.freeze_panes = "A4"
    ws_ops.page_setup.orientation = "landscape"
    ws_ops.page_setup.paperSize = Worksheet.PAPERSIZE_A3

    ws_ops.append([make_cell(ws_ops, "BATAFSIL OPERATSIYALAR REYESTRI (BARCHA NUQTALAR)", font_title, fill_navy, align_left, None)])
    ws_ops.append([make_cell(ws_ops, f"Davr: {start} — {end}  |  Jami yozuvlar: {len(rows)} ta  |  To‘langan tushum: {total_revenue:,} so‘m".replace(",", " "), font_sub, fill_slate, align_left, None)])
    ws_ops.append([make_cell(ws_ops, label, font_th, fill_th, align_center) for label, _ in ops_cols])

    for idx, r in enumerate(rows, start=1):
        occ_dt = r["occurred"].astimezone(TZ)
        ent_dt = r["entry_time"].astimezone(TZ).replace(tzinfo=None) if r["entry_time"] else ""
        ext_dt = r["exit_time"].astimezone(TZ).replace(tzinfo=None) if r["exit_time"] else ""
        st = r["status"]
        rf = fill_cancel if st == "cancelled" else (fill_paid if idx % 2 == 0 else fill_white)
        amt_font = font_red if st == "cancelled" else font_green

        ws_ops.append([
            make_cell(ws_ops, idx, font_body, rf, align_center),
            make_cell(ws_ops, r["operation_id"], font_body, rf, align_center),
            make_cell(ws_ops, r["receipt_no"], font_bold, rf, align_center),
            make_cell(ws_ops, r["queue_no"] or "—", font_body, rf, align_center),
            make_cell(ws_ops, r["plate"], font_bold, rf, align_center),
            make_cell(ws_ops, POINTS.get(r["point"], r["point"]), font_body, rf),
            make_cell(ws_ops, r["operator_name"], font_body, rf),
            make_cell(ws_ops, occ_dt.strftime("%d.%m.%Y"), font_body, rf, align_center),
            make_cell(ws_ops, occ_dt.strftime("%H:%M:%S"), font_body, rf, align_center),
            make_cell(ws_ops, r["reason"], font_body, rf),
            make_cell(ws_ops, METHODS.get(r["method"], r["method"]), font_body, rf, align_center),
            make_cell(ws_ops, STATUS_LABELS.get(st, st), amt_font if st == "cancelled" else font_body, rf, align_center),
            make_cell(ws_ops, int(r["amount"] or 0), amt_font, rf),
            make_cell(ws_ops, int(r["weight_kg"] or 0), font_body, rf),
            make_cell(ws_ops, int(r["weighing_fee"] or 0), font_body, rf),
            make_cell(ws_ops, int(r["entry_fee"] or 0), font_body, rf),
            make_cell(ws_ops, int(r["reload_fee"] or 0), font_body, rf),
            make_cell(ws_ops, int(r["parking_fee"] or 0), font_body, rf),
            make_cell(ws_ops, ent_dt, font_body, rf),
            make_cell(ws_ops, ext_dt, font_body, rf),
            make_cell(ws_ops, int(r["days"] or 1), font_body, rf, align_center),
            make_cell(ws_ops, int(r["daily_rate"] or 0), font_body, rf),
        ])
    ws_ops.auto_filter.ref = f"A3:V{len(rows) + 3}"

    # =========================================================================
    # SHEET 5: 5. Chop jurnali va Izoh
    # =========================================================================
    ws_pr = book.create_sheet("5. Chop jurnali")
    for idx, w in enumerate([18, 18, 22, 22, 16, 18, 30, 36], start=1):
        ws_pr.column_dimensions[get_column_letter(idx)].width = w
    ws_pr.freeze_panes = "A2"
    pr_headers = ["Chek", "Xodim", "Urinish vaqti", "Natija vaqti", "Printer", "Holat", "Tafsilot", "Urinish ID"]
    ws_pr.append([make_cell(ws_pr, h, font_th, fill_th, align_center) for h in pr_headers])
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
    for i, attempt in enumerate(print_rows):
        rf = fill_zebra if i % 2 == 1 else fill_white
        ws_pr.append([
            make_cell(ws_pr, attempt["receipt_no"], font_bold, rf),
            make_cell(ws_pr, attempt["username"] or "—", font_body, rf),
            make_cell(ws_pr, attempt["created_at"].astimezone(TZ).replace(tzinfo=None), font_body, rf),
            make_cell(ws_pr, attempt["updated_at"].astimezone(TZ).replace(tzinfo=None), font_body, rf),
            make_cell(ws_pr, attempt["method"], font_body, rf, align_center),
            make_cell(ws_pr, states.get(attempt["state"], attempt["state"]), font_body, rf, align_center),
            make_cell(ws_pr, attempt["detail"] or "", font_body, rf),
            make_cell(ws_pr, str(attempt["id"]), font_body, rf),
        ])

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
                + " ORDER BY occurred DESC,operation_id LIMIT 20001",
                args,
            ).fetchall()
            if len(rows) > 20000:
                abort(
                    400,
                    description="20 000 qatordan ko‘p. Sana yoki nuqtani toraytiring.",
                )
            meta = stamp(conn)
            scale_ids = [r["id"] for r in rows if r["kind"] == "scale"]
            zone_ids = [r["id"] for r in rows if r["kind"] == "zone"]
            print_rows = []
            if scale_ids or zone_ids:
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
                    (scale_ids, zone_ids),
                ).fetchall()
            if len(print_rows) > 50000:
                abort(
                    400,
                    description="50 000 dan ko‘p chop urinishi bor. Davrni toraytiring.",
                )
        s_lbl = start.isoformat() if request.args.get("start") else "Barcha-sanalar"
        e_lbl = end.isoformat() if request.args.get("end") else "Hozirgacha"
        return send_file(
            export_book(
                rows,
                meta,
                s_lbl,
                e_lbl,
                print_rows,
            ),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"AIRITOM-Hisobot-{s_lbl}-{e_lbl}.xlsx",
        )

    @app.get("/api/admin/operations/report")
    @roles("admin", "techadmin")
    def operations_report():
        where, args, _start, _end, page, size = selection(request.args)
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with conn.pipeline():
                daily_cur = conn.execute(
                    """SELECT (occurred AT TIME ZONE 'Asia/Tashkent')::date AS day,
                              COUNT(*) count,
                              COALESCE(SUM(amount),0) total,
                              COALESCE(SUM(weighing_fee),0) weighing,
                              COALESCE(SUM(entry_fee),0) entry,
                              COALESCE(SUM(reload_fee),0) reload,
                              COALESCE(SUM(parking_fee),0) parking,
                              COALESCE(SUM(amount) FILTER (WHERE method='cash'),0) cash,
                              COALESCE(SUM(amount) FILTER (WHERE method='card'),0) card,
                              COALESCE(SUM(amount) FILTER (WHERE method='bank'),0) bank
                       FROM operation_ledger WHERE """
                    + where
                    + " AND status='paid' GROUP BY 1 ORDER BY 1 DESC",
                    args,
                )
                methods_cur = conn.execute(
                    "SELECT method,COUNT(*) count,COALESCE(SUM(amount),0) total FROM operation_ledger WHERE "
                    + where
                    + " AND status='paid' GROUP BY 1",
                    args,
                )
                services_cur = conn.execute(
                    """SELECT COALESCE(SUM(weighing_fee),0) weighing,
                              COALESCE(SUM(entry_fee),0) entry,
                              COALESCE(SUM(reload_fee),0) reload,
                              COALESCE(SUM(parking_fee),0) parking,
                              COUNT(*) FILTER (WHERE kind='scale') scale_count,
                              COUNT(*) FILTER (WHERE kind='zone') zone_count
                       FROM operation_ledger WHERE """
                    + where
                    + " AND status='paid'",
                    args,
                )
                points_cur = conn.execute(
                    """SELECT point,
                              COUNT(*) count,
                              COALESCE(SUM(amount),0) total,
                              COALESCE(SUM(amount) FILTER (WHERE method='cash'),0) cash,
                              COALESCE(SUM(amount) FILTER (WHERE method='card'),0) card,
                              COALESCE(SUM(amount) FILTER (WHERE method='bank'),0) bank
                       FROM operation_ledger WHERE """
                    + where
                    + " AND status='paid' GROUP BY 1 ORDER BY total DESC",
                    args,
                )
                operators_cur = conn.execute(
                    """SELECT operator_name,
                              point,
                              COUNT(*) count,
                              COALESCE(SUM(amount),0) total,
                              COALESCE(SUM(amount) FILTER (WHERE method='cash'),0) cash,
                              COALESCE(SUM(amount) FILTER (WHERE method='card'),0) card,
                              COALESCE(SUM(amount) FILTER (WHERE method='bank'),0) bank
                       FROM operation_ledger WHERE """
                    + where
                    + " AND status='paid' GROUP BY 1, 2 ORDER BY total DESC LIMIT 50",
                    args,
                )
                hourly_cur = conn.execute(
                    """SELECT EXTRACT(HOUR FROM (occurred AT TIME ZONE 'Asia/Tashkent'))::int AS hr,
                              COUNT(*) count,
                              COALESCE(SUM(amount),0) total
                       FROM operation_ledger WHERE """
                    + where
                    + " AND status='paid' GROUP BY 1 ORDER BY 1 ASC",
                    args,
                )
            days = daily_cur.fetchall()
            methods = methods_cur.fetchall()
            services = services_cur.fetchone()
            points_rows = points_cur.fetchall()
            operators_rows = operators_cur.fetchall()
            hourly_rows = hourly_cur.fetchall()
            meta = stamp(conn)

        pages = max(1, (len(days) + size - 1) // size)
        page = min(page, pages)
        amount = sum(int(d["total"]) for d in days)
        total_cnt = sum(int(d["count"]) for d in days)

        by_point = []
        for p_code, p_label in POINTS.items():
            matched = next((r for r in points_rows if r["point"] == p_code), None)
            p_tot = int(matched["total"]) if matched else 0
            p_cnt = int(matched["count"]) if matched else 0
            by_point.append({
                "point": p_code,
                "label": p_label,
                "count": p_cnt,
                "total": p_tot,
                "total_fmt": f"{p_tot:,}".replace(",", " "),
                "cash": int(matched["cash"]) if matched else 0,
                "card": int(matched["card"]) if matched else 0,
                "bank": int(matched["bank"]) if matched else 0,
                "share_pct": round(p_tot * 100.0 / amount, 1) if amount else 0.0,
            })

        by_operator = [
            {
                "operator": r["operator_name"] or "—",
                "point": POINTS.get(r["point"], r["point"]),
                "count": int(r["count"]),
                "total": int(r["total"]),
                "total_fmt": f"{int(r['total']):,}".replace(",", " "),
                "cash": int(r["cash"]),
                "card": int(r["card"]),
                "bank": int(r["bank"]),
                "share_pct": round(int(r["total"]) * 100.0 / amount, 1) if amount else 0.0,
            }
            for r in operators_rows
        ]

        hr_map = {r["hr"]: r for r in hourly_rows}
        hourly = [
            {
                "hour": f"{h:02d}:00",
                "label": f"{h:02d}",
                "count": int(hr_map[h]["count"]) if h in hr_map else 0,
                "total": int(hr_map[h]["total"]) if h in hr_map else 0,
            }
            for h in range(24)
        ]

        return jsonify(
            success=True,
            total_count=total_cnt,
            total=amount,
            total_fmt=f"{amount:,}".replace(",", " "),
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
                    "total_fmt": f"{d['total']:,}".replace(",", " "),
                    "weighing": int(d["weighing"]),
                    "entry": int(d["entry"]),
                    "reload": int(d["reload"]),
                    "parking": int(d["parking"]),
                    "cash": int(d["cash"]),
                    "card": int(d["card"]),
                    "bank": int(d["bank"]),
                }
                for d in days[(page - 1) * size : page * size]
            ],
            payment_methods={
                r["method"]: {
                    "count": r["count"],
                    "total": int(r["total"]),
                    "total_fmt": f"{int(r['total']):,}".replace(",", " "),
                    "share_pct": round(int(r["total"]) * 100.0 / amount, 1) if amount else 0.0,
                }
                for r in methods
            },
            service_totals={
                "weighing": int(services["weighing"]),
                "entry": int(services["entry"]),
                "reload": int(services["reload"]),
                "parking": int(services["parking"]),
                "weighing_fmt": f"{int(services['weighing']):,}".replace(",", " "),
                "entry_fmt": f"{int(services['entry']):,}".replace(",", " "),
                "reload_fmt": f"{int(services['reload']):,}".replace(",", " "),
                "parking_fmt": f"{int(services['parking']):,}".replace(",", " "),
                "scale_count": int(services["scale_count"]),
                "zone_count": int(services["zone_count"]),
            },
            by_point=by_point,
            by_operator=by_operator,
            hourly=hourly,
        )
