"""Central device enrollment and persistent IMEI/HWID/IP terminal & PC authorization."""

import hashlib
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote
from flask import abort, g, jsonify, request, render_template
from werkzeug.security import check_password_hash

PUBLIC_DEVICE_ENDPOINTS = {"device_login", "station_webhook"}
DEVICE_ENDPOINTS = {
    "device_bootstrap",
    "device_heartbeat",
    "device_logout",
    "device_parking_quote",
    "device_parking_commit",
    "device_parking_recent",
    "device_parking_receipt",
    "api_today_stats",
    "recent_plates",
    "operator_weighings",
    "create_weighing",
    "cancel_weighing",
    "pay_weighing",
    "sync_state",
    "receipt_data",
    "weighing_detail",
    "receipt_page",
    "download_receipt_pdf",
    "receipt_print_status",
}

ALLOWED_DEVICE_ROLES = {"ayritom", "kpp2", "parkovka_tashqi", "operator"}

_last_dedup_at = 0.0


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return (forwarded or request.remote_addr or "").strip()[:64]


def normalize_hw_id(raw):
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = raw.strip()
    return cleaned[:80]


def derive_pc_hw_id(role: str, client_ip: str = "", station_key: str = "", device_id: str = "", raw_hw_id: str = "") -> str:
    """
    Derives a deterministic, unique hardware/computer identifier for Desktop PC apps
    (Tarozi & E-navbat Parkovka) when an explicit IMEI/HWID is not sent, using station_key,
    device_id, or IP address.
    """
    cleaned = normalize_hw_id(raw_hw_id)
    if cleaned:
        return cleaned
    if station_key and isinstance(station_key, str) and len(station_key.strip()) >= 8:
        short_hash = hashlib.sha256(station_key.strip().encode()).hexdigest()[:12].upper()
        return f"PC-{short_hash}"
    if device_id:
        clean_uuid = str(device_id).replace("-", "").upper()[:12]
        if clean_uuid:
            return f"PC-{clean_uuid}"
    if client_ip:
        return f"IP-{client_ip}"
    return ""


def record_timeouts(conn):
    conn.execute("""WITH missed AS (
        UPDATE devices SET connected=FALSE
        WHERE connected AND last_seen < NOW()-INTERVAL '100 seconds'
        RETURNING id,last_seen)
        INSERT INTO device_events(device_id,event,occurred,previous_seen)
        SELECT id,'offline',last_seen+INTERVAL '100 seconds',last_seen FROM missed""")


def _merge_device_pair(conn, keep_id, drop_id):
    """Safely merges all foreign-key references from drop_id into keep_id and deletes drop_id."""
    if keep_id == drop_id:
        return
    conn.execute(
        "UPDATE device_events SET device_id=%s WHERE device_id=%s",
        (keep_id, drop_id),
    )
    conn.execute(
        "UPDATE receipt_jobs SET device_id=%s WHERE device_id=%s",
        (keep_id, drop_id),
    )
    conn.execute(
        """DELETE FROM station_events s USING station_events k
           WHERE s.device_id=%s AND k.device_id=%s AND s.event_id=k.event_id""",
        (drop_id, keep_id),
    )
    conn.execute(
        "UPDATE station_events SET device_id=%s WHERE device_id=%s",
        (keep_id, drop_id),
    )
    conn.execute(
        """UPDATE zone_visits SET station_id=NULL, station_local_id=NULL
           WHERE station_id=%s AND EXISTS (
               SELECT 1 FROM zone_visits z2
               WHERE z2.station_id=%s AND z2.station_local_id=zone_visits.station_local_id
           )""",
        (drop_id, keep_id),
    )
    conn.execute(
        "UPDATE zone_visits SET station_id=%s WHERE station_id=%s",
        (keep_id, drop_id),
    )
    conn.execute(
        "UPDATE device_sessions SET device_id=%s WHERE device_id=%s",
        (keep_id, drop_id),
    )
    conn.execute("DELETE FROM devices WHERE id=%s", (drop_id,))


