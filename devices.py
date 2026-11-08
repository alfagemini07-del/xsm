"""Central device enrollment and persistent terminal/browser authorization."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
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


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def record_timeouts(conn):
    # Use the missed-signal deadline even if the next request arrives hours later.
    conn.execute("""WITH missed AS (
        UPDATE devices SET connected=FALSE
        WHERE connected AND last_seen < NOW()-INTERVAL '100 seconds'
        RETURNING id,last_seen)
        INSERT INTO device_events(device_id,event,occurred,previous_seen)
        SELECT id,'offline',last_seen+INTERVAL '100 seconds',last_seen FROM missed""")


def web_device_login(conn, user, app):
    """A signed, persistent identity survives logout; new browsers need approval."""
    import re
    from urllib.parse import unquote
    from itsdangerous import URLSafeTimedSerializer, BadData

    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    client_ip = forwarded or request.remote_addr or ""

    hw_id = (
        request.form.get("device_hw_id")
        or request.cookies.get("xsm_hw_id")
        or request.headers.get("X-Device-HWID")
        or ""
    ).strip()
    if not hw_id:
        ua = request.headers.get("User-Agent", "")
        match = re.search(r'HWID/([a-zA-Z0-9_\-\.:]+)', ua)
        if match:
            hw_id = match.group(1).strip()

    device_model = (
        request.form.get("device_model")
        or unquote(request.cookies.get("xsm_device_model", ""))
        or request.headers.get("X-Device-Model")
        or ""
    ).strip()
    if not device_model:
        ua = request.headers.get("User-Agent", "")
        match = re.search(r'Model/([^;)]+)', ua)
        if match:
            device_model = match.group(1).strip()

    signer = URLSafeTimedSerializer(app.secret_key, salt="kpp-device")
    stored = request.cookies.get("kpp_device_" + user["role"]) or request.cookies.get(
        "kpp_device", ""
    )
    known = None
    if hw_id:
        known = conn.execute(
            "SELECT id, approved, blocked, name FROM devices WHERE hw_id=%s AND role=%s",
            (hw_id, user["role"]),
        ).fetchone()

    if not known and stored.startswith("kpp_"):
        known = conn.execute(
            "SELECT id, approved, blocked, name FROM devices WHERE tablet_hash=%s AND role=%s",
            (digest(stored), user["role"]),
        ).fetchone()

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
                did = uuid.uuid5(uuid.NAMESPACE_DNS, f"airitom:{user['role']}:{hw_id}")
            else:
                did = uuid.uuid4()

    device_name = f"Planshet · {user['username']}"
    if device_model:
        device_name = f"{device_model} · {user['username']}"

    conn.execute(
        """INSERT INTO devices(id, name, role, ip, hw_id)
           VALUES(%s, %s, %s, %s, %s)
           ON CONFLICT(id) DO UPDATE SET
               ip = EXCLUDED.ip,
               hw_id = COALESCE(devices.hw_id, EXCLUDED.hw_id),
               name = CASE WHEN devices.name LIKE 'Planshet%' AND %s != '' THEN %s ELSE devices.name END""",
        (did, device_name, user["role"], client_ip, hw_id or None, device_model, device_name),
    )
    record_timeouts(conn)
    row = conn.execute(
        "SELECT * FROM devices WHERE id=%s FOR UPDATE", (did,)
    ).fetchone()
    cookie = stored if (known and stored.startswith("kpp_")) else "kpp_" + secrets.token_urlsafe(48)
    conn.execute(
        "UPDATE devices SET tablet_hash=%s, hw_id=COALESCE(hw_id, %s) WHERE id=%s",
        (digest(cookie), hw_id or None, did),
    )
    error = (
        "Qurilma bloklangan. Techadminga murojaat qiling."
        if row["blocked"]
        else (
            "Planshet ro‘yxatga olindi. Techadmin Qurilmalar bo‘limida ruxsat bersin, so‘ng qayta kiring."
            if not row["approved"]
            else None
        )
    )
    if not error:
        conn.execute(
            "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s,hw_id=COALESCE(hw_id, %s) WHERE id=%s",
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
    common = {"device_bootstrap", "device_heartbeat", "device_logout"}
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
        forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        client_ip = forwarded or request.remote_addr or ""
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
            stats=checkpoint_stats(conn,g.user["role"])
        return jsonify(success=True,stats=stats)

    @app.post("/api/device/login")
    def device_login():
        data = request.get_json(silent=True) or {}
        try:
            device_id = uuid.UUID(str(data.get("device_id", "")))
        except ValueError:
            abort(400)
        username = str(data.get("username", ""))[:40].strip().lower()
        password = str(data.get("password", ""))[:200]
        role = data.get("role")
        if role not in {"operator", "parkovka_tashqi"}:
            abort(400)
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
            conn.execute(
                """INSERT INTO devices(id,name,role,ip) VALUES(%s,%s,%s,%s)
                ON CONFLICT(id) DO NOTHING""",
                (
                    device_id,
                    str(data.get("name", "Kompyuter"))[:100],
                    role,
                    request.remote_addr,
                ),
            )
            record_timeouts(conn)
            device = conn.execute(
                "SELECT * FROM devices WHERE id=%s FOR UPDATE", (device_id,)
            ).fetchone()
            if device["blocked"] or device["role"] != role:
                return jsonify(message="Qurilma bloklangan"), 403
            if not device["approved"]:
                return jsonify(
                    message="Qurilma ro‘yxatga olindi. Techadmin tasdig‘ini kuting."
                ), 403
            token = secrets.token_urlsafe(48)
            station_key = data.get("station_key")
            if device["station_hash"] and station_key is None:
                return jsonify(message="Tasdiqlangan dasturdan kiring."), 403
            if station_key is not None:
                if (
                    not isinstance(station_key, str)
                    or not 40 <= len(station_key) <= 100
                ):
                    abort(400)
                if device["station_hash"] and not secrets.compare_digest(
                    device["station_hash"], digest(station_key)
                ):
                    return jsonify(
                        message="Qurilma kaliti mos kelmadi. Asl Windows profilidan kiring."
                    ), 403
                conn.execute(
                    "UPDATE devices SET station_hash=%s WHERE id=%s",
                    (digest(station_key), device_id),
                )
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
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s WHERE id=%s",
                (username, request.remote_addr, device_id),
            )
            conn.execute(
                "INSERT INTO device_events(device_id,event) VALUES(%s,'login')",
                (device_id,),
            )
        return jsonify(
            token=token,
            prices=prices(),
            version=digest(user["password_hash"]),
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
        with pool().connection() as conn:
            record_timeouts(conn)
            row = conn.execute(
                "SELECT last_seen FROM devices WHERE id=%s FOR UPDATE", (g.device,)
            ).fetchone()
            if (
                row["last_seen"]
                and (datetime.now(timezone.utc) - row["last_seen"]).total_seconds()
                > 100
            ):
                conn.execute(
                    "INSERT INTO device_events(device_id,event,previous_seen) VALUES(%s,'reconnected',%s)",
                    (g.device, row["last_seen"]),
                )
            conn.execute(
                "UPDATE devices SET connected=TRUE,last_seen=NOW(),last_user=%s,ip=%s WHERE id=%s",
                (g.user["username"], request.remote_addr, g.device),
            )
            conn.execute(
                "DELETE FROM device_events WHERE occurred<NOW()-INTERVAL '90 days'"
            )
        return jsonify(success=True)

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
    @roles("admin", "techadmin")
    def devices_page():
        return app.view_functions["admin_page"]()

    @app.get("/api/admin/devices")
    @roles("admin", "techadmin")
    def devices_list():
        page=max(1,request.args.get("event_page",1,type=int))
        size=max(10,min(100,request.args.get("event_size",20,type=int)))
        with pool().connection() as conn:
            record_timeouts(conn)
            devices = conn.execute(
                """SELECT id,name,role,approved,blocked,last_seen,last_user,ip,hw_id,connected AS online,backlog,backlog_at
                   FROM devices ORDER BY LOWER(name), id"""
            ).fetchall()
            total=conn.execute("SELECT COUNT(*) n FROM device_events").fetchone()["n"]
            pages=max(1,(total+size-1)//size);page=min(page,pages)
            events = conn.execute(
                """SELECT e.occurred,e.event,e.previous_seen,d.name FROM device_events e
                   JOIN devices d ON d.id=e.device_id ORDER BY occurred DESC,e.id DESC LIMIT %s OFFSET %s""",(size,(page-1)*size)
            ).fetchall()
        return jsonify(
            devices=devices, events=events, event_page=page,event_pages=pages,event_total=total, checked_at=datetime.now(timezone.utc)
        )

    @app.post("/admin/devices/<uuid:device_id>")
    @app.post("/api/admin/devices/<uuid:device_id>")
    @roles("techadmin")
    def device_manage(device_id):
        from flask import redirect

        data = (
            request.get_json(silent=True) if request.is_json else request.form
        ) or {}
        action = data.get("action")
        if action not in {"approve", "block", "rename", "delete"}:
            abort(400)
        name = data.get("name")
        if action == "rename" and (
            not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
        ):
            return jsonify(message="Qurilma nomi 1–100 belgidan iborat bo‘lsin."), 400
        with pool().connection() as conn:
            device = conn.execute(
                "SELECT name,approved,blocked FROM devices WHERE id=%s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if not device:
                abort(404)
            if action == "delete":
                conn.execute("DELETE FROM device_sessions WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM device_events WHERE device_id=%s", (device_id,))
                conn.execute("DELETE FROM devices WHERE id=%s", (device_id,))
                changed = False
            elif action == "rename":
                changed = name.strip() != device["name"]
                if changed:
                    conn.execute(
                        "UPDATE devices SET name=%s WHERE id=%s",
                        (name.strip(), device_id),
                    )
            else:
                approve = action == "approve"
                changed = device["approved"] != approve or device["blocked"] == approve
                if changed:
                    conn.execute(
                        "UPDATE devices SET approved=%s,blocked=%s,connected=CASE WHEN %s THEN FALSE ELSE connected END WHERE id=%s",
                        (approve, not approve, not approve, device_id),
                    )
                if action == "block":
                    conn.execute(
                        "DELETE FROM device_sessions WHERE device_id=%s", (device_id,)
                    )
            if changed:
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
        except ValueError as exc:
            return jsonify(message=str(exc)), 400
        return jsonify(
            quote=q,
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
                return jsonify(success=True, id=old["id"])
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
        return jsonify(success=True, id=eid)

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
