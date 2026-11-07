"""Portable operational backup. Imports add rows atomically and never replace live data."""

import hashlib
import io
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from flask import abort, g, jsonify, request, send_file
from psycopg import DataError, IntegrityError, sql
from psycopg.types.json import Jsonb

TABLES = {
    "users": ("id", "username", "role", "created_at"),
    "settings": ("key", "value"),
    "devices": ("id", "name", "role", "created_at"),
    "import_batches": None,
    "weighings": None,
    "zone_visits": None,
    "zone_events": None,
    "device_events": None,
    "print_attempts": None,
    "receipt_jobs": None,
    "station_events": None,
    "tariff_history": None,
    "daily_sequences": None,
    "dispatch_config": None,
    "dispatch_spots": None,
    "dispatch_gates": None,
    "dispatch_numbers": None,
    "dispatch_tickets": None,
    "dispatch_log": None,
}
LEGACY_TABLES = set(TABLES) - {key for key in TABLES if key.startswith("dispatch_")}


def encode(value):
    if isinstance(value, (date, datetime, UUID)):
        return str(value)
    if isinstance(value, Decimal):
        return int(value)
    raise TypeError(type(value).__name__)


def normalized(row):
    return json.loads(json.dumps(row, default=encode))


def register_transfer(app, pool, roles):
    @app.get("/api/admin/transfer/export")
    @roles("admin", "techadmin")
    def transfer_export():
        with pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            result = {}
            for table, columns in TABLES.items():
                selection = (
                    sql.SQL(",").join(map(sql.Identifier, columns))
                    if columns
                    else sql.SQL("*")
                )
                result[table] = conn.execute(
                    sql.SQL("SELECT {} FROM {}").format(
                        selection, sql.Identifier(table)
                    )
                ).fetchall()
            at = conn.execute("SELECT NOW() at").fetchone()["at"]
        payload = json.dumps(
            {
                "format": "xsm-operations-v2",
                "created_at": at,
                "timezone": "Asia/Tashkent",
                "tables": result,
            },
            default=encode,
            ensure_ascii=False,
        ).encode()
        return send_file(
            io.BytesIO(payload),
            mimetype="application/json",
            as_attachment=True,
            download_name="XSM-umumiy-zaxira.json",
        )

    @app.post("/api/admin/transfer/import")
    @roles("techadmin")
    def transfer_import():
        upload = request.files.get("backup")
        if not upload:
            abort(400, description="XSM JSON zaxira faylini tanlang")
        raw = upload.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            abort(400, description="Zaxira 32 MB dan oshdi")
        try:
            data = json.loads(raw)
            expected = LEGACY_TABLES if data["format"] == "xsm-operations-v1" else set(TABLES)
            if data["format"] not in ("xsm-operations-v1","xsm-operations-v2") or set(data["tables"]) != expected:
                raise ValueError()
            if any(not isinstance(rows, list) for rows in data["tables"].values()):
                raise ValueError()
            if sum(len(rows) for rows in data["tables"].values()) > 100000:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            abort(400, description="Zaxira formati noto‘g‘ri")
        digest = hashlib.sha256(raw).hexdigest()
        inserted = 0
        skipped = 0
        preview = request.form.get("preview") == "1"
        try:
            with pool().connection() as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('xsm-transfer',1))"
                )
                if conn.execute(
                    "SELECT 1 FROM transfer_imports WHERE digest=%s", (digest,)
                ).fetchone():
                    return jsonify(
                        success=True,
                        inserted=0,
                        skipped=0,
                        message="Ushbu zaxira oldin import qilingan",
                    )
                with conn.transaction(force_rollback=preview):
                    # Restore stored queue numbers rather than allocating new ones in the visit trigger.
                    conn.execute("SELECT set_config('xsm.restoring','on',true)")
                    cfg_empty=not conn.execute("SELECT 1 FROM dispatch_tickets LIMIT 1").fetchone()
                    conn.execute("SELECT id FROM dispatch_config WHERE id FOR UPDATE")
                    for table, allowed in TABLES.items():
                        columns = {
                            r["column_name"]
                            for r in conn.execute(
                                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                                (table,),
                            )
                        }
                        if allowed:
                            columns = set(allowed)
                        pks = [
                            r["attname"]
                            for r in conn.execute(
                                "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY a.attnum",
                                (table,),
                            )
                        ]
                        for row in data["tables"].get(table,[]):
                            if (
                                not isinstance(row, dict)
                                or not set(row) <= columns
                                or not all(k in row for k in pks)
                            ):
                                raise ValueError("Ustun yoki kalit mos emas: " + table)
                            where = sql.SQL(" AND ").join(
                                sql.SQL("{}=%s").format(sql.Identifier(k)) for k in pks
                            )
                            existing = conn.execute(
                                sql.SQL("SELECT * FROM {} WHERE {}").format(
                                    sql.Identifier(table), where
                                ),
                                [row[k] for k in pks],
                            ).fetchone()
                            if existing:
                                canonical = normalized(existing)
                                if table in {"dispatch_config","dispatch_gates"} and cfg_empty:
                                    assignments={k:v for k,v in row.items() if k not in pks}
                                    if assignments:
                                        conn.execute(sql.SQL("UPDATE {} SET {} WHERE {}").format(sql.Identifier(table),
                                            sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in assignments),where),
                                            [Jsonb(v) if isinstance(v,(dict,list)) else v for v in assignments.values()]+[row[k] for k in pks])
                                    skipped+=1
                                    continue
                                if table=="dispatch_numbers":
                                    conn.execute("UPDATE dispatch_numbers SET number=GREATEST(number,%s) WHERE day=%s AND flow=%s",(row["number"],row["day"],row["flow"]))
                                    skipped+=1
                                    continue
                                # User credentials and live tariff configuration stay authoritative.
                                keys = (
                                    ["username", "role"]
                                    if table == "users"
                                    else list(row)
                                )
                                if table == "settings":
                                    skipped += 1
                                    continue
                                if any(canonical.get(k) != row[k] for k in keys):
                                    raise ValueError(
                                        "Mavjud yozuv farq qiladi: "
                                        + table
                                        + ". To‘liq import bekor qilindi."
                                    )
                                skipped += 1
                                continue
                            values = dict(row)
                            if table == "users":
                                values.update(password_hash="!", is_active=False)
                            if table == "devices":
                                values.update(approved=False, blocked=True)
                            conn.execute(
                                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                                    sql.Identifier(table),
                                    sql.SQL(",").join(map(sql.Identifier, values)),
                                    sql.SQL(",").join(
                                        sql.Placeholder() for _ in values
                                    ),
                                ),
                                [
                                    Jsonb(v) if isinstance(v, (dict, list)) else v
                                    for v in values.values()
                                ],
                            )
                            inserted += 1
                    conn.execute("SELECT set_config('xsm.restoring','off',true)")
                    conn.execute("""SELECT dispatch_place(v.id,v.purpose,v.location,v.started) FROM zone_visits v
                        WHERE v.location<>'closed' AND NOT EXISTS(SELECT 1 FROM dispatch_tickets t WHERE t.visit_id=v.id)
                        ORDER BY v.started,v.id""")
                    conn.execute("""INSERT INTO dispatch_numbers(day,flow,number) SELECT day,flow,max(number) FROM dispatch_tickets GROUP BY day,flow
                        ON CONFLICT(day,flow) DO UPDATE SET number=GREATEST(dispatch_numbers.number,excluded.number)""")
                    if conn.execute("""SELECT 1 FROM dispatch_tickets t JOIN zone_visits v ON v.id=t.visit_id CROSS JOIN dispatch_config c
                        WHERE t.area<>v.location OR t.flow<>CASE WHEN v.purpose='border' THEN 'CH' ELSE 'FZ' END
                        OR t.slot<1 OR t.slot>CASE WHEN t.area='parking' THEN c.parking_capacity ELSE c.inside_capacity END
                        OR EXISTS(SELECT 1 FROM dispatch_spots s WHERE s.area=t.area AND s.slot=t.slot) LIMIT 1""").fetchone():
                        raise ValueError("Navbat joyi yoki tashrif holati mos emas. Import bekor qilindi")
                    if not preview:
                        for table in TABLES:
                            sequence = (
                                conn.execute(
                                    "SELECT pg_get_serial_sequence(%s,'id') seq",
                                    (table,),
                                ).fetchone()["seq"]
                                if table
                                not in {"settings", "daily_sequences", "station_events", "dispatch_spots", "dispatch_tickets", "dispatch_numbers"}
                                else None
                            )
                            if sequence:
                                conn.execute(
                                    sql.SQL(
                                        "SELECT setval(%s,GREATEST(COALESCE((SELECT MAX(id) FROM {}),1),(SELECT last_value FROM {})),TRUE)"
                                    ).format(
                                        sql.Identifier(table),
                                        sql.Identifier(*sequence.split(".")),
                                    ),
                                    (sequence,),
                                )
                        conn.execute(
                            "INSERT INTO transfer_imports(digest,imported_by) VALUES(%s,%s)",
                            (digest, g.user["id"]),
                        )
        except (ValueError, TypeError, KeyError) as exc:
            abort(409, description=str(exc))
        except (IntegrityError, DataError):
            abort(
                409,
                description="Zaxiradagi bog‘lanish yoki qiymat mos emas. Import bekor qilindi; joriy baza saqlandi.",
            )
        return jsonify(
            success=True,
            inserted=inserted,
            skipped=skipped,
            preview=preview,
            message="Tekshiruv tugadi; baza o‘zgarmadi"
            if preview
            else "Import yakunlandi",
        )