def deduplicate_devices(conn, force: bool = False):
    """
    Merges duplicate rows in `devices`:
    1. Rows sharing the same (UPPER(hw_id), role) where hw_id is present.
    2. Legacy rows with hw_id IS NULL that match an existing row with the same (LOWER(name), role).
    3. Propagates approval across same-IMEI/hw_id/IP rows so an approved device never needs re-approval.
    """
    global _last_dedup_at
    now_mono = time.monotonic()
    if not force and (now_mono - _last_dedup_at) < 45.0:
        return 0
    _last_dedup_at = now_mono

    merged_count = 0

    # 1. Merge rows with identical (UPPER(hw_id), role)
    dup_groups = conn.execute("""
        SELECT UPPER(TRIM(hw_id)) AS norm_hw, role
        FROM devices
        WHERE hw_id IS NOT NULL AND TRIM(hw_id) <> ''
        GROUP BY UPPER(TRIM(hw_id)), role
        HAVING COUNT(*) > 1
    """).fetchall()

    for grp in dup_groups:
        rows = conn.execute("""
            SELECT * FROM devices
            WHERE UPPER(TRIM(hw_id)) = %s AND role = %s
            ORDER BY approved DESC, connected DESC, last_seen DESC NULLS LAST, created_at ASC
            FOR UPDATE
        """, (grp["norm_hw"], grp["role"])).fetchall()
        if len(rows) < 2:
            continue
        primary = rows[0]
        any_approved = any(r["approved"] for r in rows)
        all_blocked = all(r["blocked"] for r in rows)
        best_name = next(
            (r["name"] for r in rows if not r["name"].startswith(("Planshet", "Kompyuter", "Qurilma"))),
            primary["name"],
        )
        best_ip = next((r["ip"] for r in rows if r["ip"]), primary["ip"])
        best_user = next((r["last_user"] for r in rows if r["last_user"]), primary["last_user"])
        best_station_hash = next((r["station_hash"] for r in rows if r["station_hash"]), primary["station_hash"])
        for dup in rows[1:]:
            if dup["station_hash"]:
                conn.execute("UPDATE devices SET station_hash=NULL WHERE id=%s", (dup["id"],))
            _merge_device_pair(conn, primary["id"], dup["id"])
            merged_count += 1
        conn.execute("""
            UPDATE devices
            SET approved = %s,
                blocked = %s,
                name = %s,
                ip = COALESCE(%s, ip),
                last_user = COALESCE(%s, last_user),
                station_hash = COALESCE(station_hash, %s)
            WHERE id = %s
        """, (any_approved, all_blocked, best_name, best_ip, best_user, best_station_hash, primary["id"]))

    # 2. Merge legacy rows (hw_id IS NULL) into rows with the same (LOWER(name), role)
    name_groups = conn.execute("""
        SELECT LOWER(TRIM(name)) AS norm_name, role
        FROM devices
        GROUP BY LOWER(TRIM(name)), role
        HAVING COUNT(*) > 1 AND COUNT(*) FILTER (WHERE hw_id IS NULL OR TRIM(hw_id) = '') > 0
    """).fetchall()

    for grp in name_groups:
        rows = conn.execute("""
            SELECT * FROM devices
            WHERE LOWER(TRIM(name)) = %s AND role = %s
            ORDER BY (hw_id IS NOT NULL AND TRIM(hw_id) <> '') DESC, approved DESC, last_seen DESC NULLS LAST, created_at ASC
            FOR UPDATE
        """, (grp["norm_name"], grp["role"])).fetchall()
        if len(rows) < 2:
            continue
        primary = rows[0]
        any_approved = any(r["approved"] for r in rows)
        best_hw = next((r["hw_id"] for r in rows if r["hw_id"] and r["hw_id"].strip()), primary["hw_id"])
        best_station_hash = next((r["station_hash"] for r in rows if r["station_hash"]), primary["station_hash"])
        for dup in rows[1:]:
            if not dup["hw_id"] or not dup["hw_id"].strip() or (best_hw and dup["hw_id"].strip().upper() == best_hw.strip().upper()):
                if dup["station_hash"]:
                    conn.execute("UPDATE devices SET station_hash=NULL WHERE id=%s", (dup["id"],))
                _merge_device_pair(conn, primary["id"], dup["id"])
                merged_count += 1
        conn.execute("""
            UPDATE devices
            SET approved = %s,
                hw_id = COALESCE(%s, hw_id),
                station_hash = COALESCE(station_hash, %s)
            WHERE id = %s
        """, (any_approved, best_hw, best_station_hash, primary["id"]))

    # 3. If a physical device (hw_id) is approved in at least one row and not blocked, ensure all rows with that hw_id are approved
    conn.execute("""
        UPDATE devices d
        SET approved = TRUE
        WHERE d.hw_id IS NOT NULL AND TRIM(d.hw_id) <> ''
          AND NOT d.approved AND NOT d.blocked
          AND EXISTS (
              SELECT 1 FROM devices d2
              WHERE UPPER(TRIM(d2.hw_id)) = UPPER(TRIM(d.hw_id))
                AND d2.approved = TRUE AND d2.blocked = FALSE
          )
    """)

    return merged_count


