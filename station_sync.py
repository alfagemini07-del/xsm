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


def _infer_station_role(data: dict, conn) -> str | None:
    role = str(data.get("role") or "").strip()
    if role in {"operator", "parkovka_tashqi"}:
        return role
    for event in data.get("events", []):
        if isinstance(event, dict):
            kind = str(event.get("kind") or "")
            if kind == "scale.paid":
                return "operator"
            if kind in {"parking.in", "parking.out"}:
                return "parkovka_tashqi"
    accounts = [str(a).strip().lower() for a in data.get("accounts", []) if isinstance(a, str) and a.strip()]
    if accounts:
        row = conn.execute(
            "SELECT role FROM users WHERE LOWER(username)=ANY(%s) AND role IN ('operator','parkovka_tashqi') LIMIT 1",
            (accounts,),
        ).fetchone()
        if row:
            return row["role"]
    return None


def register_station(app, pool, prices, clean_plate):
    @app.post("/api/station/webhook")
    def station_webhook():
        data = request.get_json(silent=True) or {}
        auth_header = request.headers.get("Authorization", "")
        credential = auth_header.removeprefix("Station ").removeprefix("Device ").strip()
        if (
            not 16 <= len(credential) <= 200
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
        from devices import record_timeouts, get_client_ip, derive_pc_hw_id, deduplicate_devices

        client_ip = get_client_ip()
        cred_hash = hashlib.sha256(credential.encode()).hexdigest()

        with pool().connection() as conn:
            record_timeouts(conn)
            dev = conn.execute(
                "SELECT * FROM devices WHERE station_hash=%s FOR UPDATE",
                (cred_hash,),
            ).fetchone()

            if not dev:
                # Try matching by active session token or explicit device_id / hw_id / IP
                sess_dev = conn.execute(
                    """SELECT d.* FROM device_sessions s
                       JOIN devices d ON d.id=s.device_id
                       WHERE s.token_hash=%s FOR UPDATE OF d""",
                    (cred_hash,),
                ).fetchone()
                if sess_dev:
                    dev = sess_dev
                    if not dev["station_hash"]:
                        conn.execute(
                            "UPDATE devices SET station_hash=%s WHERE id=%s",
                            (cred_hash, dev["id"]),
                        )

            if not dev:
                inferred_role = _infer_station_role(data, conn)
                raw_did = str(data.get("device_id") or "").strip()
                raw_hw = str(
                    data.get("hw_id")
                    or data.get("machine_id")
                    or request.headers.get("X-Device-HWID")
                    or ""
                ).strip()
                pc_hw = derive_pc_hw_id(
                    role=inferred_role or "operator",
                    client_ip=client_ip,
                    station_key=credential,
                    device_id=raw_did,
                    raw_hw_id=raw_hw,
                )
                # Look for an existing PC device row for this role/HWID/IP that does not yet have station_hash bound
                candidates = []
                if raw_did:
                    try:
                        uid_val = uuid.UUID(raw_did)
                        found = conn.execute(
                            "SELECT * FROM devices WHERE id=%s FOR UPDATE", (uid_val,)
                        ).fetchone()
                        if found:
                            candidates.append(found)
                    except ValueError:
                        pass
                if not candidates and inferred_role:
                    found = conn.execute(
                        """SELECT * FROM devices
                           WHERE role=%s AND (
                               UPPER(TRIM(hw_id))=UPPER(%s)
                               OR (%s <> '' AND UPPER(TRIM(hw_id))=UPPER(%s))
                               OR (%s <> '' AND UPPER(TRIM(hw_id))=UPPER(%s))
                               OR (%s <> '' AND ip=%s)
                               OR station_hash IS NULL
                           )
                           ORDER BY
                               (UPPER(TRIM(hw_id))=UPPER(%s)) DESC,
                               (%s <> '' AND (UPPER(TRIM(hw_id))=UPPER(%s) OR UPPER(TRIM(hw_id))=UPPER(%s) OR ip=%s)) DESC,
                               approved DESC,
                               last_seen DESC NULLS LAST
                           LIMIT 1 FOR UPDATE""",
                        (
                            inferred_role,
                            pc_hw,
                            client_ip, client_ip,
                            client_ip, f"IP-{client_ip}",
                            client_ip, client_ip,
                            pc_hw,
                            client_ip, client_ip, f"IP-{client_ip}", client_ip,
                        ),
                    ).fetchone()
                    if found:
                        candidates.append(found)
                elif not candidates:
                    # Even without inferred_role, check if there is a single approved PC device with station_hash IS NULL
                    found = conn.execute(
                        """SELECT * FROM devices
                           WHERE role IN ('operator', 'parkovka_tashqi')
                             AND station_hash IS NULL
                             AND (
                                 UPPER(TRIM(hw_id))=UPPER(%s)
                                 OR (%s <> '' AND (ip=%s OR UPPER(TRIM(hw_id))=UPPER(%s) OR UPPER(TRIM(hw_id))=UPPER(%s)))
                                 OR approved=TRUE
                             )
                           ORDER BY approved DESC, last_seen DESC NULLS LAST
                           LIMIT 1 FOR UPDATE""",
                        (pc_hw, client_ip, client_ip, client_ip, f"IP-{client_ip}"),
                    ).fetchone()
                    if found:
                        candidates.append(found)

                if candidates:
                    dev = candidates[0]
                    conn.execute(
                        "UPDATE devices SET station_hash=NULL WHERE station_hash=%s AND id<>%s",
                        (cred_hash, dev["id"]),
                    )
                    conn.execute(
                        """UPDATE devices
                           SET station_hash=%s,
                               ip=COALESCE(NULLIF(%s, ''), ip),
                               hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s)
                           WHERE id=%s""",
                        (cred_hash, client_ip, pc_hw or None, dev["id"]),
                    )
                elif inferred_role:
                    # Auto-register pending PC device so techadmin can see and approve it in Devices tab
                    new_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:station:{inferred_role}:{pc_hw}")
                    default_name = (
                        f"Tarozi Kompyuteri · {client_ip or pc_hw}"
                        if inferred_role == "operator"
                        else f"E-navbat Parkovka Kompyuteri · {client_ip or pc_hw}"
                    )
                    conn.execute(
                        """INSERT INTO devices(id, name, role, ip, hw_id, station_hash, approved, blocked)
                           VALUES(%s, %s, %s, %s, %s, %s, FALSE, FALSE)
                           ON CONFLICT(id) DO UPDATE SET
                               ip = COALESCE(EXCLUDED.ip, devices.ip),
                               hw_id = COALESCE(NULLIF(TRIM(devices.hw_id), ''), EXCLUDED.hw_id),
                               station_hash = COALESCE(devices.station_hash, EXCLUDED.station_hash)""",
                        (new_id, default_name[:100], inferred_role, client_ip or None, pc_hw or None, cred_hash),
                    )
                    deduplicate_devices(conn)
                    dev = conn.execute("SELECT * FROM devices WHERE id=%s FOR UPDATE", (new_id,)).fetchone()

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
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),ip=%s WHERE id=%s",
                (client_ip or request.remote_addr, dev["id"]),
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
        """INSERT INTO zone_events(request_key,visit_id,occurred,point,action,location,operator_name,days,
      previous_paid,amount,method) VALUES(%s,%s,%s,'parkovka_tashqi',%s,%s,%s,%s,%s,%s,%s)""",
        (eid, vid, at, kind.split(".")[1], loc, worker, days, previous, amount, method),
    )
