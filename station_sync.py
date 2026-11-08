"""Acknowledge a local transaction only after its reporting rows commit."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request
from psycopg.errors import UniqueViolation

TZ = ZoneInfo("Asia/Tashkent")


def integer(data, key, limit=3_000_000_000):
    value = data[key]
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError(key)
    return value


def _resolve_or_bind_station_device(conn, cred_hash, accounts, events, remote_ip):
    """
    Finds the device matching `station_hash = cred_hash`.
    If `station_hash` was not yet saved on an already-approved desktop device
    (for example, when the device was registered before approval or after a session reset),
    automatically matches the approved desktop device by operator account / role / IP and binds `station_hash`.
    """
    dev = conn.execute(
        "SELECT * FROM devices WHERE station_hash=%s FOR UPDATE",
        (cred_hash,),
    ).fetchone()
    if dev:
        if not dev["approved"] and not dev["blocked"]:
            # If another device row for the same desktop role is already approved, propagate approval
            peer = conn.execute(
                "SELECT id FROM devices WHERE role=%s AND approved=TRUE AND NOT blocked LIMIT 1",
                (dev["role"],),
            ).fetchone()
            if peer:
                conn.execute("UPDATE devices SET approved=TRUE WHERE id=%s", (dev["id"],))
                dev = conn.execute("SELECT * FROM devices WHERE id=%s", (dev["id"],)).fetchone()
        return dev

    # Determine role and operator usernames from payload accounts or events
    usernames = [str(a).strip().lower()[:40] for a in (accounts or []) if isinstance(a, str) and a.strip()]
    for ev in (events or []):
        if isinstance(ev, dict) and isinstance(ev.get("payload"), dict):
            w = str(ev["payload"].get("worker") or "").strip().lower()[:40]
            if w and w not in usernames:
                usernames.append(w)

    inferred_role = None
    if usernames:
        u_row = conn.execute(
            "SELECT username, role FROM users WHERE username = ANY(%s) AND role IN ('parkovka_tashqi', 'operator') LIMIT 1",
            (usernames,),
        ).fetchone()
        if u_row:
            inferred_role = u_row["role"]

    if not inferred_role:
        for ev in (events or []):
            if isinstance(ev, dict):
                kind = str(ev.get("kind") or "")
                if kind.startswith("parking."):
                    inferred_role = "parkovka_tashqi"
                    break
                if kind == "scale.paid":
                    inferred_role = "operator"
                    break

    if not inferred_role and remote_ip:
        ip_dev = conn.execute(
            """SELECT role FROM devices
               WHERE ip = %s AND role IN ('parkovka_tashqi', 'operator') AND NOT blocked
               ORDER BY (role = 'parkovka_tashqi') DESC, approved DESC, last_seen DESC NULLS LAST
               LIMIT 1""",
            (remote_ip,),
        ).fetchone()
        if ip_dev:
            inferred_role = ip_dev["role"]

    if not inferred_role:
        # Fallback to parkovka_tashqi if there is an approved parkovka_tashqi device
        pk_dev = conn.execute(
            "SELECT role FROM devices WHERE role='parkovka_tashqi' AND NOT blocked LIMIT 1"
        ).fetchone()
        if pk_dev:
            inferred_role = "parkovka_tashqi"

    if not inferred_role:
        return None

    def _bind_to(target_id):
        conn.execute(
            "UPDATE devices SET station_hash=NULL WHERE station_hash=%s AND id<>%s",
            (cred_hash, target_id),
        )
        conn.execute(
            "UPDATE devices SET station_hash=%s, ip=COALESCE(%s, ip) WHERE id=%s",
            (cred_hash, remote_ip or None, target_id),
        )
        return conn.execute("SELECT * FROM devices WHERE id=%s", (target_id,)).fetchone()

    # 1. Try to find a non-blocked device for this role where last_user matches one of the station accounts
    if usernames:
        candidate = conn.execute(
            """SELECT * FROM devices
               WHERE role = %s AND NOT blocked
                 AND LOWER(COALESCE(last_user, '')) = ANY(%s)
               ORDER BY approved DESC, (station_hash IS NULL) DESC, last_seen DESC NULLS LAST
               LIMIT 1 FOR UPDATE""",
            (inferred_role, usernames),
        ).fetchone()
        if candidate:
            return _bind_to(candidate["id"])

    # 2. Try to find any approved (or unblocked) device for this desktop role
    candidate = conn.execute(
        """SELECT * FROM devices
           WHERE role = %s AND NOT blocked
           ORDER BY approved DESC, (ip = %s) DESC, (station_hash IS NULL) DESC, last_seen DESC NULLS LAST, created_at ASC
           LIMIT 1 FOR UPDATE""",
        (inferred_role, remote_ip or ""),
    ).fetchone()
    if candidate:
        return _bind_to(candidate["id"])

    return None


def register_station(app, pool, prices, clean_plate):
    @app.post("/api/station/webhook")
    def station_webhook():
        data = request.get_json(silent=True) or {}
        credential = request.headers.get("Authorization", "").removeprefix("Station ").strip()
        if (
            not 40 <= len(credential) <= 200
            or (request.content_length and request.content_length > 256000)
        ):
            abort(401)
        if not isinstance(data, dict):
            abort(400)
        events = data.get("events", [])
        accounts = data.get("accounts", [])
        if (
            not isinstance(events, list)
            or len(events) > 25
            or not isinstance(accounts, list)
            or len(accounts) > 256
        ):
            abort(400)
        from devices import record_timeouts

        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        client_ip = forwarded or request.remote_addr or ""
        cred_hash = hashlib.sha256(credential.encode()).hexdigest()

        with pool().connection() as conn:
            record_timeouts(conn)
            dev = _resolve_or_bind_station_device(conn, cred_hash, accounts, events, client_ip)
            if not dev or not dev["approved"] or dev["blocked"]:
                abort(403)
            if (
                dev["last_seen"]
                and (datetime.now(timezone.utc) - dev["last_seen"]).total_seconds()
                > 100
            ):
                conn.execute(
                    "INSERT INTO device_events(device_id,event,previous_seen) VALUES(%s,'reconnected',%s)",
                    (dev["id"], dev["last_seen"]),
                )
            last_u = str(accounts[0]).strip()[:40] if accounts else dev["last_user"]
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),ip=%s,last_user=COALESCE(%s,last_user) WHERE id=%s",
                (client_ip, last_u, dev["id"]),
            )
            acknowledged, rejected = [], {}
            backlog = data.get("backlog")
            if isinstance(backlog, dict):
                try:
                    counts = {key: integer(backlog, key, 2**63 - 1) for key in ("count", "amount", "rejected")}
                except (ValueError, TypeError, KeyError):
                    abort(400, description="Yuborish navbati qiymatlari noto‘g‘ri")
                counts["oldest"] = str(backlog.get("oldest") or "")[:40]
                from psycopg.types.json import Jsonb
                conn.execute("UPDATE devices SET backlog=%s,backlog_at=NOW() WHERE id=%s", (Jsonb(counts), dev["id"]))
            for event in events:
                try:
                    eid = uuid.UUID(str(event["id"]))
                    with conn.transaction():
                        inserted = conn.execute(
                            """INSERT INTO station_events(device_id,event_id) VALUES(%s,%s)
                            ON CONFLICT DO NOTHING RETURNING event_id""",
                            (dev["id"], eid),
                        ).fetchone()
                        if inserted:
                            apply_event(conn, dev, eid, event, clean_plate)
                    acknowledged.append(str(eid))
                except (
                    KeyError,
                    ValueError,
                    TypeError,
                    OverflowError,
                    UniqueViolation,
                ) as error:
                    if not isinstance(event, dict) or "id" not in event:
                        abort(400)
                    rejected[str(event["id"])] = "Tashrif yoki chek mos kelmadi: " + (
                        str(error)[:90]
                        if not isinstance(error, UniqueViolation)
                        else "takroriy ochiq tashrif"
                    )
            users = conn.execute(
                "SELECT username,password_hash FROM users WHERE role=%s AND is_active AND username=ANY(%s)",
                (dev["role"], accounts),
            ).fetchall()
            versions = {
                u["username"]: hashlib.sha256(u["password_hash"].encode()).hexdigest()
                for u in users
            }
            return jsonify(
                ack=acknowledged,
                rejected=rejected,
                accounts=versions,
                prices=prices(conn),
            )


def apply_event(conn, dev, eid, event, clean_plate):
    p = event["payload"]
    kind = event["kind"]
    worker = str(p["worker"])[:40]
    u = conn.execute("SELECT id FROM users WHERE username=%s AND role=%s", (worker, dev["role"])).fetchone()
    if not u:
        raise ValueError("Xodim topilmadi")
    at = datetime.fromisoformat(p["occurred"])
    if not at.tzinfo:
        raise ValueError("Vaqt mintaqasi")
    plate, key = clean_plate(p["plate"])
    amount = integer(p, "amount")
    method = p["method"]
    if method not in {"cash", "card", "bank", "none"}:
        raise ValueError("To‘lov turi")
    if kind == "scale.paid" and dev["role"] == "operator":
        fees = [
            integer(p, k, 1_000_000_000)
            for k in ("weighing_fee", "entry_fee", "reload_fee")
        ]
        weight = integer(p, "weight_kg", 1_000_000)
        if not weight or sum(fees) != amount or method == "none":
            raise ValueError("Chek jami yoki vazn")
        ref = f"{dev['id']}/{p['receipt_no']}"
        conn.execute(
            """INSERT INTO weighings(receipt_no,plate_number,plate_search,price,weight_kg,
            weighing_fee,entry_fee,reload_fee,entry_service,reload_service,status,created_at,business_date,
            paid_at,created_by,payment_method,source,public_token,client_request_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'paid',%s,%s,%s,%s,%s,'desktop',%s,%s)""",
            (
                ref,
                plate,
                key,
                amount,
                weight,
                *fees,
                bool(p["entry_service"]),
                bool(p["reload_service"]),
                at,
                at.astimezone(TZ).date(),
                at,
                u["id"],
                method,
                secrets.token_urlsafe(24),
                str(eid),
            ),
        )
        return
    if dev["role"] != "parkovka_tashqi" or kind not in {"parking.in", "parking.out"}:
        raise ValueError("Nuqta")
    local_id = integer(p, "local_id", 2**63 - 1)
    rate, days = integer(p, "rate"), integer(p, "days", 50000)
    purpose = p["purpose"]
    if purpose not in {"zone", "border"} or (amount and method == "none"):
        raise ValueError("Navbat yoki to‘lov")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))", (key,))
    if kind == "parking.in":
        loc = "parking"
        if amount != rate:
            raise ValueError("Birinchi kun to‘lovi")
        vid = conn.execute(
            """INSERT INTO zone_visits(plate,plate_key,purpose,location,started,daily_rate,station_id,station_local_id,queue_no)
            VALUES(%s,%s,%s,'parking',%s,%s,%s,%s,%s) RETURNING id""",
            (plate, key, purpose, at, rate, dev["id"], local_id, str(p.get("queue_no") or p.get("receipt_no") or "")[:80]),
        ).fetchone()["id"]
        previous = 0
    else:
        visit = conn.execute(
            "SELECT * FROM zone_visits WHERE station_id=%s AND station_local_id=%s FOR UPDATE",
            (dev["id"], local_id),
        ).fetchone()
        if not visit:
            visit = conn.execute(
                "SELECT * FROM zone_visits WHERE plate_key=%s AND purpose=%s AND location='parking' ORDER BY id DESC LIMIT 1 FOR UPDATE",
                (key, purpose),
            ).fetchone()
        if not visit or visit["plate_key"] != key or visit["purpose"] != purpose:
            raise ValueError("Mahalliy kirish topilmadi")
        loc = visit["location"] if purpose == "zone" else "closed"
        vid = visit["id"]
        previous = visit["daily_rate"]
        if amount != (0 if purpose == "zone" else max(0, days * previous - previous)):
            raise ValueError("Chiqish summasi")
        conn.execute(
            "UPDATE zone_visits SET parking_departed_at=COALESCE(parking_departed_at,%s), location=%s WHERE id=%s",
            (at, loc, vid),
        )
    conn.execute(
        """INSERT INTO zone_events(request_key,visit_id,occurred,point,action,location,operator_name,days,previous_paid,amount,method)
        VALUES(%s,%s,%s,'parkovka_tashqi',%s,%s,%s,%s,%s,%s,%s)""",
        (eid, vid, at, kind[8:], loc, worker, days, previous, amount, method),
    )