def web_device_login(conn, user, app):
    """
    IMEI / HWID / IP persistent device authorization for web & desktop browsers and APKs.
    Supports both Android APK terminals (KPP1, KPP2) and PC / Desktop stations (Tarozi, E-navbat parkovka).
    """
    from itsdangerous import URLSafeTimedSerializer, BadData

    client_ip = get_client_ip()

    hw_id = normalize_hw_id(
        request.form.get("device_hw_id")
        or request.cookies.get("xsm_hw_id")
        or request.headers.get("X-Device-HWID")
        or ""
    )
    if not hw_id:
        ua = request.headers.get("User-Agent", "")
        match = re.search(r"HWID/([a-zA-Z0-9_\-\.:]+)", ua)
        if match:
            hw_id = normalize_hw_id(match.group(1))

    device_model = (
        request.form.get("device_model")
        or unquote(request.cookies.get("xsm_device_model", ""))
        or request.headers.get("X-Device-Model")
        or ""
    ).strip()[:60]
    if not device_model:
        ua = request.headers.get("User-Agent", "")
        match = re.search(r"Model/([^;)]+)", ua)
        if match:
            device_model = match.group(1).strip()[:60]
        elif "Windows" in ua:
            device_model = "Windows Kompyuter"
        elif "Macintosh" in ua:
            device_model = "Mac Kompyuter"
        elif "Linux" in ua and "Android" not in ua:
            device_model = "Linux Kompyuter"

    is_pc = user["role"] in {"operator", "parkovka_tashqi"} or "Kompyuter" in device_model
    if not hw_id and client_ip:
        hw_id = f"IP-{client_ip}"

    signer = URLSafeTimedSerializer(app.secret_key, salt="kpp-device")
    stored = request.cookies.get("kpp_device_" + user["role"]) or request.cookies.get(
        "kpp_device", ""
    )

    default_prefix = "Kompyuter" if is_pc else "Planshet"
    device_name = f"{default_prefix} · {user['username']}"
    if device_model:
        device_name = f"{device_model} · {user['username']}"

    deduplicate_devices(conn)

    known = None
    if hw_id:
        known = conn.execute(
            """SELECT id, approved, blocked, name, hw_id FROM devices
               WHERE UPPER(TRIM(hw_id)) = UPPER(%s) AND role = %s
               ORDER BY approved DESC, last_seen DESC NULLS LAST LIMIT 1""",
            (hw_id, user["role"]),
        ).fetchone()

    if not known and stored.startswith("kpp_"):
        known = conn.execute(
            "SELECT id, approved, blocked, name, hw_id FROM devices WHERE tablet_hash=%s AND role=%s",
            (digest(stored), user["role"]),
        ).fetchone()

    if not known and client_ip:
        # Match by IP or pre-enrolled IP in hw_id for this role
        known = conn.execute(
            """SELECT id, approved, blocked, name, hw_id FROM devices
               WHERE role = %s AND (
                   ip = %s
                   OR UPPER(TRIM(hw_id)) = UPPER(%s)
                   OR UPPER(TRIM(hw_id)) = UPPER(%s)
               )
               ORDER BY approved DESC, last_seen DESC NULLS LAST LIMIT 1""",
            (user["role"], client_ip, client_ip, f"IP-{client_ip}"),
        ).fetchone()

    if not known and hw_id:
        known = conn.execute(
            """SELECT id, approved, blocked, name, hw_id FROM devices
               WHERE (hw_id IS NULL OR TRIM(hw_id) = '')
                 AND role = %s AND (last_user = %s OR LOWER(name) = LOWER(%s))
               ORDER BY approved DESC, last_seen DESC NULLS LAST LIMIT 1""",
            (user["role"], user["username"], device_name),
        ).fetchone()

    hw_already_approved = False
    if hw_id or client_ip:
        approved_peer = conn.execute(
            """SELECT approved, blocked, name FROM devices
               WHERE approved = TRUE AND blocked = FALSE
                 AND (
                     (%s <> '' AND UPPER(TRIM(hw_id)) = UPPER(%s))
                     OR (%s <> '' AND role = %s AND (ip = %s OR UPPER(TRIM(hw_id)) = UPPER(%s) OR UPPER(TRIM(hw_id)) = UPPER(%s)))
                 )
               LIMIT 1""",
            (hw_id, hw_id, client_ip, user["role"], client_ip, client_ip, f"IP-{client_ip}"),
        ).fetchone()
        if approved_peer:
            hw_already_approved = True

    if known:
        did = known["id"]
    else:
        try:
            identity = signer.loads(stored, max_age=365 * 86400)
            did = uuid.UUID(identity["id"])
            if identity["role"] != user["role"]:
                raise ValueError()
        except (BadData, ValueError, KeyError, TypeError):
            if hw_id:
                did = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:{user['role']}:{hw_id.upper()}")
            else:
                did = uuid.uuid4()

    conn.execute(
        """INSERT INTO devices(id, name, role, ip, hw_id, approved)
           VALUES(%s, %s, %s, %s, %s, %s)
           ON CONFLICT(id) DO UPDATE SET
               ip = EXCLUDED.ip,
               hw_id = COALESCE(NULLIF(TRIM(devices.hw_id), ''), EXCLUDED.hw_id),
               approved = devices.approved OR EXCLUDED.approved,
               name = CASE WHEN (devices.name LIKE 'Planshet%%' OR devices.name LIKE 'Kompyuter%%' OR devices.name LIKE 'Qurilma%%') AND %s != '' THEN %s ELSE devices.name END""",
        (did, device_name, user["role"], client_ip, hw_id or None, hw_already_approved, device_model, device_name),
    )

    deduplicate_devices(conn)
    if hw_id:
        current = conn.execute(
            "SELECT id FROM devices WHERE UPPER(TRIM(hw_id))=UPPER(%s) AND role=%s LIMIT 1",
            (hw_id, user["role"]),
        ).fetchone()
        if current:
            did = current["id"]

    record_timeouts(conn)
    row = conn.execute(
        "SELECT * FROM devices WHERE id=%s FOR UPDATE", (did,)
    ).fetchone()
    cookie = stored if (known and stored.startswith("kpp_")) else "kpp_" + secrets.token_urlsafe(48)
    conn.execute(
        "UPDATE devices SET tablet_hash=%s, hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s) WHERE id=%s",
        (digest(cookie), hw_id or None, did),
    )
    error = (
        "Qurilma bloklangan. Techadminga murojaat qiling."
        if row["blocked"]
        else (
            f"Qurilma ro‘yxatga olindi (ID/IP: {hw_id or client_ip or str(did)[:8]}). Techadmin Qurilmalar bo‘limida ruxsat bersin, so‘ng qayta kiring."
            if not row["approved"]
            else None
        )
    )
    if not error:
        conn.execute(
            "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s,hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s) WHERE id=%s",
            (user["username"], client_ip, hw_id or None, did),
        )
        conn.execute(
            "INSERT INTO device_events(device_id,event) VALUES(%s,'login')", (did,)
        )
    return str(did), cookie, error


def set_web_device_cookie(response, cookie, app, role):
    response.set_cookie(
        "kpp_device_" + role,
        cookie,
        max_age=365 * 86400,
        httponly=True,
        secure=app.config["SESSION_COOKIE_SECURE"],
        samesite="Lax",
    )
    return response


