"""Shift handover and cash reconciliation for checkpoints and scale."""
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import abort, g, jsonify, request

TZ = ZoneInfo("Asia/Tashkent")

POINT_LABELS = {
    "ayritom": "Afg‘oniston darvozasi",
    "kpp2": "O‘zbekiston darvozasi",
    "parkovka_tashqi": "E-navbat parkovka",
    "tarozi": "Tarozi",
}


def register_shifts(app, pool, roles_required):
    @app.get("/api/admin/shifts/current")
    @roles_required("admin", "techadmin", "operator", "ayritom", "kpp2", "parkovka_tashqi")
    def get_current_shifts():
        with pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT s.*, u.username as opener_name,
                       u.username as opener_display
                FROM shifts s
                LEFT JOIN users u ON u.id = s.opened_by
                WHERE s.status = 'open'
                ORDER BY s.opened_at DESC
                """
            ).fetchall()
        return jsonify(
            success=True,
            shifts=[
                {
                    "id": r["id"],
                    "point": r["point"],
                    "point_label": POINT_LABELS.get(r["point"], r["point"]),
                    "opened_at": r["opened_at"].isoformat() if r["opened_at"] else None,
                    "opener_name": r["opener_name"],
                    "opener_display": r["opener_display"],
                    "open_note": r["open_note"],
                    "status": r["status"],
                }
                for r in rows
            ],
        )

    @app.get("/api/admin/shifts")
    @roles_required("admin", "techadmin")
    def list_shifts():
        point = request.args.get("point", "").strip()
        limit = min(int(request.args.get("limit", 50)), 100)
        sql = """
            SELECT s.*,
                   u_open.username as opener_name,
                   u_open.username as opener_display,
                   u_close.username as closer_name,
                   u_close.username as closer_display
            FROM shifts s
            LEFT JOIN users u_open ON u_open.id = s.opened_by
            LEFT JOIN users u_close ON u_close.id = s.closed_by
            WHERE 1=1
        """
        params = []
        if point and point in POINT_LABELS:
            sql += " AND s.point = %s"
            params.append(point)
        sql += " ORDER BY s.id DESC LIMIT %s"
        params.append(limit)

        with pool().connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        return jsonify(
            success=True,
            shifts=[
                {
                    "id": r["id"],
                    "point": r["point"],
                    "point_label": POINT_LABELS.get(r["point"], r["point"]),
                    "opened_at": r["opened_at"].isoformat() if r["opened_at"] else None,
                    "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
                    "opener_name": r["opener_name"],
                    "opener_display": r["opener_display"],
                    "closer_name": r["closer_name"],
                    "closer_display": r["closer_display"],
                    "system_cash": r["system_cash"],
                    "counted_cash": r["counted_cash"],
                    "difference": r["difference"],
                    "open_note": r["open_note"],
                    "note": r["note"],
                    "status": r["status"],
                }
                for r in rows
            ],
        )

    @app.post("/api/admin/shifts/open")
    @roles_required("admin", "techadmin", "operator", "ayritom", "kpp2", "parkovka_tashqi")
    def open_shift():
        data = request.get_json(silent=True) or {}
        point = str(data.get("point") or "").strip()
        note = str(data.get("note") or "").strip()

        if point not in POINT_LABELS:
            return jsonify(success=False, message=f"Noto‘g‘ri nuqta: {point}"), 400

        with pool().connection() as conn:
            # Check if there is already an open shift for this point
            active = conn.execute(
                "SELECT id, opened_at FROM shifts WHERE point = %s AND status = 'open'",
                (point,),
            ).fetchone()
            if active:
                return (
                    jsonify(
                        success=False,
                        message=f"{POINT_LABELS.get(point, point)} uchun ochiq smena mavjud (#{active['id']}). Avval uni yoping.",
                    ),
                    409,
                )

            row = conn.execute(
                """
                INSERT INTO shifts (point, opened_by, opened_at, open_note, status)
                VALUES (%s, %s, now(), %s, 'open')
                RETURNING id, opened_at
                """,
                (point, g.user["id"], note or None),
            ).fetchone()

        return jsonify(
            success=True,
            message="Smena muvaffaqiyatli ochildi",
            shift_id=row["id"],
            opened_at=row["opened_at"].isoformat(),
        )

    @app.post("/api/admin/shifts/<int:shift_id>/close")
    @roles_required("admin", "techadmin", "operator", "ayritom", "kpp2", "parkovka_tashqi")
    def close_shift(shift_id: int):
        data = request.get_json(silent=True) or {}
        counted_cash = data.get("counted_cash")
        note = str(data.get("note") or "").strip()

        if counted_cash is None:
            return jsonify(success=False, message="Sanalgan naqd pul miqdorini kiriting"), 400

        try:
            counted_cash = int(counted_cash)
            if counted_cash < 0:
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(success=False, message="Naqd pul musbat butun son bo‘lishi kerak"), 400

        with pool().connection() as conn:
            shift = conn.execute(
                "SELECT * FROM shifts WHERE id = %s FOR UPDATE", (shift_id,)
            ).fetchone()
            if not shift:
                abort(404)
            if shift["status"] == "closed":
                return jsonify(success=False, message="Ushbu smena allaqachon yopilgan"), 409

            # Calculate system cash collected during this shift
            opened_at = shift["opened_at"]
            point = shift["point"]

            if point == "tarozi":
                calc = conn.execute(
                    """
                    SELECT COALESCE(SUM(price), 0) AS total, COUNT(*) AS count
                    FROM weighings
                    WHERE payment_method = 'cash' AND status = 'paid'
                      AND paid_at >= %s AND paid_at <= now()
                    """,
                    (opened_at,),
                ).fetchone()
            else:
                calc = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count
                    FROM zone_events
                    WHERE point = %s AND method = 'cash'
                      AND occurred >= %s AND occurred <= now()
                    """,
                    (point, opened_at),
                ).fetchone()

            system_cash = int(calc["total"])
            difference = counted_cash - system_cash

            conn.execute(
                """
                UPDATE shifts
                SET status = 'closed',
                    closed_by = %s,
                    closed_at = now(),
                    system_cash = %s,
                    counted_cash = %s,
                    difference = %s,
                    note = %s
                WHERE id = %s
                """,
                (g.user["id"], system_cash, counted_cash, difference, note or None, shift_id),
            )

        return jsonify(
            success=True,
            message="Smena yopildi",
            system_cash=system_cash,
            counted_cash=counted_cash,
            difference=difference,
            transaction_count=int(calc["count"]),
        )

    @app.get("/api/admin/shifts/<int:shift_id>")
    @roles_required("admin", "techadmin")
    def get_shift_detail(shift_id: int):
        with pool().connection() as conn:
            shift = conn.execute(
                """
                SELECT s.*,
                       u_open.username as opener_name,
                       u_open.username as opener_display,
                       u_close.username as closer_name,
                       u_close.username as closer_display
                FROM shifts s
                LEFT JOIN users u_open ON u_open.id = s.opened_by
                LEFT JOIN users u_close ON u_close.id = s.closed_by
                WHERE s.id = %s
                """,
                (shift_id,),
            ).fetchone()
            if not shift:
                abort(404)

        return jsonify(
            success=True,
            shift={
                "id": shift["id"],
                "point": shift["point"],
                "point_label": POINT_LABELS.get(shift["point"], shift["point"]),
                "opened_at": shift["opened_at"].isoformat() if shift["opened_at"] else None,
                "closed_at": shift["closed_at"].isoformat() if shift["closed_at"] else None,
                "opener_name": shift["opener_name"],
                "opener_display": shift["opener_display"],
                "closer_name": shift["closer_name"],
                "closer_display": shift["closer_display"],
                "system_cash": shift["system_cash"],
                "counted_cash": shift["counted_cash"],
                "difference": shift["difference"],
                "open_note": shift["open_note"],
                "note": shift["note"],
                "status": shift["status"],
            },
        )

