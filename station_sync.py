"""Acknowledge a local transaction only after its reporting rows commit."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import abort, jsonify, request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

TZ = ZoneInfo("Asia/Tashkent")


def integer(data, key, limit=3_000_000_000):
    value = data[key]
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError(key)
    return value


def _resolve_or_bind_station_device(conn, cred_hash, data, accounts, events, remote_ip, credential):
    """Resolve only an exact desktop identity; never inherit another PC's approval."""
    from devices import derive_pc_hw_id

    dev = conn.execute(
        "SELECT * FROM devices WHERE station_hash=%s FOR UPDATE",
        (cred_hash,),
    ).fetchone()
    if dev:
        return dev

    inferred_role = str(data.get("role") or "").strip()
    if inferred_role not in {"operator", "parkovka_tashqi"}:
        return None

    raw_did = str(data.get("device_id") or "").strip()
    raw_hw = str(
        data.get("hw_id")
        or data.get("machine_id")
        or request.headers.get("X-Device-HWID")
        or ""
    ).strip()
    pc_hw = derive_pc_hw_id(
        role=inferred_role,
        client_ip=remote_ip,
        station_key=credential,
        device_id=raw_did,
        raw_hw_id=raw_hw,
    )

    def _bind_to(device):
        if device["role"] != inferred_role or device["blocked"]:
            return None
        if device["station_hash"] and not secrets.compare_digest(device["station_hash"], cred_hash):
            return None
        conn.execute(
            """UPDATE devices
               SET station_hash=%s,
                   ip=COALESCE(NULLIF(%s, ''), ip),
                   hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s)
               WHERE id=%s""",
            (cred_hash, remote_ip or "", pc_hw or None, device["id"]),
        )
        return conn.execute("SELECT * FROM devices WHERE id=%s", (device["id"],)).fetchone()

    if raw_did:
        try:
            uid_val = uuid.UUID(raw_did)
            found = conn.execute("SELECT * FROM devices WHERE id=%s FOR UPDATE", (uid_val,)).fetchone()
            if found:
                return _bind_to(found)
        except ValueError:
            return None

    if pc_hw:
        exact = conn.execute(
            """SELECT * FROM devices
               WHERE role=%s AND UPPER(TRIM(hw_id))=UPPER(%s)
               ORDER BY created_at LIMIT 2 FOR UPDATE""",
            (inferred_role, pc_hw),
        ).fetchall()
        if len(exact) == 1:
            return _bind_to(exact[0])

    # Unknown identities stay pending until an explicit techadmin approval.
    new_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:station:{inferred_role}:{pc_hw}")
    default_name = (
        f"Tarozi Kompyuteri · {remote_ip or pc_hw}"
        if inferred_role == "operator"
        else f"E-navbat Parkovka Kompyuteri · {remote_ip or pc_hw}"
    )
    conn.execute(
        """INSERT INTO devices(id, name, role, ip, hw_id, station_hash, approved, blocked)
           VALUES(%s, %s, %s, %s, %s, %s, FALSE, FALSE)
           ON CONFLICT(id) DO UPDATE SET
               ip = COALESCE(EXCLUDED.ip, devices.ip),
               hw_id = COALESCE(NULLIF(TRIM(devices.hw_id), ''), EXCLUDED.hw_id),
               station_hash = COALESCE(devices.station_hash, EXCLUDED.station_hash)""",
        (new_id, default_name[:100], inferred_role, remote_ip or None, pc_hw or None, cred_hash),
    )
    return conn.execute("SELECT * FROM devices WHERE id=%s FOR UPDATE", (new_id,)).fetchone()


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
        from devices import record_timeouts, get_client_ip

        client_ip = get_client_ip()
        cred_hash = hashlib.sha256(credential.encode()).hexdigest()

        backlog = data.get("backlog")
        counts_json = None
        if isinstance(backlog, dict):
            try:
                counts = {key: integer(backlog, key, 2**63 - 1) for key in ("count", "amount", "rejected")}
            except (ValueError, TypeError, KeyError):
                abort(400, description="Yuborish navbati qiymatlari noto‘g‘ri")
            counts["oldest"] = str(backlog.get("oldest") or "")[:40]
            counts_json = Jsonb(counts)

        with pool().connection() as conn:
            dev = _resolve_or_bind_station_device(conn, cred_hash, data, accounts, events, client_ip, credential)
            if not dev or not dev["approved"] or dev["blocked"]:
                abort(403)
            last_u = str(accounts[0]).strip()[:40] if accounts else dev["last_user"]
            with conn.pipeline():
                record_timeouts(conn)
                if (
                    dev["last_seen"]
                    and (datetime.now(timezone.utc) - dev["last_seen"]).total_seconds()
                    > 100
                ):
                    conn.execute(
                        "INSERT INTO device_events(device_id,event,previous_seen) VALUES(%s,'reconnected',%s)",
                        (dev["id"], dev["last_seen"]),
                    )
                if counts_json is not None:
                    conn.execute(
                        """UPDATE devices
                           SET connected=TRUE, last_seen=NOW(), ip=%s,
                               last_user=COALESCE(%s, last_user),
                               backlog=%s, backlog_at=NOW()
                           WHERE id=%s""",
                        (client_ip or request.remote_addr, last_u, counts_json, dev["id"]),
                    )
                else:
                    conn.execute(
                        """UPDATE devices
                           SET connected=TRUE, last_seen=NOW(), ip=%s,
                               last_user=COALESCE(%s, last_user)
                           WHERE id=%s""",
                        (client_ip or request.remote_addr, last_u, dev["id"]),
                    )
            acknowledged, rejected = [], {}
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
            zone_access = None
            parking_state = None
            if dev["role"] == "operator":
                snapshot_at = datetime.now(timezone.utc)
                access_rows = conn.execute(
                    """SELECT v.id AS visit_id,v.plate,v.plate_key,v.entered_at,
                              COALESCE((SELECT MAX(e.id) FROM zone_events e WHERE e.visit_id=v.id),0) AS access_version
                       FROM zone_visits v
                       WHERE v.location='inside' AND v.entered_at IS NOT NULL
                       ORDER BY v.id"""
                ).fetchall()
                zone_access = dict(
                    snapshot_at=snapshot_at.isoformat(),
                    max_age_seconds=600,
                    vehicles=[dict(
                        visit_id=row["visit_id"], plate=row["plate"], plate_key=row["plate_key"],
                        entered_at=row["entered_at"].isoformat(), access_version=int(row["access_version"]),
                    ) for row in access_rows],
                )
            if dev["role"] == "parkovka_tashqi":
                snapshot_at = datetime.now(timezone.utc)
                parking_counts = conn.execute(
                    """SELECT COUNT(*) AS active,
                              COUNT(*) FILTER(WHERE purpose='border') AS border,
                              COUNT(*) FILTER(WHERE purpose='zone') AS zone
                       FROM zone_visits
                       WHERE location='parking' AND parking_departed_at IS NULL"""
                ).fetchone()
                parking_state = dict(
                    snapshot_at=snapshot_at.isoformat(),
                    active=int(parking_counts["active"]),
                    border=int(parking_counts["border"]),
                    zone=int(parking_counts["zone"]),
                )
            return jsonify(
                ack=acknowledged,
                rejected=rejected,
                accounts=versions,
                prices=prices(conn),
                zone_access=zone_access,
                parking_state=parking_state,
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
    if kind == "print.attempt":
        attempt_id = uuid.UUID(str(p["attempt_id"]))
        entity_kind = str(p["entity_kind"])
        expected_kind = "scale" if dev["role"] == "operator" else "parking"
        state = str(p["state"])
        action = str(p.get("action") or "weigh")
        if entity_kind != expected_kind or state not in {"requested","dialog_closed","spooled","confirmed","failed"}:
            raise ValueError("Chop etish turi yoki holati")
        plate, _key = clean_plate(p["plate"])
        conn.execute(
            """INSERT INTO terminal_print_attempts
               (device_id,attempt_id,entity_kind,local_id,receipt_no,plate,action,state,detail,operator_name,occurred)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(device_id,attempt_id) DO UPDATE SET
                 state=EXCLUDED.state,detail=EXCLUDED.detail,occurred=EXCLUDED.occurred,
                 received_at=NOW()
               WHERE terminal_print_attempts.entity_kind=EXCLUDED.entity_kind
                 AND terminal_print_attempts.local_id=EXCLUDED.local_id""",
            (dev["id"], attempt_id, entity_kind, integer(p,"local_id",2**63-1),
             str(p["receipt_no"])[:100], plate, action[:20], state,
             str(p.get("detail") or "")[:300], worker, at),
        )
        return
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
        visit_id = integer(p, "zone_visit_id", 2**63 - 1)
        access_version = integer(p, "eligibility_version", 2**63 - 1)
        visit = conn.execute(
            """SELECT v.id FROM zone_visits v
               WHERE v.id=%s AND v.plate_key=%s AND v.entered_at IS NOT NULL
                 AND v.entered_at<=%s
                 AND NOT EXISTS (
                     SELECT 1 FROM zone_events e
                     WHERE e.visit_id=v.id AND e.action='out'
                       AND e.point IN ('kpp2','ayritom') AND e.occurred<=%s
                 )""",
            (visit_id, key, at, at),
        ).fetchone()
        if not visit:
            raise ValueError("Avtomobil tortilgan vaqtda Free Zone ichida bo‘lmagan")
        ref = f"{dev['id']}/{p['receipt_no']}"
        conn.execute(
            """INSERT INTO weighings(receipt_no,plate_number,plate_search,price,weight_kg,
            weighing_fee,entry_fee,reload_fee,entry_service,reload_service,status,created_at,business_date,
            paid_at,created_by,payment_method,source,public_token,client_request_id,zone_visit_id,
            eligibility_state,eligibility_checked_at,eligibility_version)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'paid',%s,%s,%s,%s,%s,'desktop',%s,%s,%s,
                   'verified',%s,%s)""",
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
                visit_id,
                at,
                access_version,
            ),
        )
        return
    if dev["role"] != "parkovka_tashqi" or kind not in {"parking.in", "parking.out", "parking.queue"}:
        raise ValueError("Nuqta")
    local_id = integer(p, "local_id", 2**63 - 1)
    rate, days = integer(p, "rate"), integer(p, "days", 50000)
    purpose = p["purpose"]
    if purpose not in {"zone", "border"} or (amount and method == "none"):
        raise ValueError("Navbat yoki to‘lov")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))", (key,))
    if kind == "parking.queue":
        if purpose != "border" or amount != 0 or method != "none":
            raise ValueError("Navbatga qo‘yish ma’lumoti")
        visit = conn.execute(
            "SELECT * FROM zone_visits WHERE station_id=%s AND station_local_id=%s FOR UPDATE",
            (dev["id"], local_id),
        ).fetchone()
        queue_no = str(p.get("queue_no") or "").strip()[:80]
        if (
            not visit
            or visit["plate_key"] != key
            or visit["purpose"] != "border"
            or visit["location"] != "parking"
            or visit["parking_departed_at"] is not None
            or not queue_no
        ):
            raise ValueError("Navbatsiz parkovka tashrifi topilmadi")
        if visit["queue_no"] and visit["queue_no"] != queue_no:
            raise ValueError("Navbat raqami avval berilgan")
        conn.execute(
            """UPDATE zone_visits SET queue_no=%s,queue_ready=TRUE,
                      queued_at=COALESCE(queued_at,%s)
               WHERE id=%s""",
            (queue_no, at, visit["id"]),
        )
        return
    if kind == "parking.in":
        loc = "parking"
        if amount != rate:
            raise ValueError("Birinchi kun to‘lovi")
        queue_no = str(p.get("queue_no") or "").strip()[:80]
        queued = p.get("queued")
        if type(queued) is not bool:
            queued = bool(queue_no)
        if purpose == "zone" and not queued:
            raise ValueError("Free Zone navbati majburiy")
        if queued != bool(queue_no):
            raise ValueError("Navbat holati va raqami mos emas")
        vid = conn.execute(
            """INSERT INTO zone_visits(plate,plate_key,purpose,location,started,daily_rate,
                       station_id,station_local_id,queue_no,queue_ready,queued_at)
            VALUES(%s,%s,%s,'parking',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (plate, key, purpose, at, rate, dev["id"], local_id, queue_no,
             queued, at if queued else None),
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