def authenticate_device(pool):
    g.device = None
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Device "):
        return None
    with pool().connection() as conn:
        row = conn.execute(
            """SELECT u.id,u.username,u.role,u.is_active,d.id device_id,
            d.name device_name FROM device_sessions s JOIN devices d ON d.id=s.device_id
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>NOW()
            AND d.approved AND NOT d.blocked AND u.is_active AND d.role=u.role
            AND s.password_stamp=u.password_hash""",
            (digest(auth[7:]),),
        ).fetchone()
    if not row:
        abort(
            401, description="Qurilma sessiyasi tugagan yoki bloklangan. Qayta kiring."
        )
    if request.endpoint not in DEVICE_ENDPOINTS:
        abort(403)
    parking_endpoint = request.endpoint.startswith("device_parking_")
    common = {"device_bootstrap", "device_heartbeat", "device_logout", "sync_state"}
    if request.endpoint not in common and parking_endpoint != (
        row["role"] == "parkovka_tashqi"
    ):
        abort(403)
    g.device = row["device_id"]
    return row


def register_devices(
    app,
    pool,
    roles,
    prices,
    clean_plate,
    login_identifier,
    check_login_limit,
    register_login_failure,
):
    from freezone import quote, TZ, LABEL, checkpoint_stats
    from itsdangerous import URLSafeTimedSerializer, BadData

    signer = URLSafeTimedSerializer(app.secret_key, salt="desktop-parking-v1")

    def device_only():
        if not getattr(g, "device", None):
            abort(403)

    @app.get("/dastur/kpp", strict_slashes=False)
    def kpp_select():
        return render_template("kpp_select.html")

    @app.get("/kpp<int:number>")
    @app.get("/dastur/kpp/<int:number>", strict_slashes=False)
    @roles("kpp2", "ayritom")
    def kpp_entry(number):
        if number not in (1, 2) or g.user["role"] != {1: "ayritom", 2: "kpp2"}[number]:
            abort(403)
        return app.view_functions["zone_home"]()

    @app.post("/api/kpp/heartbeat")
    @roles("kpp2", "ayritom")
    def web_heartbeat():
        from flask import session

        did = session.get("device_id")
        if not did:
            return jsonify(message="Qayta login qiling"), 401
        client_ip = get_client_ip()
        with pool().connection() as conn:
            record_timeouts(conn)
            row = conn.execute(
                "SELECT * FROM devices WHERE id=%s FOR UPDATE", (did,)
            ).fetchone()
            if not row or row["blocked"]:
                session.clear()
                abort(403)
            if (
                row["last_seen"]
                and (datetime.now(timezone.utc) - row["last_seen"]).total_seconds()
                > 100
            ):
                conn.execute(
                    "INSERT INTO device_events(device_id,event,previous_seen) VALUES(%s,'reconnected',%s)",
                    (did, row["last_seen"]),
                )
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),ip=%s WHERE id=%s",
                (client_ip, did),
            )
            stats = checkpoint_stats(conn, g.user["role"])
            current_prices = prices(conn)
        return jsonify(success=True, stats=stats, prices=current_prices)

    @app.post("/api/device/login")
    def device_login():
        data = request.get_json(silent=True) or {}
        raw_did = str(data.get("device_id", "")).strip()
        role = str(data.get("role", "")).strip()
        if role not in {"operator", "parkovka_tashqi"}:
            abort(400)
        client_ip = get_client_ip()
        station_key = data.get("station_key")
        raw_hw = (
            data.get("hw_id")
            or data.get("machine_id")
            or data.get("computer_id")
            or request.headers.get("X-Device-HWID")
            or ""
        )
        try:
            device_id = uuid.UUID(raw_did) if raw_did else uuid.uuid5(
                uuid.NAMESPACE_DNS, f"airitom:pc:{role}:{raw_hw or station_key or client_ip}"
            )
        except ValueError:
            if raw_did:
                device_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:pc:{role}:{raw_did}")
            else:
                abort(400)

        hw_id = derive_pc_hw_id(
            role=role,
            client_ip=client_ip,
            station_key=station_key if isinstance(station_key, str) else "",
            device_id=str(device_id),
            raw_hw_id=str(raw_hw),
        )
        username = str(data.get("username", ""))[:40].strip().lower()
        password = str(data.get("password", ""))[:200]
        default_pc_name = "Tarozi Kompyuteri" if role == "operator" else "E-navbat Parkovka Kompyuteri"
        raw_name = str(data.get("name") or "").strip()[:100]
        device_name = raw_name if raw_name and raw_name != "Kompyuter" else f"{default_pc_name} · {username or client_ip}"

        identifier = login_identifier(username)
        with pool().connection() as conn:
            if check_login_limit(conn, identifier):
                return jsonify(
                    message="Ko‘p urinish. 15 daqiqadan keyin qayta kiring."
                ), 429
            user = conn.execute(
                "SELECT * FROM users WHERE username=%s AND is_active", (username,)
            ).fetchone()
            if (
                not user
                or user["role"] != role
                or not check_password_hash(user["password_hash"], password)
            ):
                register_login_failure(conn, identifier)
                return jsonify(message="Login, parol yoki dastur roli noto‘g‘ri"), 401
            conn.execute(
                "DELETE FROM login_attempts WHERE identifier=%s", (identifier,)
            )
            deduplicate_devices(conn)

            # Match existing PC device by id, station_hash, hw_id, or IP
            station_digest = (
                digest(station_key)
                if isinstance(station_key, str) and 16 <= len(station_key) <= 200
                else None
            )
            existing_dev = conn.execute(
                "SELECT * FROM devices WHERE id=%s LIMIT 1", (device_id,)
            ).fetchone()
            if not existing_dev and station_digest:
                existing_dev = conn.execute(
                    "SELECT * FROM devices WHERE station_hash=%s AND role=%s LIMIT 1",
                    (station_digest, role),
                ).fetchone()
            if not existing_dev and hw_id:
                existing_dev = conn.execute(
                    """SELECT * FROM devices
                       WHERE role=%s AND (
                           UPPER(TRIM(hw_id))=UPPER(%s)
                           OR UPPER(TRIM(hw_id))=UPPER(%s)
                       )
                       ORDER BY approved DESC, last_seen DESC NULLS LAST LIMIT 1""",
                    (role, hw_id, str(device_id)),
                ).fetchone()
            if not existing_dev and client_ip:
                existing_dev = conn.execute(
                    """SELECT * FROM devices
                       WHERE role=%s AND (
                           UPPER(TRIM(hw_id))=UPPER(%s)
                           OR UPPER(TRIM(hw_id))=UPPER(%s)
                           OR (ip=%s AND approved=TRUE AND NOT blocked)
                       )
                       ORDER BY approved DESC, last_seen DESC NULLS LAST LIMIT 1""",
                    (role, client_ip, f"IP-{client_ip}", client_ip),
                ).fetchone()

            if existing_dev:
                device_id = existing_dev["id"]

            # Check if any peer row with same hw_id or IP is already approved
            peer_approved = False
            if hw_id or client_ip:
                peer = conn.execute(
                    """SELECT 1 FROM devices
                       WHERE approved=TRUE AND blocked=FALSE AND (
                           (%s <> '' AND UPPER(TRIM(hw_id))=UPPER(%s))
                           OR (%s <> '' AND role=%s AND (
                               UPPER(TRIM(hw_id))=UPPER(%s)
                               OR UPPER(TRIM(hw_id))=UPPER(%s)
                               OR ip=%s
                           ))
                       ) LIMIT 1""",
                    (hw_id, hw_id, client_ip, role, client_ip, f"IP-{client_ip}", client_ip),
                ).fetchone()
                if peer:
                    peer_approved = True

            conn.execute(
                """INSERT INTO devices(id,name,role,ip,hw_id,approved) VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET
                    ip = EXCLUDED.ip,
                    hw_id = COALESCE(NULLIF(TRIM(devices.hw_id), ''), EXCLUDED.hw_id),
                    approved = devices.approved OR EXCLUDED.approved,
                    name = CASE WHEN devices.name IN ('Kompyuter', 'Qurilma') THEN EXCLUDED.name ELSE devices.name END""",
                (
                    device_id,
                    device_name[:100],
                    role,
                    client_ip,
                    hw_id or None,
                    peer_approved,
                ),
            )
            deduplicate_devices(conn)
            record_timeouts(conn)
            device = conn.execute(
                "SELECT * FROM devices WHERE id=%s FOR UPDATE", (device_id,)
            ).fetchone()
            if device["blocked"] or device["role"] != role:
                return jsonify(message="Qurilma bloklangan. Techadminga murojaat qiling."), 403

            # Bind station_key BEFORE checking approval so station_webhook can also identify this PC!
            if station_key is not None:
                if (
                    not isinstance(station_key, str)
                    or not 16 <= len(station_key) <= 200
                ):
                    abort(400)
                key_hash = digest(station_key)
                if device["station_hash"] and not secrets.compare_digest(
                    device["station_hash"], key_hash
                ):
                    # If the device is approved by HWID or IP, update the station_hash to allow seamless PC re-enrollment
                    conn.execute(
                        "UPDATE devices SET station_hash=NULL WHERE station_hash=%s AND id<>%s",
                        (key_hash, device_id),
                    )
                    conn.execute(
                        "UPDATE devices SET station_hash=%s WHERE id=%s",
                        (key_hash, device_id),
                    )
                elif not device["station_hash"]:
                    conn.execute(
                        "UPDATE devices SET station_hash=NULL WHERE station_hash=%s AND id<>%s",
                        (key_hash, device_id),
                    )
                    conn.execute(
                        "UPDATE devices SET station_hash=%s WHERE id=%s",
                        (key_hash, device_id),
                    )

            if not device["approved"]:
                conn.execute(
                    "UPDATE devices SET last_user=%s, ip=%s, hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s) WHERE id=%s",
                    (username, client_ip, hw_id or None, device_id),
                )
                return jsonify(
                    message=f"Kompyuter ro‘yxatga olindi (ID: {hw_id or str(device_id)[:8]}, IP: {client_ip}). Techadmin Qurilmalar bo‘limida ruxsat bersin.",
                    device_id=str(device_id),
                    hw_id=hw_id,
                    ip=client_ip,
                ), 403

            token = secrets.token_urlsafe(48)
            conn.execute(
                "UPDATE users SET last_login_at=NOW() WHERE id=%s", (user["id"],)
            )
            conn.execute(
                "DELETE FROM device_sessions WHERE device_id=%s OR expires_at<NOW()",
                (device_id,),
            )
            conn.execute(
                """INSERT INTO device_sessions(token_hash,device_id,user_id,password_stamp,expires_at)
                VALUES(%s,%s,%s,%s,NOW()+INTERVAL '12 hours')""",
                (digest(token), device_id, user["id"], user["password_hash"]),
            )
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s,hw_id=COALESCE(NULLIF(TRIM(hw_id), ''), %s) WHERE id=%s",
                (username, client_ip, hw_id or None, device_id),
            )
            conn.execute(
                "INSERT INTO device_events(device_id,event) VALUES(%s,'login')",
                (device_id,),
            )
            current_prices = prices(conn)
        return jsonify(
            token=token,
            prices=current_prices,
            version=digest(user["password_hash"]),
            device_id=str(device_id),
            hw_id=hw_id,
            user={k: user[k] for k in ("id", "username", "role")},
        )

    @app.get("/api/device/bootstrap")
    def device_bootstrap():
        device_only()
        return jsonify(
            user={k: g.user[k] for k in ("id", "username", "role")}, prices=prices()
        )

    @app.post("/api/device/heartbeat")
    def device_heartbeat():
        device_only()
        client_ip = get_client_ip()
        with pool().connection() as conn:
            record_timeouts(conn)
            row = conn.execute(
                "SELECT last_seen FROM devices WHERE id=%s FOR UPDATE", (g.device,)
            ).fetchone()
            if (
                row
                and row["last_seen"]
                and (datetime.now(timezone.utc) - row["last_seen"]).total_seconds()
                > 100
            ):
                conn.execute(
                    "INSERT INTO device_events(device_id,event,previous_seen) VALUES(%s,'reconnected',%s)",
                    (g.device, row["last_seen"]),
                )
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s WHERE id=%s",
                (g.user["username"], client_ip, g.device),
            )
            conn.execute(
                "DELETE FROM device_events WHERE occurred<NOW()-INTERVAL '90 days'"
            )
            current_prices = prices(conn)
        return jsonify(success=True, prices=current_prices)

    @app.post("/api/device/logout")
    def device_logout():
        device_only()
        with pool().connection() as conn:
            conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (g.device,))
            conn.execute(
                "UPDATE devices SET connected=FALSE,last_seen=NOW() WHERE id=%s",
                (g.device,),
            )
            conn.execute(
                "INSERT INTO device_events(device_id,event) VALUES(%s,'logout')",
                (g.device,),
            )
        return jsonify(success=True)

    @app.get("/admin/devices")
    @roles("techadmin")
    def devices_page():
        return app.view_functions["admin_page"]()

    @app.get("/api/admin/devices")
    @roles("techadmin")
    def devices_list():
        page = max(1, request.args.get("event_page", 1, type=int))
        size = max(10, min(100, request.args.get("event_size", 20, type=int)))
        with pool().connection() as conn:
            deduplicate_devices(conn)
            record_timeouts(conn)
            with conn.pipeline():
                devices_cur = conn.execute(
                    """SELECT id,name,role,approved,blocked,last_seen,last_user,ip,hw_id,connected AS online,backlog,backlog_at,created_at
                       FROM devices ORDER BY connected DESC, approved ASC, LOWER(name), id"""
                )
                total_cur = conn.execute("SELECT COUNT(*) n FROM device_events")
            devices = devices_cur.fetchall()
            total = total_cur.fetchone()["n"]
            pages = max(1, (total + size - 1) // size)
            page = min(page, pages)
            events = conn.execute(
                """SELECT e.occurred,e.event,e.previous_seen,d.name,d.hw_id,d.role FROM device_events e
                   JOIN devices d ON d.id=e.device_id ORDER BY occurred DESC,e.id DESC LIMIT %s OFFSET %s""",
                (size, (page - 1) * size),
            ).fetchall()
        return jsonify(
            devices=devices,
            events=events,
            event_page=page,
            event_pages=pages,
            event_total=total,
            checked_at=datetime.now(timezone.utc),
        )

    @app.post("/api/admin/devices/enroll")
    @roles("techadmin")
    def device_enroll():
        """Pre-register or bind a device by IMEI / HWID / IP / PC ID so it is immediately approved, or clear device memory."""
        data = (request.get_json(silent=True) if request.is_json else request.form) or {}
        action = str(data.get("action", "enroll")).strip()

        if action == "deduplicate":
            with pool().connection() as conn:
                merged = deduplicate_devices(conn, force=True)
            return jsonify(success=True, merged=merged)

        if action == "clear_all_devices":
            with pool().connection() as conn:
                cnt = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"]
                conn.execute("DELETE FROM device_sessions")
                conn.execute("DELETE FROM device_events")
                conn.execute("DELETE FROM station_events")
                conn.execute("UPDATE receipt_jobs SET device_id=NULL WHERE device_id IS NOT NULL")
                conn.execute("UPDATE zone_visits SET station_id=NULL, station_local_id=NULL WHERE station_id IS NOT NULL")
                conn.execute("DELETE FROM devices")
            return jsonify(
                success=True,
                deleted=cnt,
                message=f"Barcha qurilmalar xotirasi ({cnt} ta qurilma, sessiyalar va ulanish tarixi) noldan tozalandi ✓",
            )

        name = str(data.get("name", "")).strip()
        hw_id = normalize_hw_id(data.get("hw_id", ""))
        ip_addr = str(data.get("ip", "")).strip()[:64]
        role = str(data.get("role", "")).strip()

        if not 1 <= len(name) <= 100:
            return jsonify(message="Qurilma nomi 1–100 belgidan iborat bo‘lsin."), 400
        if not hw_id and ip_addr:
            hw_id = f"IP-{ip_addr}"
        if not 3 <= len(hw_id) <= 80:
            return jsonify(message="IMEI, Kompyuter ID yoki IP manzil kamida 3 belgidan iborat bo‘lsin."), 400
        if role not in ALLOWED_DEVICE_ROLES:
            return jsonify(message="Nuqta / rol noto‘g‘ri tanlangan."), 400

        if not ip_addr and re.fullmatch(r"(?:IP-)?(\d{1,3}(?:\.\d{1,3}){3})", hw_id, re.I):
            ip_addr = re.sub(r"^IP-", "", hw_id, flags=re.I)

        with pool().connection() as conn:
            deduplicate_devices(conn, force=True)
            existing = conn.execute(
                """SELECT id FROM devices
                   WHERE (UPPER(TRIM(hw_id)) = UPPER(%s) OR (%s <> '' AND ip = %s))
                     AND role = %s
                   LIMIT 1 FOR UPDATE""",
                (hw_id, ip_addr, ip_addr, role),
            ).fetchone()
            if existing:
                did = existing["id"]
                conn.execute(
                    """UPDATE devices
                       SET name=%s, hw_id=%s, ip=COALESCE(NULLIF(%s, ''), ip), approved=TRUE, blocked=FALSE
                       WHERE id=%s""",
                    (name, hw_id, ip_addr, did),
                )
            else:
                try:
                    did = uuid.UUID(hw_id)
                except ValueError:
                    did = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:{role}:{hw_id.upper()}")
                conn.execute(
                    """INSERT INTO devices(id, name, role, hw_id, ip, approved, blocked)
                       VALUES(%s, %s, %s, %s, %s, TRUE, FALSE)
                       ON CONFLICT(id) DO UPDATE SET
                           name = EXCLUDED.name,
                           role = EXCLUDED.role,
                           hw_id = EXCLUDED.hw_id,
                           ip = COALESCE(EXCLUDED.ip, devices.ip),
                           approved = TRUE,
                           blocked = FALSE""",
                    (did, name, role, hw_id, ip_addr or None),
                )
            deduplicate_devices(conn, force=True)
            conn.execute(
                "INSERT INTO device_events(device_id,event) VALUES(%s,%s)",
                (did, "approve:" + g.user["username"]),
            )
        return jsonify(success=True, id=str(did))

    @app.post("/admin/devices/<uuid:device_id>")
    @app.post("/api/admin/devices/<uuid:device_id>")
    @roles("techadmin")
    def device_manage(device_id):
        from flask import redirect

        data = (
            request.get_json(silent=True) if request.is_json else request.form
        ) or {}
        action = data.get("action")
        if action not in {"approve", "block", "unblock", "rename", "delete", "reset_session", "clear_memory"}:
            abort(400)
        name = data.get("name")
        hw_id = normalize_hw_id(data.get("hw_id", "")) if "hw_id" in data else None
        ip_override = str(data.get("ip", "")).strip()[:64] if "ip" in data else None
        role = str(data.get("role", "")).strip() if "role" in data else None
        status_override = str(data.get("status", "")).strip() if "status" in data else None

        if action == "rename" and (
            not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
        ):
            return jsonify(message="Qurilma nomi 1–100 belgidan iborat bo‘lsin."), 400
        if role and role not in ALLOWED_DEVICE_ROLES:
            return jsonify(message="Nuqta / rol noto‘g‘ri tanlangan."), 400

        with pool().connection() as conn:
            device = conn.execute(
                "SELECT id,name,role,hw_id,ip,approved,blocked FROM devices WHERE id=%s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if not device:
                abort(404)
            if action == "delete":
                conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM device_events WHERE device_id=%s", (device_id,))
                conn.execute("UPDATE receipt_jobs SET device_id=NULL WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM station_events WHERE device_id=%s", (device_id,))
                conn.execute("UPDATE zone_visits SET station_id=NULL WHERE station_id=%s", (device_id,))
                conn.execute("DELETE FROM devices WHERE id=%s", (device_id,))
                changed = False
            elif action == "clear_memory":
                conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM device_events WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM station_events WHERE device_id=%s", (device_id,))
                conn.execute("UPDATE zone_visits SET station_id=NULL, station_local_id=NULL WHERE station_id=%s", (device_id,))
                conn.execute(
                    "UPDATE devices SET connected=FALSE, station_hash=NULL, tablet_hash=NULL, backlog=NULL, backlog_at=NULL WHERE id=%s",
                    (device_id,),
                )
                changed = True
            elif action == "reset_session":
                conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (device_id,))
                conn.execute("UPDATE devices SET connected=FALSE, station_hash=NULL, tablet_hash=NULL, backlog=NULL, backlog_at=NULL WHERE id=%s", (device_id,))
                changed = True
            elif action == "rename":
                new_name = name.strip()
                new_hw = hw_id if hw_id is not None else (device["hw_id"] or "")
                new_ip = ip_override if ip_override is not None else (device["ip"] or "")
                new_role = role if role else device["role"]
                new_approved = device["approved"]
                new_blocked = device["blocked"]
                if status_override == "approved":
                    new_approved, new_blocked = True, False
                elif status_override == "pending":
                    new_approved, new_blocked = False, False
                elif status_override == "blocked":
                    new_approved, new_blocked = False, True

                changed = (
                    new_name != device["name"]
                    or (new_hw or None) != (device["hw_id"] or None)
                    or (new_ip or None) != (device["ip"] or None)
                    or new_role != device["role"]
                    or new_approved != device["approved"]
                    or new_blocked != device["blocked"]
                )
                if changed:
                    conn.execute(
                        "UPDATE devices SET name=%s, hw_id=%s, ip=%s, role=%s, approved=%s, blocked=%s WHERE id=%s",
                        (new_name, new_hw or None, new_ip or None, new_role, new_approved, new_blocked, device_id),
                    )
                    if new_blocked:
                        conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (device_id,))
                    deduplicate_devices(conn, force=True)
            else:
                approve = action in {"approve", "unblock"}
                changed = device["approved"] != approve or device["blocked"] == approve
                if changed:
                    conn.execute(
                        "UPDATE devices SET approved=%s,blocked=%s,connected=CASE WHEN %s THEN FALSE ELSE connected END WHERE id=%s",
                        (approve, not approve, not approve, device_id),
                    )
                    if approve and device["hw_id"]:
                        conn.execute(
                            "UPDATE devices SET approved=TRUE, blocked=FALSE WHERE UPPER(TRIM(hw_id))=UPPER(TRIM(%s))",
                            (device["hw_id"],),
                        )
                if action == "block":
                    conn.execute(
                        "DELETE FROM device_sessions WHERE device_id=%s", (device_id,)
                    )
            if changed:
                still_exists = conn.execute("SELECT id FROM devices WHERE id=%s", (device_id,)).fetchone()
                if still_exists:
                    conn.execute(
                        "INSERT INTO device_events(device_id,event) VALUES(%s,%s)",
                        (device_id, action + ":" + g.user["username"]),
                    )
        if request.is_json:
            return jsonify(success=True)
        return redirect("/admin?tab=devices", 303)

    @app.post("/api/device/parking/quote")
    def device_parking_quote():
        device_only()
        data = request.get_json(silent=True) or {}
        try:
            plate, key = clean_plate(str(data.get("plate", "")))
            if len(key) < 2:
                raise ValueError("Raqamni to‘g‘ri kiriting")
            with pool().connection() as conn:
                q = quote(
                    conn,
                    "parkovka_tashqi",
                    data.get("action"),
                    plate,
                    key,
                    data.get("purpose"),
                    datetime.now(TZ),
                )
                current_prices = prices(conn)
        except ValueError as exc:
            return jsonify(message=str(exc)), 400
        return jsonify(
            quote=q,
            prices=current_prices,
            ticket=signer.dumps([g.user["id"], str(g.device), str(uuid.uuid4()), q]),
        )

    @app.post("/api/device/parking/commit")
    def device_parking_commit():
        device_only()
        data = request.get_json(silent=True) or {}
        try:
            uid, did, rid, expected = signer.loads(data.get("ticket", ""), max_age=900)
        except BadData:
            return jsonify(message="Hisob muddati tugadi. Qayta tekshiring."), 409
        if uid != g.user["id"] or did != str(g.device):
            abort(403)
        with pool().connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",
                (expected["key"],),
            )
            old = conn.execute(
                "SELECT id FROM zone_events WHERE request_key=%s", (rid,)
            ).fetchone()
            if old:
                return jsonify(success=True, id=old["id"], prices=prices(conn))
            now = datetime.now(TZ)
            try:
                q = quote(
                    conn,
                    "parkovka_tashqi",
                    expected["action"],
                    expected["plate"],
                    expected["key"],
                    expected["purpose"],
                    now,
                )
                if q != expected:
                    raise ValueError("Hisob o‘zgardi. Qayta tekshiring.")
                method = data.get("method") if q["amount"] else "none"
                if q["amount"] and (
                    method not in {"cash", "card", "bank"}
                    or data.get("received") is not True
                ):
                    raise ValueError("To‘lovni tasdiqlang")
            except ValueError as exc:
                return jsonify(message=str(exc)), 409
            vid = q["visit"]
            if vid:
                conn.execute(
                    """UPDATE zone_visits SET location=%s,parking_departed_at=
                    CASE WHEN %s THEN COALESCE(parking_departed_at,%s) ELSE parking_departed_at END WHERE id=%s""",
                    (q["location"], q["mark_departed"], now, vid),
                )
            else:
                vid = conn.execute(
                    "INSERT INTO zone_visits(plate,plate_key,purpose,location,started,daily_rate) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                    (q["plate"], q["key"], q["purpose"], q["location"], now, q["rate"]),
                ).fetchone()["id"]
            eid = conn.execute(
                """INSERT INTO zone_events(request_key,visit_id,occurred,point,action,location,operator_name,days,previous_paid,amount,method)
                VALUES(%s,%s,%s,'parkovka_tashqi',%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    rid,
                    vid,
                    now,
                    q["action"],
                    q["location"],
                    g.user["username"],
                    q["days"],
                    q["paid"],
                    q["amount"],
                    method,
                ),
            ).fetchone()["id"]
            current_prices = prices(conn)
        return jsonify(success=True, id=eid, prices=current_prices)

    @app.get("/api/device/parking/recent")
    def device_parking_recent():
        device_only()
        with pool().connection() as conn:
            rows = conn.execute(
                "SELECT e.id,v.plate,e.amount,e.action FROM zone_events e JOIN zone_visits v ON v.id=e.visit_id WHERE e.point='parkovka_tashqi' ORDER BY e.id DESC LIMIT 20"
            ).fetchall()
        return jsonify(rows=rows)

    @app.get("/api/device/parking/receipt/<int:eid>")
    def device_parking_receipt(eid):
        device_only()
        with pool().connection() as conn:
            row = conn.execute(
                "SELECT e.*,v.plate FROM zone_events e JOIN zone_visits v ON v.id=e.visit_id WHERE e.id=%s AND e.point='parkovka_tashqi'",
                (eid,),
            ).fetchone()
        if not row:
            abort(404)
        row["occurred"] = row["occurred"].astimezone(TZ).strftime("%d.%m.%Y %H:%M")
        row["request_key"] = str(row["request_key"])
        return jsonify(receipt=row)
