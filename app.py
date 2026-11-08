from __future__ import annotations

import base64
import atexit
import csv
import hashlib
import hmac
import io
import math
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import psutil
import qrcode
from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from receipt_render import receipt_view, receipt_pdf


BASE_DIR = Path(__file__).resolve().parent
COMPANY_NAME = "AIRITOM LOGISTICS CENTER MCHJ"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SESSION_SECRET = os.getenv("SECRET_KEY", "").strip() or secrets.token_hex(32)
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Tashkent"))
DB_POOL_SIZE = max(1, min(int(os.getenv("DB_POOL_SIZE", "5")), 20))
LOCAL_PRINTER_AGENT_ENABLED = os.getenv(
    "LOCAL_PRINTER_AGENT_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}
SUPABASE_DB_LIMIT_BYTES = max(
    1,
    int(os.getenv("SUPABASE_DB_LIMIT_BYTES", str(500 * 1024 * 1024))),
)
from freezone import POINTS, register_zone

ALLOWED_ROLES = {"operator", "admin", "techadmin", "display", "observer", *POINTS}
ADMIN_ROLES = {"admin", "techadmin"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
TARIFFS = {
    "weighing": ("weighing_price", "weighing_enabled", "Tarozi o'lchovi"),
    "entry": ("entry_service_price", "entry_service_enabled", "Hududga kirish xizmati"),
    "reload": ("reload_service_price", "reload_service_enabled", "Qayta yuklash xizmati"),
    "zone_daily": ("zone_daily_rate", "zone_daily_enabled", "Parkovka va Free Zone kunlik haqi"),
}


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = SESSION_SECRET
app.config.update(
    MAX_CONTENT_LENGTH=256 * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=1024 * 1024,
    MAX_FORM_PARTS=50,
    SESSION_COOKIE_NAME="tarozi_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=True,
)


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_schema_ready = False
_schema_lock = threading.Lock()
_app_started_at = datetime.now(timezone.utc)
_request_metrics_lock = threading.Lock()
_request_durations_ms: deque[float] = deque(maxlen=500)
_request_count = 0
_error_count = 0
_process = psutil.Process(os.getpid())


def close_database_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        finally:
            _pool = None


atexit.register(close_database_pool)


def now_local() -> datetime:
    return datetime.now(APP_TIMEZONE)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            if not DATABASE_URL:
                raise RuntimeError("DATABASE_URL o'rnatilmagan")
            conninfo = DATABASE_URL
            if "sslmode=" not in conninfo and "localhost" not in conninfo and "127.0.0.1" not in conninfo:
                conninfo += ("&" if "?" in conninfo else "?") + "sslmode=require"
            _pool = ConnectionPool(
                conninfo=conninfo,
                min_size=1,
                max_size=DB_POOL_SIZE,
                timeout=15,
                max_idle=300,
                kwargs={
                    "connect_timeout": 8,
                    "autocommit": False,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                },
                open=True,
            )
    return _pool


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
        generated_bootstrap = None
        synced_bootstrap = None
        with get_pool().connection() as conn:
            conn.execute(schema)
            row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            username = os.getenv("INITIAL_TECHADMIN_USERNAME", "techadmin").strip().lower() or "techadmin"
            if not re.fullmatch(r"[a-z0-9_.-]{3,40}", username):
                username = "techadmin"
            configured_password = os.getenv("INITIAL_TECHADMIN_PASSWORD", "").strip()
            if row["count"] == 0:
                password = configured_password
                if len(password) < 8:
                    raise RuntimeError("Yangi baza uchun INITIAL_TECHADMIN_PASSWORD kamida 8 belgidan iborat bo‘lishi kerak")
                inserted = conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role)
                    VALUES (%s, %s, 'techadmin')
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id
                    """,
                    (username, generate_password_hash(password, method="scrypt")),
                ).fetchone()
                if not inserted:
                    generated_bootstrap = None
            elif len(configured_password) >= 8:
                # First-login recovery: an incorrectly configured initial password can be
                # corrected on the next deploy, but never overwrite a password after the
                # techadmin has logged in successfully for the first time.
                bootstrap_user = conn.execute(
                    """SELECT id, password_hash, last_login_at FROM users
                       WHERE username=%s AND role='techadmin' AND is_active""",
                    (username,),
                ).fetchone()
                if bootstrap_user and bootstrap_user["last_login_at"] is None:
                    if not check_password_hash(bootstrap_user["password_hash"], configured_password):
                        conn.execute(
                            "UPDATE users SET password_hash=%s, updated_at=NOW() WHERE id=%s",
                            (generate_password_hash(configured_password, method="scrypt"), bootstrap_user["id"]),
                        )
                    # Identifiers contain an irreversible IP+username hash. During the
                    # one-time first-login recovery it is safe to clear stale lockouts.
                    conn.execute("DELETE FROM login_attempts")
                    synced_bootstrap = username
        if generated_bootstrap:
            username, password = generated_bootstrap
            app.logger.warning(
                "AIRITOM FIRST LOGIN | username=%s", username,
            )
        elif synced_bootstrap:
            app.logger.warning(
                "AIRITOM FIRST LOGIN RECOVERY | username=%s | ENV paroli tayyor, login bloklari tozalandi.",
                synced_bootstrap,
            )
        _schema_ready = True


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_request_context():
    g.style_nonce = secrets.token_urlsafe(18)
    g.request_started = time.perf_counter()
    supplied_request_id = request.headers.get("X-Request-ID", "")
    g.request_id = supplied_request_id if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", supplied_request_id) else uuid.uuid4().hex
    g.user = None
    if request.endpoint in {"static", "health", "favicon"}:
        return None
    if request.endpoint=="index" and request.method in {"GET","HEAD"} and not session.get("user_id") and not request.headers.get("Authorization"):
        return redirect(url_for("login",next="/"))
    if request.endpoint=="login" and request.method in {"GET","HEAD"} and not session.get("user_id"):
        return None
    ensure_schema()
    from devices import authenticate_device, PUBLIC_DEVICE_ENDPOINTS
    if request.endpoint in PUBLIC_DEVICE_ENDPOINTS:
        return None
    device_user = authenticate_device(get_pool)
    if device_user:
        g.user = device_user
        return None
    if request.endpoint in {"create_weighing", "pay_weighing", "cancel_weighing"}:
        abort(403, description="Kompyuter dasturidan kiring")
    user_id = session.get("user_id")
    if user_id:
        with get_pool().connection() as conn:
            user = conn.execute(
                """
                SELECT u.id,u.username,u.role,u.is_active,u.password_hash,
                       d.approved AS device_approved,d.blocked AS device_blocked,d.role AS device_role
                FROM users u LEFT JOIN devices d ON d.id=%s
                WHERE u.id = %s
                """,
                (session.get("device_id"),user_id),
            ).fetchone()
        if user and user["is_active"] and session.get("auth_version") == hashlib.sha256(user["password_hash"].encode()).hexdigest():
            g.user = user
            if user["role"]=="display" and request.endpoint not in {"index","login","logout","zone_home","queue_display","queue_display_data"}:
                abort(403)
            if user["role"] == "observer" and request.endpoint not in {
                "index", "login", "logout", "zone_home", "control_room", "room_data",
                "visit_timeline", "queue_display", "queue_display_data"
            }:
                abort(403)
            if session.get("device_id"):
                if not user["device_approved"] or user["device_blocked"] or user["device_role"] != user["role"]:
                    session.clear()
                    abort(403)
            if user["role"] in POINTS and request.endpoint not in {
                "index", "login", "logout", "zone_home", "zone_quote", "zone_commit", "zone_receipt", "kpp_select", "kpp_entry", "web_heartbeat", "kpp_journal", "kpp_stats", "kpp_print"
            }:
                abort(403)
        else:
            session.clear()

    if request.method in MUTATING_METHODS:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="CSRF token noto'g'ri"), 400
            abort(400)
    return None


@app.after_request
def add_security_headers(response):
    global _request_count, _error_count
    started = getattr(g, "request_started", None)
    if started is not None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if elapsed_ms > 1000 and request.endpoint != "static":
            app.logger.warning("SLOW REQUEST request_id=%s endpoint=%s method=%s duration_ms=%.0f",
                               g.request_id, request.endpoint, request.method, elapsed_ms)
        with _request_metrics_lock:
            _request_count += 1
            if response.status_code >= 500:
                _error_count += 1
            _request_durations_ms.append(elapsed_ms)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Request-ID"] = getattr(g, "request_id", uuid.uuid4().hex)
    if request.endpoint != "static":
        response.headers.setdefault("Cache-Control", "no-store")
    embedded_receipt = (
        request.endpoint == "receipt_page"
        and request.args.get("embedded") == "1"
    )
    frame_ancestors = "'self'" if embedded_receipt else "'none'"
    response.headers["X-Frame-Options"] = "SAMEORIGIN" if embedded_receipt else "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        f"style-src 'self' 'nonce-{g.style_nonce}'; "
        "script-src 'self'; "
        "connect-src 'self' http://127.0.0.1:17832; "
        f"frame-ancestors {frame_ancestors}; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(success=False, message="Kirish talab qilinadi"), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                if request.path.startswith("/api/"):
                    return jsonify(success=False, message="Ruxsat yetarli emas"), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def clean_username(value: str) -> str:
    value = (value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,40}", value):
        raise ValueError("Login 3-40 belgidan iborat bo'lsin: a-z, 0-9, _, ., -")
    return value


def clean_plate(value: str, strict: bool = True) -> tuple[str, str]:
    plate = " ".join((value or "").strip().upper().split())
    if strict and (len(plate) < 2 or len(plate) > 20):
        raise ValueError("Mashina raqami 2-20 belgi bo'lishi kerak")
    if any(ord(ch) < 32 for ch in plate) or any(ch in "<>{}\"`" for ch in plate):
        raise ValueError("Mashina raqamida ruxsat etilmagan belgi bor")
    if not plate:
        raise ValueError("Mashina raqami kiritilmagan")
    if not strict:
        plate = plate[:64]
    search = re.sub(r"[^0-9A-ZА-ЯЁЎҚҒҲ]", "", plate)
    if strict and re.fullmatch(r"\d{2}[A-Z]\d{3}[A-Z]{2}", search):
        plate = f"{search[:2]} {search[2]} {search[3:6]} {search[6:]}"
    elif strict and re.fullmatch(r"\d{5}[A-Z]{3}", search):
        plate = f"{search[:2]} {search[2:5]} {search[5:]}"
    return plate, search


def get_service_prices(conn=None) -> dict:
    defaults = {
        "weighing": 30000,
        "entry": 30000,
        "reload": 30000,
    }

    def query(active_conn) -> dict[str, int]:
        keys = [key for price_key, enabled_key, _ in TARIFFS.values() for key in (price_key, enabled_key)]
        rows = active_conn.execute("SELECT key, value FROM settings WHERE key = ANY(%s)", (keys,)).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        result = {}
        for code, (price_key, enabled_key, _label) in TARIFFS.items():
            fallback = defaults.get(code, 30000)
            result[code] = max(0, int(values.get(price_key, fallback)))
            result[f"{code}_enabled"] = values.get(enabled_key, "true").lower() == "true"
        return result

    if conn is not None:
        return query(conn)
    with get_pool().connection() as active_conn:
        return query(active_conn)


def money(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def localize(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TIMEZONE)


def serialize_weighing(row: dict) -> dict:
    created = localize(row["created_at"])
    paid_at = localize(row.get("paid_at"))
    total = int(row.get("price") or 0)
    weighing_fee_value = row.get("weighing_fee")
    weighing_fee = total if weighing_fee_value is None else int(weighing_fee_value)
    entry_fee = int(row.get("entry_fee") or 0)
    reload_fee = int(row.get("reload_fee") or 0)
    public_token = row.get("public_token")
    return {
        "id": row["id"],
        "receipt_no": row["receipt_no"],
        "plate_number": row["plate_number"],
        "price": total,
        "price_fmt": money(total),
        "total": total,
        "total_fmt": money(total),
        "weight_kg": int(row.get("weight_kg") or 0),
        "weight_fmt": money(int(row.get("weight_kg") or 0)),
        "weighing_fee": weighing_fee,
        "weighing_fee_fmt": money(weighing_fee),
        "entry_service": bool(row.get("entry_service")),
        "entry_fee": entry_fee,
        "entry_fee_fmt": money(entry_fee),
        "reload_service": bool(row.get("reload_service")),
        "reload_fee": reload_fee,
        "reload_fee_fmt": money(reload_fee),
        "status": row["status"],
        "created_at": created.strftime("%d.%m.%Y %H:%M:%S"),
        "created_iso": created.isoformat(),
        "date": created.strftime("%d.%m.%Y"),
        "time": created.strftime("%H:%M:%S"),
        "paid_at": paid_at.strftime("%d.%m.%Y %H:%M:%S") if paid_at else None,
        "operator": row.get("operator") or "—",
        "source": row.get("source") or "web",
        "payment_method": row.get("payment_method") or "cash",
        "print_state": row.get("print_state") or "not_requested",
        "public_url": url_for("public_receipt", token=public_token, _external=True) if public_token else None,
    }


def can_access_weighing(row: dict) -> bool:
    if g.user["role"] in ADMIN_ROLES:
        return True
    if getattr(g, "device", None) and row.get("created_by") != g.user["id"]:
        return False
    business_date = row.get("business_date")
    return business_date in {now_local().date(), now_local().date() - timedelta(days=1)}


def make_qr_base64(text: str, *, border: int = 2) -> str:
    qr = qrcode.QRCode(version=1, box_size=5, border=border, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def receipt_payload(row: dict) -> dict:
    data = serialize_weighing(row)
    payment_label = {
        "cash": "Naqd pul",
        "card": "Uzcard / Humo",
        "bank": "Hisob raqam",
    }.get(data["payment_method"], "Naqd pul")
    qr_lines = [
        "TAROZI CHEKI",
        COMPANY_NAME,
        f"Chek: {data['receipt_no']}",
        f"Avtomobil: {data['plate_number']}",
        f"Vazni: {data['weight_fmt']} kg",
        f"Vaqti: {data['created_at']}",
        f"Vazn o'lchash: {data['weighing_fee_fmt']} so'm",
    ]
    if data["entry_service"]:
        qr_lines.append(f"Hududga kirish: {data['entry_fee_fmt']} so'm")
    if data["reload_service"]:
        qr_lines.append(f"Qayta yuklash: {data['reload_fee_fmt']} so'm")
    qr_lines.extend(
        (
            f"Jami: {data['total_fmt']} so'm",
            f"To'lov turi: {payment_label}",
            "Holat: To'langan",
        )
    )
    qr_text = "\n".join(qr_lines)
    data.update(company=COMPANY_NAME, qr_text=qr_text)
    return data


def ensure_public_token(conn, row: dict) -> dict:
    if row.get("public_token"):
        return row
    token = secrets.token_urlsafe(24)
    conn.execute(
        "UPDATE weighings SET public_token = %s, updated_at = NOW() WHERE id = %s",
        (token, row["id"]),
    )
    row["public_token"] = token
    return row


def get_weighing(conn, weighing_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT w.*, u.username AS operator
        FROM weighings w
        LEFT JOIN users u ON u.id = w.created_by
        WHERE w.id = %s
        """,
        (weighing_id,),
    ).fetchone()


def parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} noto'g'ri")


def login_identifier(username: str) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    ip = forwarded or request.remote_addr or "unknown"
    return hashlib.sha256(f"{ip}|{username}".encode()).hexdigest()


def check_login_limit(conn, identifier: str) -> int:
    row = conn.execute(
        "SELECT * FROM login_attempts WHERE identifier = %s",
        (identifier,),
    ).fetchone()
    if not row:
        return 0
    now = datetime.now(timezone.utc)
    blocked = row["blocked_until"]
    if blocked and blocked > now:
        return max(1, math.ceil((blocked - now).total_seconds() / 60))
    if row["first_failed_at"] < now - timedelta(minutes=15):
        conn.execute("DELETE FROM login_attempts WHERE identifier = %s", (identifier,))
    return 0


def register_login_failure(conn, identifier: str) -> None:
    now = datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT * FROM login_attempts WHERE identifier = %s FOR UPDATE",
        (identifier,),
    ).fetchone()
    if not row or row["first_failed_at"] < now - timedelta(minutes=15):
        conn.execute(
            """
            INSERT INTO login_attempts (identifier, failure_count, first_failed_at, blocked_until)
            VALUES (%s, 1, %s, NULL)
            ON CONFLICT (identifier) DO UPDATE SET
                failure_count = 1,
                first_failed_at = EXCLUDED.first_failed_at,
                blocked_until = NULL
            """,
            (identifier, now),
        )
        return
    failures = row["failure_count"] + 1
    blocked_until = now + timedelta(minutes=15) if failures >= 5 else None
    conn.execute(
        """
        UPDATE login_attempts
        SET failure_count = %s, blocked_until = %s
        WHERE identifier = %s
        """,
        (failures, blocked_until, identifier),
    )


_health_lock = threading.Lock()
_health_success = 0.0


@app.get("/health")
def health():
    global _health_success
    # Reuse a recent successful check so platform probes do not open extra DB transactions.
    with _health_lock:
        if time.monotonic() - _health_success < 20:
            return jsonify(status="ok")
        try:
            ensure_schema()
            with get_pool().connection() as conn:
                conn.execute("SELECT 1").fetchone()
            _health_success = time.monotonic()
            return jsonify(status="ok")
        except Exception as error:
            app.logger.error("Health check failed: %s",error)
            return jsonify(status="error"),503


@app.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="favicon.ico"), code=308)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index" if g.user["role"] == "operator" else "zone_home"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        identifier = login_identifier(username)
        with get_pool().connection() as conn:
            blocked_minutes = check_login_limit(conn, identifier)
            if blocked_minutes:
                error = f"Ko'p xato urinish. {blocked_minutes} daqiqadan keyin qayta urinib ko'ring."
            else:
                user = conn.execute(
                    "SELECT * FROM users WHERE username = %s",
                    (username,),
                ).fetchone()
                if user and user["is_active"] and check_password_hash(user["password_hash"], password):
                    station_roles = {"/kpp1": "ayritom", "/kpp2": "kpp2", "/dastur/kpp/1": "ayritom", "/dastur/kpp/2": "kpp2"}
                    requested_station = request.args.get("next", "").split("?")[0].rstrip("/")
                    required_role = station_roles.get(requested_station)
                    if required_role and required_role != user["role"]:
                        return render_template("login.html", error="Bu KPP uchun berilgan hisob bilan kiring.", username=username), 403
                    conn.execute("DELETE FROM login_attempts WHERE identifier = %s", (identifier,))
                    conn.execute(
                        "UPDATE users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s",
                        (user["id"],),
                    )
                    device_cookie = None
                    if user["role"] in {"kpp2", "ayritom"}:
                        from devices import web_device_login, set_web_device_cookie
                        from flask import make_response
                        did, device_cookie, device_error = web_device_login(conn, user, app)
                        if device_error:
                            response = make_response(render_template("login.html", error=device_error, username=username), 403)
                            return set_web_device_cookie(response, device_cookie, app, user["role"])
                    session.clear()
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["auth_version"] = hashlib.sha256(user["password_hash"].encode()).hexdigest()
                    if user["role"] in {"kpp2", "ayritom"}:
                        session["device_id"] = did
                    csrf_token()
                    # Land on the workspace allowed for this role, even when login
                    # was opened from another employee's admin link.
                    next_url = url_for("index" if user["role"] == "operator" else "zone_home")
                    if user["role"] in {"operator", "parkovka_tashqi"}:
                        session.clear()
                        return render_template("login.html", error="Bu hisob bilan kompyuterdagi Tarozi yoki Parkovka dasturidan kiring.", username=username), 403
                    if user["role"] in {"ayritom", "kpp2"}:
                        next_url = url_for("kpp_entry", number=1 if user["role"] == "ayritom" else 2)
                    response = redirect(next_url)
                    return set_web_device_cookie(response, device_cookie, app, user["role"]) if device_cookie else response
                register_login_failure(conn, identifier)
                error = "Login yoki parol noto'g'ri"
    return render_template("login.html", error=error, username=request.form.get("username", ""))


@app.post("/logout")
@login_required
def logout():
    if session.get("device_id"):
        with get_pool().connection() as conn:
            conn.execute("UPDATE devices SET connected=FALSE,last_seen=NOW() WHERE id=%s", (session["device_id"],))
            conn.execute("INSERT INTO device_events(device_id,event) VALUES(%s,'logout')", (session["device_id"],))
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    if g.user["role"] == "operator":
        abort(403, description="Kompyuter dasturidan foydalaning")
    return redirect(url_for("zone_home"))


@app.get("/admin")
@roles_required("admin", "techadmin")
def admin_page():
    allowed_tabs = {"monitor", "users", "reports", "analytics", "search", "history", "transfer", "tech", "devices"}
    requested_tab = "devices" if request.endpoint == "devices_page" else request.args.get("tab", "")
    permitted_tabs = allowed_tabs if g.user["role"] == "techadmin" else {"reports", "analytics", "search", "history", "transfer", "devices"}
    default_tab = requested_tab if requested_tab in permitted_tabs else (
        "monitor" if g.user["role"] == "techadmin" else "dashboard"
    )
    return render_template(
        "admin.html",
        user=g.user,
        company=COMPANY_NAME,
        default_tab=default_tab,
        local_printer_agent=LOCAL_PRINTER_AGENT_ENABLED,
    )


@app.get("/receipt/<int:weighing_id>")
@login_required
def receipt_page(weighing_id: int):
    with get_pool().connection() as conn:
        row = get_weighing(conn, weighing_id)
        if not row or not can_access_weighing(row):
            abort(404)
        if row["status"] != "paid":
            abort(409)
        row = ensure_public_token(conn, row)
        payload = receipt_payload(row)
    payload = receipt_view(payload)
    return render_template(
        "receipt.html",
        receipt=payload,
        auto_print=request.args.get("autoprint") == "1",
        embedded=request.args.get("embedded") == "1",
        public_view=False,
    )


@app.get("/r/<token>")
def public_receipt(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", token):
        abort(404)
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT w.*, u.username AS operator
            FROM weighings w
            LEFT JOIN users u ON u.id = w.created_by
            WHERE w.public_token = %s AND w.status = 'paid'
            """,
            (token,),
        ).fetchone()
    if not row:
        abort(404)
    payload = receipt_payload(row)
    payload = receipt_view(payload)
    return render_template(
        "receipt.html", receipt=payload, auto_print=False, embedded=False, public_view=True
    )


@app.get("/api/stats/today")
@login_required
def api_today_stats():
    today = now_local().date()
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(price), 0) AS total
            FROM weighings
            WHERE business_date = %s AND status = 'paid'
            """,
            (today,),
        ).fetchone()
        service_prices = get_service_prices(conn)
        personal = conn.execute(
            """SELECT payment_method, COALESCE(SUM(price),0) AS amount FROM weighings
            WHERE business_date=%s AND status='paid' AND created_by=%s GROUP BY payment_method""",
            (today, g.user["id"]),
        ).fetchall()
        last = conn.execute(
            """SELECT id, receipt_no FROM weighings WHERE created_by=%s AND status='paid'
            AND business_date IN (%s,%s) ORDER BY paid_at DESC NULLS LAST, id DESC LIMIT 1""",
            (g.user["id"], today, today - timedelta(days=1)),
        ).fetchone()
    return jsonify(
        count=row["count"],
        total=row["total"],
        total_fmt=money(row["total"]),
        current_price=service_prices["weighing"],
        entry_price=service_prices["entry"],
        reload_price=service_prices["reload"],
        weighing_enabled=service_prices["weighing_enabled"],
        entry_enabled=service_prices["entry_enabled"],
        reload_enabled=service_prices["reload_enabled"],
        personal={item["payment_method"]: int(item["amount"]) for item in personal},
        last_receipt=last,
    )


@app.get("/api/weighings/recent-plates")
@login_required
def recent_plates():
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT plate_number, MAX(created_at) AS last_seen
            FROM weighings
            WHERE status <> 'cancelled'
            GROUP BY plate_number
            ORDER BY last_seen DESC
            LIMIT 3
            """
        ).fetchall()
    return jsonify(success=True, plates=[row["plate_number"] for row in rows])


@app.get("/api/operator/weighings")
@login_required
def operator_weighings():
    day_key = request.args.get("day", "today")
    if day_key not in {"today", "yesterday"}:
        return jsonify(success=False, message="Faqat bugun yoki kechagi ma'lumot mumkin"), 400
    target_date = now_local().date() - (timedelta(days=1) if day_key == "yesterday" else timedelta())
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "15"))
    except ValueError:
        return jsonify(success=False, message="Sahifa qiymati noto'g'ri"), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15
    conditions = ["w.business_date = %s"]
    params: list = [target_date]
    if getattr(g, "device", None):
        conditions.append("w.created_by = %s")
        params.append(g.user["id"])
    query = (request.args.get("q") or "").strip().upper()
    if query:
        plate_search = re.sub(r"[^0-9A-ZА-ЯЁЎҚҒҲ]", "", query)
        conditions.append("(w.plate_search LIKE %s OR UPPER(w.receipt_no) LIKE %s)")
        params.extend((f"%{plate_search}%", f"%{query}%"))
    status = request.args.get("status", "")
    if status in {"pending", "paid", "cancelled"}:
        conditions.append("w.status = %s")
        params.append(status)
    offset = (page - 1) * per_page
    with get_pool().connection() as conn:
        rows = conn.execute(
            f"""
            SELECT w.*, u.username AS operator, COUNT(*) OVER() AS full_count,
                (SELECT p.state FROM print_attempts p WHERE p.weighing_id=w.id ORDER BY p.created_at DESC LIMIT 1) AS print_state
            FROM weighings w
            LEFT JOIN users u ON u.id = w.created_by
            WHERE {' AND '.join(conditions)}
            ORDER BY w.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (*params, per_page, offset),
        ).fetchall()
    total = rows[0]["full_count"] if rows else 0
    return jsonify(
        success=True,
        results=[serialize_weighing(row) for row in rows],
        date=target_date.isoformat(),
        date_label=target_date.strftime("%d.%m.%Y"),
        page=page,
        per_page=per_page,
        total=total,
        total_pages=max(1, math.ceil(total / per_page)),
    )


@app.post("/api/weighings")
@login_required
def create_weighing():
    data = request.get_json(silent=True) or {}
    request_id = str(data.get("request_id") or "")
    if request_id and not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
        return jsonify(success=False, message="So'rov identifikatori noto'g'ri"), 400
    try:
        plate, plate_search = clean_plate(data.get("plate_number", ""))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400

    created = now_local()
    business_date = created.date()
    with get_pool().connection() as conn:
        if request_id:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (request_id,))
            existing = conn.execute("SELECT * FROM weighings WHERE client_request_id=%s", (request_id,)).fetchone()
            if existing:
                if existing["created_by"] != g.user["id"] or existing["plate_search"] != plate_search:
                    return jsonify(message="Bu amal avval boshqa ma’lumot bilan yuborilgan. Dastlabki raqamni tekshiring."), 409
                return jsonify(success=True, weighing=serialize_weighing(existing), service_prices=get_service_prices(conn))
        service_prices = get_service_prices(conn)
        price = service_prices["weighing"] if service_prices["weighing_enabled"] else 0
        public_token = secrets.token_urlsafe(24)
        seq_row = conn.execute(
            """
            INSERT INTO daily_sequences (business_date, next_value)
            VALUES (%s, 1)
            ON CONFLICT (business_date) DO UPDATE
            SET next_value = daily_sequences.next_value + 1
            RETURNING next_value
            """,
            (business_date,),
        ).fetchone()
        receipt_no = f"{business_date.strftime('%d.%m.%Y')}/{seq_row['next_value']:05d}"
        row = conn.execute(
            """
            INSERT INTO weighings (
                receipt_no, plate_number, plate_search, price, status,
                weighing_fee, created_at, business_date, created_by, source,
                public_token, client_request_id
            ) VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, 'desktop', %s, %s)
            RETURNING *
            """,
            (
                receipt_no, plate, plate_search, price, price, created,
                business_date, g.user["id"], public_token, request_id or None,
            ),
        ).fetchone()
        row["operator"] = g.user["username"]
    return jsonify(
        success=True,
        weighing=serialize_weighing(row),
        service_prices=service_prices,
    )


@app.post("/api/weighings/<int:weighing_id>/cancel")
@login_required
def cancel_weighing(weighing_id: int):
    with get_pool().connection() as conn:
        row = conn.execute("SELECT * FROM weighings WHERE id=%s FOR UPDATE", (weighing_id,)).fetchone()
        if not row or not can_access_weighing(row):
            return jsonify(success=False, message="Yozuv topilmadi"), 404
        if row["status"] == "paid":
            return jsonify(success=False, message="To'langan yozuvni bekor qilib bo'lmaydi"), 409
        if row["status"] != "cancelled":
            conn.execute(
                """
                UPDATE weighings
                SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (weighing_id,),
            )
    return jsonify(success=True)


@app.post("/api/weighings/<int:weighing_id>/pay")
@login_required
def pay_weighing(weighing_id: int):
    data = request.get_json(silent=True) or {}
    payment_method = data.get("payment_method", "cash")
    if payment_method not in {"cash", "card", "bank"}:
        return jsonify(success=False, message="To'lov usuli noto'g'ri"), 400
    try:
        weight_kg = int(data.get("weight_kg"))
    except (TypeError, ValueError):
        return jsonify(success=False, message="Vaznni kilogrammda kiriting"), 400
    if weight_kg < 1 or weight_kg > 1_000_000:
        return jsonify(success=False, message="Vazn 1–1 000 000 kg oralig'ida bo'lishi kerak"), 400
    entry_service = data.get("entry_service") is True
    reload_service = data.get("reload_service") is True
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT w.*, u.username AS operator
            FROM weighings w
            LEFT JOIN users u ON u.id = w.created_by
            WHERE w.id = %s
            FOR UPDATE OF w
            """,
            (weighing_id,),
        ).fetchone()
        if not row or not can_access_weighing(row):
            return jsonify(success=False, message="Yozuv topilmadi"), 404
        if row["status"] == "cancelled":
            return jsonify(success=False, message="Bekor qilingan yozuvni to'lab bo'lmaydi"), 409
        if row["status"] == "pending":
            service_prices = get_service_prices(conn)
            if entry_service and not service_prices["entry_enabled"]:
                return jsonify(success=False, message="Hududga kirish tarifi techadmin tomonidan o'chirilgan"), 409
            if reload_service and not service_prices["reload_enabled"]:
                return jsonify(success=False, message="Qayta yuklash tarifi techadmin tomonidan o'chirilgan"), 409
            weighing_fee_value = row.get("weighing_fee")
            weighing_fee = int(
                weighing_fee_value
                if weighing_fee_value is not None
                else (row.get("price") or service_prices["weighing"])
            )
            entry_fee = service_prices["entry"] if entry_service else 0
            reload_fee = service_prices["reload"] if reload_service else 0
            total = weighing_fee + entry_fee + reload_fee
            if data.get("expected_total") != total:
                return jsonify(success=False, message="Tarif o‘zgardi. Hisobni yangilab qayta tasdiqlang."), 409
            row = conn.execute(
                """
                UPDATE weighings
                SET status = 'paid', paid_at = NOW(), payment_method = %s,
                    weight_kg = %s, weighing_fee = %s,
                    entry_service = %s, entry_fee = %s,
                    reload_service = %s, reload_fee = %s,
                    price = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    payment_method, weight_kg, weighing_fee,
                    entry_service, entry_fee, reload_service, reload_fee,
                    total, weighing_id,
                ),
            ).fetchone()
            row["operator"] = g.user["username"]
        row = ensure_public_token(conn, row)
    return jsonify(success=True, receipt=receipt_payload(row))


@app.get("/api/sync/state")
@login_required
def sync_state():
    """A lightweight shared version used by all open devices.

    Clients only refresh their visible data after this value changes, which keeps
    forms and typed text intact while avoiding repeated heavy dashboard queries.
    """
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            SELECT
                GREATEST(
                    COALESCE((SELECT MAX(updated_at) FROM weighings), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(updated_at) FROM settings), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(updated_at) FROM users), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(imported_at) FROM import_batches), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(received_at) FROM zone_events), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(started) FROM zone_visits), TIMESTAMPTZ '1970-01-01'),
                    COALESCE((SELECT MAX(parking_departed_at) FROM zone_visits), TIMESTAMPTZ '1970-01-01')
                ) AS changed_at
            """
        ).fetchone()
    changed = localize(row["changed_at"])
    return jsonify(
        success=True,
        version=changed.isoformat(),
        server_time=now_local().isoformat(),
    )


@app.get("/api/weighings/<int:weighing_id>/receipt")
@login_required
def receipt_data(weighing_id: int):
    with get_pool().connection() as conn:
        row = get_weighing(conn, weighing_id)
        if not row or not can_access_weighing(row):
            return jsonify(success=False, message="Chek topilmadi"), 404
        if row["status"] != "paid":
            return jsonify(success=False, message="To'lov tasdiqlanmagan"), 409
        row = ensure_public_token(conn, row)
    return jsonify(success=True, receipt=receipt_payload(row))


@app.get("/api/weighings/<int:weighing_id>")
@login_required
def weighing_detail(weighing_id):
    with get_pool().connection() as conn:
        row = get_weighing(conn, weighing_id)
    if not row or not can_access_weighing(row):
        abort(404)
    return jsonify(success=True, weighing=serialize_weighing(row))


@app.get("/api/admin/vehicles/<path:plate>/history")
@login_required
def vehicle_history(plate: str):
    if g.user["role"] not in ("admin", "techadmin"):
        abort(403)
    cleaned = clean_plate(plate)
    if not cleaned:
        return jsonify(success=False, message="Avtomobil raqami noto‘g‘ri"), 400

    events = []
    total_paid = 0
    visit_ids = set()

    with get_pool().connection() as conn:
        # Zone events & visits
        z_rows = conn.execute(
            """
            SELECT e.id as event_id, e.visit_id, e.occurred, e.point, e.action, e.location,
                   e.operator_name, e.amount, e.method, v.plate, v.purpose, v.queue_no
            FROM zone_events e
            JOIN zone_visits v ON v.id = e.visit_id
            WHERE v.plate_key = %s
            ORDER BY e.occurred DESC
            LIMIT 200
            """,
            (cleaned,),
        ).fetchall()

        for r in z_rows:
            visit_ids.add(r["visit_id"])
            amt = int(r["amount"] or 0)
            total_paid += amt
            kind = "zone_payment" if amt > 0 else ("zone_entry" if r["action"] == "in" else "zone_exit")
            events.append({
                "type": kind,
                "occurred_at": r["occurred"].isoformat() if r["occurred"] else None,
                "point": r["point"],
                "action": r["action"],
                "location": r["location"],
                "amount": amt,
                "method": r["method"],
                "operator": r["operator_name"],
                "visit_id": r["visit_id"],
                "event_id": r["event_id"],
                "queue_no": r["queue_no"],
                "purpose": r["purpose"],
            })

        # Weighings
        w_rows = conn.execute(
            """
            SELECT w.id as weighing_id, w.created_at, w.plate, w.payment_method, w.price,
                   w.status, w.first_weight, w.second_weight, w.cargo_type, u.username as operator_name
            FROM weighings w
            LEFT JOIN users u ON u.id = w.created_by
            WHERE w.plate_key = %s
            ORDER BY w.created_at DESC
            LIMIT 200
            """,
            (cleaned,),
        ).fetchall()

        for w in w_rows:
            price = int(w["price"] or 0)
            if w["status"] == "paid":
                total_paid += price
            events.append({
                "type": "weighing",
                "occurred_at": w["created_at"].isoformat() if w["created_at"] else None,
                "point": "tarozi",
                "action": "weigh",
                "location": "scale",
                "amount": price,
                "method": w["payment_method"],
                "operator": w["operator_name"],
                "weighing_id": w["weighing_id"],
                "status": w["status"],
                "first_weight": w["first_weight"],
                "second_weight": w["second_weight"],
                "cargo_type": w["cargo_type"],
            })

    events.sort(key=lambda x: x["occurred_at"] or "", reverse=True)
    last_seen = events[0]["occurred_at"] if events else None

    return jsonify(
        success=True,
        plate=plate.upper(),
        events=events,
        summary={
            "total_paid": total_paid,
            "visit_count": len(visit_ids),
            "weighing_count": len(w_rows),
            "last_seen": last_seen,
        },
    )


@app.post("/api/admin/weighings/<int:weighing_id>/correct")
@login_required
def correct_weighing(weighing_id: int):
    if g.user["role"] not in ("admin", "techadmin"):
        abort(403)
    data = request.get_json(silent=True) or {}
    field = str(data.get("field") or "").strip()
    new_value = str(data.get("new_value") or "").strip()
    reason = str(data.get("reason") or "").strip()

    if not reason:
        return jsonify(success=False, message="Tuzatish sababini ko‘rsating"), 400

    allowed_fields = {"plate", "payment_method", "price", "cargo_type", "driver_name", "note"}
    if field not in allowed_fields:
        return jsonify(success=False, message=f"Maydonni tuzatishga ruxsat yo‘q: {field}"), 400

    with get_pool().connection() as conn:
        row = conn.execute("SELECT * FROM weighings WHERE id = %s FOR UPDATE", (weighing_id,)).fetchone()
        if not row:
            abort(404)

        old_value = str(row[field] if row[field] is not None else "")

        # Save correction record
        conn.execute(
            """
            INSERT INTO corrections (table_name, record_id, field, old_value, new_value, reason, corrected_by, corrected_at)
            VALUES ('weighings', %s, %s, %s, %s, %s, %s, now())
            """,
            (weighing_id, field, old_value, new_value, reason, g.user["id"]),
        )

        # Apply update
        if field == "plate":
            cleaned = clean_plate(new_value)
            conn.execute(
                "UPDATE weighings SET plate = %s, plate_key = %s, updated_at = now() WHERE id = %s",
                (new_value.upper(), cleaned, weighing_id),
            )
        elif field == "price":
            try:
                price_val = int(new_value)
                if price_val < 0:
                    raise ValueError()
            except ValueError:
                return jsonify(success=False, message="Narx musbat son bo‘lishi kerak"), 400
            conn.execute(
                "UPDATE weighings SET price = %s, updated_at = now() WHERE id = %s",
                (price_val, weighing_id),
            )
        else:
            conn.execute(
                f"UPDATE weighings SET {field} = %s, updated_at = now() WHERE id = %s",
                (new_value, weighing_id),
            )

    return jsonify(success=True, message="Tuzatish kiritildi", old_value=old_value, new_value=new_value)


@app.get("/api/admin/weighings/<int:weighing_id>/corrections")
@login_required
def get_weighing_corrections(weighing_id: int):
    if g.user["role"] not in ("admin", "techadmin"):
        abort(403)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username as user_name
            FROM corrections c
            LEFT JOIN users u ON u.id = c.corrected_by
            WHERE c.table_name = 'weighings' AND c.record_id = %s
            ORDER BY c.corrected_at DESC
            """,
            (weighing_id,),
        ).fetchall()

    return jsonify(
        success=True,
        corrections=[
            {
                "id": r["id"],
                "field": r["field"],
                "old_value": r["old_value"],
                "new_value": r["new_value"],
                "reason": r["reason"],
                "user_name": r["user_name"],
                "corrected_at": r["corrected_at"].isoformat() if r["corrected_at"] else None,
            }
            for r in rows
        ],
    )


@app.post("/api/admin/zone-events/<int:event_id>/correct")
@login_required
def correct_zone_event(event_id: int):
    if g.user["role"] not in ("admin", "techadmin"):
        abort(403)
    data = request.get_json(silent=True) or {}
    field = str(data.get("field") or "").strip()
    new_value = str(data.get("new_value") or "").strip()
    reason = str(data.get("reason") or "").strip()

    if not reason:
        return jsonify(success=False, message="Tuzatish sababini ko‘rsating"), 400

    allowed_fields = {"method", "amount"}
    if field not in allowed_fields:
        return jsonify(success=False, message=f"Maydonni tuzatishga ruxsat yo‘q: {field}"), 400

    with get_pool().connection() as conn:
        row = conn.execute("SELECT * FROM zone_events WHERE id = %s FOR UPDATE", (event_id,)).fetchone()
        if not row:
            abort(404)

        old_value = str(row[field] if row[field] is not None else "")

        conn.execute(
            """
            INSERT INTO corrections (table_name, record_id, field, old_value, new_value, reason, corrected_by, corrected_at)
            VALUES ('zone_events', %s, %s, %s, %s, %s, %s, now())
            """,
            (event_id, field, old_value, new_value, reason, g.user["id"]),
        )

        if field == "amount":
            try:
                amt_val = int(new_value)
                if amt_val < 0:
                    raise ValueError()
            except ValueError:
                return jsonify(success=False, message="Summa musbat son bo‘lishi kerak"), 400
            conn.execute("UPDATE zone_events SET amount = %s WHERE id = %s", (amt_val, event_id))
        else:
            conn.execute(f"UPDATE zone_events SET {field} = %s WHERE id = %s", (new_value, event_id))

    return jsonify(success=True, message="Tuzatish kiritildi", old_value=old_value, new_value=new_value)


@app.get("/api/admin/zone-events/<int:event_id>/corrections")
@login_required
def get_zone_event_corrections(event_id: int):
    if g.user["role"] not in ("admin", "techadmin"):
        abort(403)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, u.username as user_name
            FROM corrections c
            LEFT JOIN users u ON u.id = c.corrected_by
            WHERE c.table_name = 'zone_events' AND c.record_id = %s
            ORDER BY c.corrected_at DESC
            """,
            (event_id,),
        ).fetchall()

    return jsonify(
        success=True,
        corrections=[
            {
                "id": r["id"],
                "field": r["field"],
                "old_value": r["old_value"],
                "new_value": r["new_value"],
                "reason": r["reason"],
                "user_name": r["user_name"],
                "corrected_at": r["corrected_at"].isoformat() if r["corrected_at"] else None,
            }
            for r in rows
        ],
    )


@app.get("/receipt/<int:weighing_id>/pdf")
@login_required
def download_receipt_pdf(weighing_id):
    with get_pool().connection() as conn:
        row = get_weighing(conn, weighing_id)
    if not row or not can_access_weighing(row) or row["status"] != "paid":
        abort(404)
    return send_file(receipt_pdf(receipt_payload(row)), mimetype="application/pdf", as_attachment=True,
                     download_name=f"chek-{weighing_id}.pdf")


@app.route("/api/weighings/<int:weighing_id>/print", methods=["GET", "POST"])
@login_required
def receipt_print_status(weighing_id):
    with get_pool().connection() as conn:
        row = get_weighing(conn, weighing_id)
        if not row or not can_access_weighing(row) or row["status"] != "paid":
            abort(404)
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            try:
                attempt_id = uuid.UUID(str(data.get("attempt_id", "")))
            except ValueError:
                return jsonify(success=False, message="Chop urinish raqami noto'g'ri"), 400
            state = data.get("state")
            method = data.get("method", "browser")
            if state not in {"requested", "dialog_closed", "spooled", "confirmed", "failed"} or method not in {"browser", "agent"}:
                abort(400)
            conn.execute(
                """INSERT INTO print_attempts (id,weighing_id,user_id,state,method) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()
                WHERE print_attempts.weighing_id=EXCLUDED.weighing_id AND print_attempts.user_id=EXCLUDED.user_id
                AND (EXCLUDED.state IN ('confirmed','failed') OR print_attempts.state NOT IN ('confirmed','failed'))""",
                (attempt_id, weighing_id, g.user["id"], state, method),
            )
        latest = conn.execute("SELECT id,state,method,user_id FROM print_attempts WHERE weighing_id=%s ORDER BY created_at DESC LIMIT 1", (weighing_id,)).fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM print_attempts WHERE weighing_id=%s", (weighing_id,)).fetchone()["n"]
    return jsonify(success=True, state=latest["state"] if latest else "not_requested", attempts=count,
                   attempt_id=str(latest["id"]) if latest and latest["user_id"] == g.user["id"] else None,
                   method=latest["method"] if latest else None)


@app.get("/api/admin/dashboard")
@roles_required("admin", "techadmin")
def admin_dashboard():
    from operations import TZ, as_time, stamp
    today = now_local().date()
    first = today - timedelta(days=6)
    with get_pool().connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = conn.execute(
            """SELECT (occurred AT TIME ZONE 'Asia/Tashkent')::date AS day,
                    COUNT(*) AS count, SUM(amount) AS total, SUM(weighing_fee) AS weighing,
                    SUM(entry_fee) AS entry, SUM(reload_fee) AS reload, SUM(parking_fee) AS parking
               FROM operation_ledger
               WHERE status='paid' AND occurred >= %s AND occurred < %s GROUP BY 1""",
            (datetime.combine(first, datetime.min.time(), TZ),
             datetime.combine(today + timedelta(days=1), datetime.min.time(), TZ)),
        ).fetchall()
        meta = stamp(conn)
    by_date = {row["day"]: row for row in rows}
    weekly = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        row = by_date.get(day, {})
        amount = int(row.get("total", 0))
        weekly.append(dict(date=day.isoformat(), label=day.strftime("%d.%m"),
                           count=row.get("count", 0), total=amount, total_fmt=money(amount)))
    services = {}
    for key in ("weighing", "entry", "reload", "parking"):
        services[key] = int(by_date.get(today, {}).get(key, 0))
        services[key + "_fmt"] = money(services[key])
    week_total = sum(row["total"] for row in weekly)
    return jsonify(today_count=weekly[-1]["count"], today_total=weekly[-1]["total"],
                   today_total_fmt=weekly[-1]["total_fmt"], today_services=services,
                   week_count=sum(row["count"] for row in weekly), week_total=week_total,
                   week_total_fmt=money(week_total), weekly=weekly, as_of=as_time(meta["at"]))


@app.get("/api/admin/weighings")
@roles_required("admin", "techadmin")
def admin_weighings():
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "15"))
    except ValueError:
        return jsonify(success=False, message="Sahifa qiymati noto'g'ri"), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15

    conditions = ["1=1"]
    params: list = []
    plate = (request.args.get("plate") or "").strip().upper()
    if plate:
        search = re.sub(r"[^0-9A-ZА-ЯЁЎҚҒҲ]", "", plate)
        conditions.append("(w.plate_search LIKE %s OR UPPER(w.receipt_no) LIKE %s)")
        params.extend((f"%{search}%", f"%{plate}%"))
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        if start:
            conditions.append("w.business_date >= %s")
            params.append(parse_date(start, "Boshlanish sanasi"))
        if end:
            conditions.append("w.business_date <= %s")
            params.append(parse_date(end, "Tugash sanasi"))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    status = request.args.get("status")
    if status in {"pending", "paid", "cancelled"}:
        conditions.append("w.status = %s")
        params.append(status)

    offset = (page - 1) * per_page
    sql = f"""
        SELECT w.*, u.username AS operator, COUNT(*) OVER() AS full_count
        FROM weighings w
        LEFT JOIN users u ON u.id = w.created_by
        WHERE {' AND '.join(conditions)}
        ORDER BY w.created_at DESC
        LIMIT %s OFFSET %s
    """
    with get_pool().connection() as conn:
        rows = conn.execute(sql, (*params, per_page, offset)).fetchall()
    total = rows[0]["full_count"] if rows else 0
    return jsonify(
        success=True,
        results=[serialize_weighing(row) for row in rows],
        page=page,
        per_page=per_page,
        total=total,
        total_pages=max(1, math.ceil(total / per_page)),
    )


@app.get("/api/admin/report")
@roles_required("admin", "techadmin")
def admin_report():
    try:
        start = parse_date(request.args.get("start", ""), "Boshlanish sanasi")
        end = parse_date(request.args.get("end", ""), "Tugash sanasi")
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "15"))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15
    if end < start or (end - start).days > 3660:
        return jsonify(success=False, message="Sana oralig'i noto'g'ri"), 400
    offset = (page - 1) * per_page
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT business_date, COUNT(*) AS count, COALESCE(SUM(price), 0) AS total
            FROM weighings
            WHERE business_date BETWEEN %s AND %s AND status = 'paid'
            GROUP BY business_date
            ORDER BY business_date DESC
            LIMIT %s OFFSET %s
            """,
            (start, end, per_page, offset),
        ).fetchall()
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                COUNT(DISTINCT business_date) AS total_days,
                COALESCE(SUM(price), 0) AS total_revenue
            FROM weighings
            WHERE business_date BETWEEN %s AND %s AND status = 'paid'
            """,
            (start, end),
        ).fetchone()
        methods = conn.execute(
            """
            SELECT payment_method, COUNT(*) AS count, COALESCE(SUM(price), 0) AS total
            FROM weighings
            WHERE business_date BETWEEN %s AND %s AND status = 'paid'
            GROUP BY payment_method
            """,
            (start, end),
        ).fetchall()
        service_totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(weighing_fee), 0) AS weighing,
                COALESCE(SUM(entry_fee), 0) AS entry,
                COALESCE(SUM(reload_fee), 0) AS reload
            FROM weighings
            WHERE business_date BETWEEN %s AND %s AND status = 'paid'
            """,
            (start, end),
        ).fetchone()
    total_count = summary["total_count"]
    total_days = summary["total_days"]
    total_revenue = summary["total_revenue"]
    return jsonify(
        success=True,
        total_count=total_count,
        total_days=total_days,
        total_revenue=total_revenue,
        total_fmt=money(total_revenue),
        page=page,
        per_page=per_page,
        total_pages=max(1, math.ceil(total_days / per_page)),
        service_totals={
            "weighing": service_totals["weighing"],
            "weighing_fmt": money(service_totals["weighing"]),
            "entry": service_totals["entry"],
            "entry_fmt": money(service_totals["entry"]),
            "reload": service_totals["reload"],
            "reload_fmt": money(service_totals["reload"]),
        },
        payment_methods={
            row["payment_method"]: {
                "count": row["count"],
                "total": row["total"],
                "total_fmt": money(row["total"]),
            }
            for row in methods
        },
        daily=[
            {
                "date": row["business_date"].strftime("%d.%m.%Y"),
                "count": row["count"],
                "total": row["total"],
                "total_fmt": money(row["total"]),
            }
            for row in rows
        ],
    )


@app.get("/api/admin/export.csv")
@roles_required("admin", "techadmin")
def export_weighings_csv():
    conditions = ["1=1"]
    params: list = []
    start = request.args.get("start")
    end = request.args.get("end")
    status = request.args.get("status")
    plate = (request.args.get("plate") or "").strip().upper()
    try:
        if start:
            conditions.append("w.business_date >= %s")
            params.append(parse_date(start, "Boshlanish sanasi"))
        if end:
            conditions.append("w.business_date <= %s")
            params.append(parse_date(end, "Tugash sanasi"))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    if status in {"pending", "paid", "cancelled"}:
        conditions.append("w.status = %s")
        params.append(status)
    if plate:
        plate_search = re.sub(r"[^0-9A-ZА-ЯЁЎҚҒҲ]", "", plate)
        conditions.append("(w.plate_search LIKE %s OR UPPER(w.receipt_no) LIKE %s)")
        params.extend((f"%{plate_search}%", f"%{plate}%"))

    handle = tempfile.NamedTemporaryFile(
        prefix="tarozi-export-", suffix=".csv", delete=False,
        mode="w", newline="", encoding="utf-8-sig",
    )
    export_path = handle.name
    try:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
                "Chek", "Davlat raqami", "Vazn (kg)", "Vazn o'lchash",
                "Hududga kirish", "Qayta yuklash", "Jami", "Holat",
                "To'lov usuli", "Sana va vaqt", "Operator", "Manba",
            ]
        )
        with get_pool().connection() as conn:
            with conn.cursor(name=f"export_{uuid.uuid4().hex}") as cursor:
                cursor.execute(
                    f"""
                    SELECT w.*, u.username AS operator
                    FROM weighings w
                    LEFT JOIN users u ON u.id = w.created_by
                    WHERE {' AND '.join(conditions)}
                    ORDER BY w.created_at DESC
                    """,
                    params,
                )
                while True:
                    rows = cursor.fetchmany(2000)
                    if not rows:
                        break
                    for row in rows:
                        item = serialize_weighing(row)
                        writer.writerow(
                            [
                                item["receipt_no"], item["plate_number"], item["weight_kg"],
                                item["weighing_fee"], item["entry_fee"], item["reload_fee"],
                                item["total"], item["status"], item["payment_method"],
                                item["created_at"], item["operator"], item["source"],
                            ]
                        )
        handle.close()
    except Exception:
        handle.close()
        try:
            os.remove(export_path)
        except OSError:
            pass
        raise
    filename = f"tarozi-hisobot-{now_local().strftime('%Y-%m-%d')}.csv"
    response = send_file(export_path, as_attachment=True, download_name=filename, mimetype="text/csv; charset=utf-8")
    response.call_on_close(lambda: os.path.exists(export_path) and os.remove(export_path))
    return response


def parse_legacy_datetime(value, fallback_date: date) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        parsed = None
        for parser in (
            datetime.fromisoformat,
            lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),
            lambda v: datetime.strptime(v, "%d.%m.%Y %H:%M:%S"),
        ):
            try:
                parsed = parser(text)
                break
            except (TypeError, ValueError):
                continue
        if parsed is None:
            parsed = datetime.combine(fallback_date, datetime.min.time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=APP_TIMEZONE)
    return parsed


def legacy_number(value, default: int = 0) -> int:
    """Read SQLite INTEGER/REAL/TEXT money fields without rejecting old rows."""
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return max(0, default)


def legacy_flag(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ha", "paid", "completed"}
    return bool(value)


def legacy_date(value, fallback: date | None = None) -> date:
    text = str(value or "").strip()
    for parser in (
        date.fromisoformat,
        lambda item: datetime.strptime(item, "%d.%m.%Y").date(),
        lambda item: datetime.fromisoformat(item).date(),
    ):
        try:
            return parser(text)
        except (TypeError, ValueError):
            continue
    return fallback or now_local().date()


@app.post("/api/admin/import-sqlite")
@roles_required("admin", "techadmin")
def import_sqlite():
    uploaded = request.files.get("database")
    if not uploaded or not uploaded.filename:
        return jsonify(success=False, message="SQLite fayl tanlanmagan"), 400
    filename = secure_filename(uploaded.filename) or "import.db"
    if Path(filename).suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        return jsonify(success=False, message="Faqat .db/.sqlite fayl qabul qilinadi"), 400

    temp_path = None
    legacy = None
    try:
        handle = tempfile.NamedTemporaryFile(prefix="tarozi-import-", suffix=".db", delete=False)
        temp_path = handle.name
        handle.close()
        uploaded.save(temp_path)
        digest = hashlib.sha256()
        with open(temp_path, "rb") as source:
            header = source.read(16)
            digest.update(header)
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if header != b"SQLite format 3\x00":
            return jsonify(success=False, message="Bu SQLite database emas"), 400
        file_sha = digest.hexdigest()

        uri = f"file:{Path(temp_path).as_posix()}?mode=ro&immutable=1"
        legacy = sqlite3.connect(uri, uri=True)
        legacy.row_factory = sqlite3.Row
        table_names = {
            row[0]
            for row in legacy.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "weighings" in table_names:
            source_kind = "weighings"
            columns = {row[1] for row in legacy.execute("PRAGMA table_info(weighings)")}
            if "plate_number" not in columns:
                return jsonify(success=False, message="weighings jadvalida plate_number ustuni yo'q"), 400
            cursor = legacy.execute('SELECT rowid AS "__rowid__", * FROM "weighings" ORDER BY rowid')
        elif "vehicles" in table_names:
            source_kind = "vehicles"
            columns = {row[1] for row in legacy.execute("PRAGMA table_info(vehicles)")}
            if "plate" not in columns:
                return jsonify(success=False, message="vehicles jadvalida plate ustuni yo'q"), 400
            cursor = legacy.execute('SELECT rowid AS "__rowid__", * FROM "vehicles" ORDER BY rowid')
        else:
            return jsonify(
                success=False,
                message="Mos jadval topilmadi: weighings yoki vehicles jadvali kerak",
            ), 400
        batch_id = uuid.uuid4()
        total = inserted = 0
        daily_counter: dict[str, int] = {}

        with get_pool().connection() as conn:
            import_users = {u["username"]: u["id"] for u in conn.execute("SELECT id,username FROM users").fetchall()}
            conn.execute(
                """
                INSERT INTO import_batches (id, filename, file_sha256, imported_by)
                VALUES (%s, %s, %s, %s)
                """,
                (batch_id, filename, file_sha, g.user["id"]),
            )
            while True:
                source_rows = cursor.fetchmany(1000)
                if not source_rows:
                    break
                values = []
                for legacy_row in source_rows:
                    total += 1
                    row = dict(legacy_row)
                    source_id = row.get("id") or row.get("__rowid__") or total
                    if source_kind == "vehicles":
                        business_date = legacy_date(row.get("entry_date"), legacy_date(row.get("created_at")))
                        created_value = row.get("created_at") or " ".join(
                            part for part in (str(row.get("entry_date") or "").strip(), str(row.get("entry_time") or "").strip()) if part
                        )
                        plate_value = row.get("plate")
                        parking_fee = legacy_number(row.get("fee")) + legacy_number(row.get("queue_fee"))
                        imported_reload_fee = legacy_number(row.get("reload_fee"))
                        imported_weighing_fee = 0
                        imported_entry_fee = parking_fee
                        imported_entry_service = parking_fee > 0
                        imported_reload_service = imported_reload_fee > 0
                        imported_price = parking_fee + imported_reload_fee
                        vehicle_status = str(row.get("status") or "").strip().lower()
                        imported_status = "paid" if vehicle_status in {"completed", "paid", "done", "closed"} else (
                            "cancelled" if vehicle_status in {"cancelled", "canceled", "deleted"} else "pending"
                        )
                        imported_receipt = row.get("queue_number")
                        imported_source = "sqlite_import:vehicles"
                    else:
                        created_fallback = legacy_date(row.get("created_at"))
                        business_date = legacy_date(row.get("date"), created_fallback)
                        created_value = row.get("created_at")
                        plate_value = row.get("plate_number")
                        imported_price = legacy_number(row.get("price"))
                        imported_status = row.get("status")
                        if imported_status not in {"pending", "paid", "cancelled"}:
                            imported_status = "paid" if legacy_flag(row.get("paid"), True) else "pending"
                        imported_weighing_fee = legacy_number(row.get("weighing_fee")) if "weighing_fee" in row else imported_price
                        imported_entry_fee = legacy_number(row.get("entry_fee"))
                        imported_reload_fee = legacy_number(row.get("reload_fee"))
                        imported_entry_service = legacy_flag(row.get("entry_service"), imported_entry_fee > 0)
                        imported_reload_service = legacy_flag(row.get("reload_service"), imported_reload_fee > 0)
                        imported_receipt = row.get("legacy_receipt_no") or row.get("receipt_no")
                        imported_source = str(row.get("source") or "sqlite_import")[:80]
                    created_at = parse_legacy_datetime(created_value, business_date)
                    try:
                        plate, plate_search = clean_plate(str(plate_value or ""), strict=False)
                    except ValueError:
                        plate = f"NOMA'LUM-{source_id}"
                        plate_search = re.sub(r"\W", "", plate)
                    weight_kg = legacy_number(row.get("weight_kg"))
                    weighing_fee = imported_weighing_fee
                    entry_fee = imported_entry_fee if imported_entry_service else 0
                    reload_fee = imported_reload_fee if imported_reload_service else 0
                    price = imported_price or (weighing_fee + entry_fee + reload_fee)
                    status = imported_status
                    daily_key = business_date.isoformat()
                    daily_counter[daily_key] = daily_counter.get(daily_key, 0) + 1
                    legacy_receipt = imported_receipt
                    if not legacy_receipt:
                        legacy_receipt = f"{business_date.strftime('%d.%m.%Y')}/{daily_counter[daily_key]:05d}"
                    fingerprint = row.get("legacy_fingerprint") or hashlib.sha256(
                        (
                            f"{source_kind}|{source_id}|{plate}|{price}|{status}|"
                            f"{created_at.isoformat()}|{business_date.isoformat()}"
                        ).encode("utf-8")
                    ).hexdigest()
                    receipt_no = row.get("receipt_no")
                    if not receipt_no or not str(receipt_no).strip():
                        receipt_no = f"IMP-{business_date.strftime('%Y%m%d')}-{source_id}-{fingerprint[:8]}"
                    paid_at = parse_legacy_datetime(row.get("paid_at"), business_date) if row.get("paid_at") else (
                        created_at if status == "paid" else None
                    )
                    cancelled_at = (
                        parse_legacy_datetime(row.get("cancelled_at"), business_date)
                        if row.get("cancelled_at")
                        else None
                    )
                    payment_method = row.get("payment_method") or "cash"
                    if payment_method not in {"cash", "card", "bank"}:
                        payment_method = "cash"
                    entry_service = imported_entry_service
                    reload_service = imported_reload_service
                    public_token = str(row.get("public_token") or secrets.token_urlsafe(24))[:100]
                    values.append(
                        (
                            str(receipt_no)[:80],
                            plate,
                            plate_search,
                            price,
                            weight_kg,
                            weighing_fee,
                            entry_fee,
                            reload_fee,
                            entry_service,
                            reload_service,
                            public_token,
                            status,
                            created_at,
                            business_date,
                            paid_at,
                            cancelled_at,
                            import_users.get(row.get("created_by_username"), g.user["id"]),
                            imported_source,
                            legacy_number(row.get("legacy_id") or source_id),
                            str(legacy_receipt)[:80],
                            fingerprint,
                            batch_id,
                            payment_method,
                        )
                    )
                with conn.cursor() as pg_cursor:
                    pg_cursor.executemany(
                        """
                        INSERT INTO weighings (
                            receipt_no, plate_number, plate_search, price,
                            weight_kg, weighing_fee, entry_fee, reload_fee,
                            entry_service, reload_service, public_token, status,
                            created_at, business_date, paid_at, cancelled_at,
                            created_by, source, legacy_id, legacy_receipt_no,
                            legacy_fingerprint, import_batch_id
                            , payment_method
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        values,
                    )
                    inserted += max(0, pg_cursor.rowcount)
            if "print_attempts" in table_names:
                print_columns = {r[1] for r in legacy.execute('PRAGMA table_info("print_attempts")')}
                if {"id","receipt_no","username","state","method","created_at","updated_at"} <= print_columns:
                    print_cursor = legacy.execute('SELECT * FROM "print_attempts"')
                    while True:
                        print_rows = print_cursor.fetchmany(1000)
                        if not print_rows:
                            break
                        records = []
                        for item in print_rows:
                            item = dict(item)
                            if item["state"] not in {"requested","dialog_closed","spooled","confirmed","failed"} or item["method"] not in {"browser","agent"}:
                                continue
                            try:
                                identifier = uuid.UUID(str(item["id"]))
                                recorded = datetime.fromisoformat(item["created_at"])
                                updated = datetime.fromisoformat(item["updated_at"])
                            except (ValueError,TypeError):
                                continue
                            records.append((identifier,import_users.get(item["username"]),item["state"],item["method"],recorded,updated,item["receipt_no"]))
                        with conn.cursor() as print_target:
                            print_target.executemany(
                                """INSERT INTO print_attempts(id,weighing_id,user_id,state,method,created_at,updated_at)
                                SELECT %s,w.id,%s,%s,%s,%s,%s FROM weighings w WHERE w.receipt_no=%s AND w.status='paid'
                                ON CONFLICT(id) DO NOTHING""", records)
            conn.execute(
                """
                UPDATE import_batches
                SET total_rows = %s, inserted_rows = %s, skipped_rows = %s
                WHERE id = %s
                """,
                (total, inserted, total - inserted, batch_id),
            )
        legacy.close()
        return jsonify(
            success=True,
            message="Import yakunlandi",
            source_table=source_kind,
            total=total,
            inserted=inserted,
            skipped=total - inserted,
        )
    except sqlite3.DatabaseError:
        app.logger.exception("SQLite import error")
        return jsonify(success=False, message="SQLite fayl buzilgan yoki o'qib bo'lmadi"), 400
    except Exception:
        app.logger.exception("Import failed")
        return jsonify(success=False, message="Import vaqtida server xatosi yuz berdi"), 500
    finally:
        if legacy is not None:
            try:
                legacy.close()
            except sqlite3.Error:
                pass
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


@app.get("/api/admin/settings/tariffs")
@roles_required("admin", "techadmin")
def list_tariffs():
    try:
        page = max(1, int(request.args.get("history_page", "1")))
        per_page = int(request.args.get("history_per_page", "15"))
    except ValueError:
        return jsonify(success=False, message="Sahifa qiymati noto'g'ri"), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15
    offset = (page - 1) * per_page
    with get_pool().connection() as conn:
        values = get_service_prices(conn)
        history = conn.execute(
            """SELECT h.*, u.username, COUNT(*) OVER() AS full_count FROM tariff_history h
               LEFT JOIN users u ON u.id=h.changed_by
               ORDER BY h.changed_at DESC LIMIT %s OFFSET %s""",
            (per_page, offset),
        ).fetchall()
    history_total = history[0]["full_count"] if history else 0
    tariffs = [
        {
            "code": code,
            "label": label,
            "amount": values[code],
            "enabled": values[f"{code}_enabled"],
            "can_apply_existing": code in {"weighing", "zone_daily"},
        }
        for code, (_price_key, _enabled_key, label) in TARIFFS.items()
    ]
    return jsonify(
        success=True,
        tariffs=tariffs,
        history_page=page,
        history_per_page=per_page,
        history_total=history_total,
        history_pages=max(1, math.ceil(history_total / per_page)),
        history=[
            {
                "id": row["id"], "code": row["code"], "label": TARIFFS.get(row["code"], (None, None, row["code"]))[2],
                "amount": row["amount"], "enabled": row["enabled"],
                "applied_to_existing": row["applied_to_existing"], "affected_rows": row["affected_rows"],
                "username": row["username"] or "—",
                "changed_at": localize(row["changed_at"]).strftime("%d.%m.%Y %H:%M"),
            }
            for row in history
        ],
    )


@app.put("/api/admin/settings/tariffs/<code>")
@roles_required("techadmin")
def update_tariff(code: str):
    if code not in TARIFFS:
        return jsonify(success=False, message="Tarif topilmadi"), 404
    data = request.get_json(silent=True) or {}
    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify(success=False, message="Tarif summasi noto'g'ri"), 400
    enabled = data.get("enabled") is True
    apply_existing = data.get("apply_existing") is True and code in {"weighing", "zone_daily"}
    if amount < 0 or amount > 1_000_000_000 or (enabled and amount < 1):
        return jsonify(success=False, message="Faol tarif 1–1 000 000 000 so'm oralig'ida bo'lishi kerak"), 400
    price_key, enabled_key, _label = TARIFFS[code]
    effective_amount = amount if enabled else 0
    affected = 0
    with get_pool().connection() as conn:
        for key, value in ((price_key, str(amount)), (enabled_key, "true" if enabled else "false")):
            conn.execute(
                """INSERT INTO settings(key,value,updated_at,updated_by) VALUES(%s,%s,NOW(),%s)
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
                (key, value, g.user["id"]),
            )
        if apply_existing and code == "zone_daily":
            affected = conn.execute(
                "UPDATE zone_visits SET daily_rate=%s WHERE location<>'closed'", (effective_amount,)
            ).rowcount
        elif apply_existing and code == "weighing":
            affected = conn.execute(
                "UPDATE weighings SET weighing_fee=%s,price=%s,updated_at=NOW() WHERE status='pending'",
                (effective_amount, effective_amount),
            ).rowcount
        conn.execute(
            """INSERT INTO tariff_history(code,amount,enabled,applied_to_existing,affected_rows,changed_by)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (code, amount, enabled, apply_existing, max(0, affected), g.user["id"]),
        )
    return jsonify(success=True, code=code, amount=amount, enabled=enabled, affected_rows=max(0, affected))


@app.get("/api/admin/users")
@roles_required("techadmin")
def list_users():
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "15"))
    except ValueError:
        return jsonify(success=False, message="Sahifa qiymati noto'g'ri"), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15
    offset = (page - 1) * per_page
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT id, username, role, is_active, created_at, last_login_at,
                   COUNT(*) OVER() AS full_count
            FROM users
            ORDER BY username
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
        ).fetchall()
    total = rows[0]["full_count"] if rows else 0
    return jsonify(
        success=True,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=max(1, math.ceil(total / per_page)),
        users=[
            {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "is_active": row["is_active"],
                "created_at": localize(row["created_at"]).strftime("%d.%m.%Y %H:%M"),
                "last_login_at": (
                    localize(row["last_login_at"]).strftime("%d.%m.%Y %H:%M")
                    if row["last_login_at"]
                    else None
                ),
            }
            for row in rows
        ],
    )


@app.post("/api/admin/users")
@roles_required("techadmin")
def create_user():
    data = request.get_json(silent=True) or {}
    try:
        username = clean_username(data.get("username", ""))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400
    password = data.get("password") or ""
    role = data.get("role")
    if len(password) < 8:
        return jsonify(success=False, message="Parol kamida 8 belgi bo'lishi kerak"), 400
    if role not in ALLOWED_ROLES:
        return jsonify(success=False, message="Rol noto'g'ri"), 400
    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (username, generate_password_hash(password, method="scrypt"), role),
            ).fetchone()
        return jsonify(success=True, id=row["id"])
    except psycopg.errors.UniqueViolation:
        return jsonify(success=False, message="Bu login allaqachon mavjud"), 409


@app.post("/api/admin/users/<int:user_id>/reset-password")
@roles_required("techadmin")
def reset_user_password(user_id: int):
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if len(password) < 8:
        return jsonify(success=False, message="Parol kamida 8 belgi bo'lishi kerak"), 400
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE users
            SET password_hash = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id
            """,
            (generate_password_hash(password, method="scrypt"), user_id),
        ).fetchone()
    if not row:
        return jsonify(success=False, message="Foydalanuvchi topilmadi"), 404
    return jsonify(success=True)


@app.post("/api/admin/users/<int:user_id>/toggle")
@roles_required("techadmin")
def toggle_user(user_id: int):
    if user_id == g.user["id"]:
        return jsonify(success=False, message="O'zingizni bloklay olmaysiz"), 400
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE users
            SET is_active = NOT is_active, updated_at = NOW()
            WHERE id = %s
            RETURNING is_active
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return jsonify(success=False, message="Foydalanuvchi topilmadi"), 404
    return jsonify(success=True, is_active=row["is_active"])


@app.get("/api/admin/backup-tokens")
@roles_required("techadmin")
def list_backup_tokens():
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "15"))
    except ValueError:
        return jsonify(success=False, message="Sahifa qiymati noto'g'ri"), 400
    if per_page not in {10, 15, 20, 50, 100}:
        per_page = 15
    offset = (page - 1) * per_page
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, created_at, last_used_at, revoked_at, COUNT(*) OVER() AS full_count
            FROM backup_tokens
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
        ).fetchall()
    total = rows[0]["full_count"] if rows else 0
    return jsonify(
        success=True,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=max(1, math.ceil(total / per_page)),
        tokens=[
            {
                "id": row["id"],
                "name": row["name"],
                "created_at": localize(row["created_at"]).strftime("%d.%m.%Y %H:%M"),
                "last_used_at": (
                    localize(row["last_used_at"]).strftime("%d.%m.%Y %H:%M")
                    if row["last_used_at"]
                    else None
                ),
                "revoked": bool(row["revoked_at"]),
            }
            for row in rows
        ],
    )


@app.post("/api/admin/backup-tokens")
@roles_required("techadmin")
def create_backup_token():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Backup kompyuteri").strip()[:80]
    token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            INSERT INTO backup_tokens (name, token_hash, created_by)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (name, token_hash, g.user["id"]),
        ).fetchone()
    return jsonify(success=True, id=row["id"], token=token)


@app.post("/api/admin/backup-tokens/<int:token_id>/revoke")
@roles_required("techadmin")
def revoke_backup_token(token_id: int):
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            UPDATE backup_tokens
            SET revoked_at = COALESCE(revoked_at, NOW())
            WHERE id = %s
            RETURNING id
            """,
            (token_id,),
        ).fetchone()
    if not row:
        return jsonify(success=False, message="Token topilmadi"), 404
    return jsonify(success=True)


def backup_token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify(success=False, message="Backup token kerak"), 401
        token_hash = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM backup_tokens
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return jsonify(success=False, message="Backup token yaroqsiz"), 401
            conn.execute("UPDATE backup_tokens SET last_used_at = NOW() WHERE id = %s", (row["id"],))
        return view(*args, **kwargs)

    return wrapped


def create_sqlite_backup(include_users: bool = True) -> str:
    handle = tempfile.NamedTemporaryFile(prefix="tarozi-backup-", suffix=".db", delete=False)
    path = handle.name
    handle.close()
    backup = sqlite3.connect(path)
    try:
        backup.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE weighings (
                id INTEGER PRIMARY KEY,
                receipt_no TEXT NOT NULL,
                plate_number TEXT NOT NULL,
                price INTEGER NOT NULL,
                weight_kg INTEGER NOT NULL DEFAULT 0,
                weighing_fee INTEGER NOT NULL DEFAULT 0,
                entry_fee INTEGER NOT NULL DEFAULT 0,
                reload_fee INTEGER NOT NULL DEFAULT 0,
                entry_service INTEGER NOT NULL DEFAULT 0,
                reload_service INTEGER NOT NULL DEFAULT 0,
                public_token TEXT,
                paid INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                date TEXT NOT NULL,
                paid_at TEXT,
                cancelled_at TEXT,
                payment_method TEXT NOT NULL DEFAULT 'cash',
                created_by_username TEXT,
                source TEXT,
                legacy_id INTEGER,
                legacy_receipt_no TEXT,
                legacy_fingerprint TEXT
            );
            CREATE INDEX idx_backup_weighings_date ON weighings(date);
            CREATE INDEX idx_backup_weighings_plate ON weighings(plate_number);
            CREATE TABLE print_attempts (
                id TEXT PRIMARY KEY, receipt_no TEXT NOT NULL, username TEXT,
                state TEXT NOT NULL, method TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        backup.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("format", "TaroziKiosk backup v4"),
                ("created_at", now_local().isoformat()),
                ("timezone", str(APP_TIMEZONE)),
            ],
        )
        with get_pool().connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if include_users:
                users = conn.execute(
                    """
                    SELECT id, username, password_hash, role, is_active, created_at, last_login_at
                    FROM users ORDER BY id
                    """
                ).fetchall()
                backup.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            row["id"], row["username"], row["password_hash"], row["role"],
                            int(row["is_active"]), row["created_at"].isoformat(),
                            row["last_login_at"].isoformat() if row["last_login_at"] else None,
                        )
                        for row in users
                    ],
                )
            settings = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
            if include_users:
                # Append checkpoint journals to the restricted technical backup.
                for table, columns in (
                    ("zone_visits", "id plate plate_key purpose location started daily_rate"),
                    ("zone_events", "id request_key visit_id occurred point action location operator_name days previous_paid amount method"),
                    ("tariff_history", "id code amount enabled applied_to_existing affected_rows changed_by changed_at"),
                ):
                    names = columns.split()
                    numeric = {
                        "id", "visit_id", "daily_rate", "days", "previous_paid", "amount",
                        "enabled", "applied_to_existing", "affected_rows", "changed_by",
                    }
                    definition = ",".join(name + (" INTEGER" if name in numeric else " TEXT") for name in names)
                    backup.execute(f"CREATE TABLE {table} ({definition}, PRIMARY KEY(id))")
                    with conn.cursor(name=f"snapshot_{table}") as cursor:
                        cursor.execute(f"SELECT {','.join(names)} FROM {table} ORDER BY id")
                        while batch := cursor.fetchmany(2000):
                            backup.executemany(f"INSERT INTO {table} VALUES ({','.join('?' for _ in names)})",
                                [tuple(r[n] if n in numeric else str(r[n]) for n in names) for r in batch])
            backup.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [(row["key"], row["value"]) for row in settings],
            )
            cursor_name = f"backup_{uuid.uuid4().hex}"
            with conn.cursor(name=cursor_name) as cursor:
                cursor.execute(
                    """
                    SELECT w.*, u.username AS created_by_username
                    FROM weighings w
                    LEFT JOIN users u ON u.id = w.created_by
                    ORDER BY w.id
                    """
                )
                while True:
                    rows = cursor.fetchmany(2000)
                    if not rows:
                        break
                    backup.executemany(
                        """
                        INSERT INTO weighings VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        [
                            (
                                row["id"], row["receipt_no"], row["plate_number"], row["price"],
                                row["weight_kg"], row["weighing_fee"], row["entry_fee"],
                                row["reload_fee"], int(row["entry_service"]),
                                int(row["reload_service"]), row["public_token"],
                                int(row["status"] == "paid"), row["status"],
                                row["created_at"].isoformat(), row["business_date"].isoformat(),
                                row["paid_at"].isoformat() if row["paid_at"] else None,
                                row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
                                row["payment_method"],
                                row["created_by_username"], row["source"], row["legacy_id"],
                                row["legacy_receipt_no"], row["legacy_fingerprint"],
                            )
                            for row in rows
                        ],
                    )
            with conn.cursor(name=f"prints_{uuid.uuid4().hex}") as cursor:
                cursor.execute("""SELECT p.*,w.receipt_no,u.username FROM print_attempts p
                    JOIN weighings w ON w.id=p.weighing_id LEFT JOIN users u ON u.id=p.user_id ORDER BY p.created_at""")
                while True:
                    rows=cursor.fetchmany(2000)
                    if not rows: break
                    backup.executemany("INSERT INTO print_attempts VALUES (?,?,?,?,?,?,?)",
                        [(str(r["id"]),r["receipt_no"],r["username"],r["state"],r["method"],r["created_at"].isoformat(),r["updated_at"].isoformat()) for r in rows])
        backup.commit()
        return path
    except Exception:
        backup.close()
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        try:
            backup.close()
        except Exception:
            pass


def send_sqlite_backup(include_users: bool = True):
    path = create_sqlite_backup(include_users=include_users)
    filename = f"{now_local().strftime('%d.%m.%Y')} 00-00 holatiga backup.db"
    response = send_file(
        path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.sqlite3",
        conditional=False,
    )
    response.call_on_close(lambda: os.path.exists(path) and os.remove(path))
    return response


@app.get("/api/admin/backup/download")
@roles_required("techadmin")
def manual_backup_download():
    return send_sqlite_backup()


@app.get("/api/backup/download")
@login_required
def user_backup_download():
    return send_sqlite_backup(include_users=False)


@app.post("/api/admin/clear-operational-data")
@roles_required("techadmin")
def clear_operational_data():
    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "BARCHASINI OCHIRISH":
        return jsonify(
            success=False,
            message="Tasdiqlash maydoniga BARCHASINI OCHIRISH deb yozing",
        ), 400
    password = data.get("password") or ""
    with get_pool().connection() as conn:
        techadmin = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s AND role = 'techadmin' AND is_active",
            (g.user["id"],),
        ).fetchone()
        if not techadmin or not check_password_hash(techadmin["password_hash"], password):
            return jsonify(success=False, message="Techadmin paroli noto'g'ri"), 403
        counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM weighings) AS weighings,
                (SELECT COUNT(*) FROM import_batches) AS imports,
                (SELECT COUNT(*) FROM zone_visits) AS zone_visits
            """
        ).fetchone()
        conn.execute(
            """TRUNCATE TABLE export_jobs, dispatch_tickets, dispatch_log, dispatch_numbers, receipt_jobs, transfer_imports, zone_events, zone_visits, print_attempts, weighings,
               import_batches, daily_sequences RESTART IDENTITY"""
        )
    return jsonify(
        success=True,
        deleted_weighings=counts["weighings"],
        deleted_imports=counts["imports"],
        deleted_zone_visits=counts["zone_visits"],
        message="Barcha tarozi, Free Zone tashriflari va pul operatsiyalari tozalandi",
    )


@app.get("/api/admin/system-status")
@roles_required("techadmin")
def system_status():
    db_started = time.perf_counter()
    today = now_local().date()
    period_start = today - timedelta(days=13)
    with get_pool().connection() as conn:
        with conn.pipeline():
            database = conn.execute(
                """
                SELECT
                    current_database() AS database_name,
                    pg_database_size(current_database()) AS used_bytes,
                    (SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections,
                    (SELECT COUNT(*) FROM weighings) AS weighing_rows,
                    (SELECT COUNT(*) FROM users WHERE is_active) AS active_users
                """
            )
            activity_rows = conn.execute(
                """
                SELECT business_date, COUNT(*) AS count, COALESCE(SUM(price), 0) AS total
                FROM weighings
                WHERE business_date BETWEEN %s AND %s AND status = 'paid'
                GROUP BY business_date
                ORDER BY business_date
                """,
                (period_start, today),
            )
            last_import = conn.execute(
                """
                SELECT filename, imported_at, total_rows, inserted_rows, skipped_rows
                FROM import_batches
                ORDER BY imported_at DESC
                LIMIT 1
                """
            )
        database = database.fetchone()
        activity_rows = activity_rows.fetchall()
        last_import = last_import.fetchone()
    db_latency_ms = round((time.perf_counter() - db_started) * 1000, 2)

    by_date = {row["business_date"]: row for row in activity_rows}
    activity = []
    for offset in range(14):
        day = period_start + timedelta(days=offset)
        row = by_date.get(day, {"count": 0, "total": 0})
        activity.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "count": row["count"],
                "total": row["total"],
            }
        )

    used_bytes = int(database["used_bytes"] or 0)
    remaining_bytes = max(0, SUPABASE_DB_LIMIT_BYTES - used_bytes)
    with _request_metrics_lock:
        durations = list(_request_durations_ms)
        request_count = _request_count
        error_count = _error_count
    avg_response_ms = round(sum(durations) / len(durations), 2) if durations else 0
    p95_response_ms = 0
    if durations:
        ordered = sorted(durations)
        p95_response_ms = round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)], 2)

    memory = _process.memory_info()
    host_memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.getenv("RENDER_PROJECT_ROOT", "/") if os.name != "nt" else str(BASE_DIR.anchor))
    uptime_seconds = int((datetime.now(timezone.utc) - _app_started_at).total_seconds())
    pool_stats = get_pool().get_stats()
    return jsonify(
        success=True,
        checked_at=now_local().isoformat(),
        overall_status="healthy" if db_latency_ms < 1000 else "degraded",
        database={
            "name": database["database_name"],
            "used_bytes": used_bytes,
            "limit_bytes": SUPABASE_DB_LIMIT_BYTES,
            "remaining_bytes": remaining_bytes,
            "used_percent": round(min(100, used_bytes * 100 / SUPABASE_DB_LIMIT_BYTES), 2),
            "connections": database["connections"],
            "weighing_rows": database["weighing_rows"],
            "latency_ms": db_latency_ms,
        },
        server={
            "uptime_seconds": uptime_seconds,
            "memory_bytes": memory.rss,
            "host_memory_percent": host_memory.percent,
            "cpu_percent": _process.cpu_percent(interval=None),
            "disk_used_percent": disk.percent,
            "requests": request_count,
            "errors": error_count,
            "avg_response_ms": avg_response_ms,
            "p95_response_ms": p95_response_ms,
            "pool": pool_stats,
        },
        application={
            "active_users": database["active_users"],
            "version": "2.6 Pro",
            "environment": os.getenv("RENDER_SERVICE_NAME", "local"),
            "last_import": (
                {
                    "filename": last_import["filename"],
                    "imported_at": localize(last_import["imported_at"]).strftime("%d.%m.%Y %H:%M"),
                    "total": last_import["total_rows"],
                    "inserted": last_import["inserted_rows"],
                    "skipped": last_import["skipped_rows"],
                }
                if last_import else None
            ),
        },
        activity=activity,
    )


@app.get("/api/backups/sqlite")
@backup_token_required
def download_backup():
    return send_sqlite_backup()


from reports import register_reports
from devices import register_devices
from station_sync import register_station
register_station(app, get_pool, get_service_prices, clean_plate)
register_devices(app, get_pool, roles_required, get_service_prices, clean_plate,
                 login_identifier, check_login_limit, register_login_failure)
register_zone(app, get_pool, roles_required, clean_plate, make_qr_base64)
register_reports(app, get_pool, roles_required, now_local, parse_date, str(APP_TIMEZONE), COMPANY_NAME)
from operations import register_operations
register_operations(app, get_pool, roles_required)
from printing import register_printing
register_printing(app, get_pool, roles_required)
from transfer import register_transfer
register_transfer(app, get_pool, roles_required)
from control_room import register_control_room
register_control_room(app, get_pool, roles_required)
from export_jobs import register_exports
register_exports(app, get_pool, roles_required)
from shifts import register_shifts
register_shifts(app, get_pool, roles_required)


@app.errorhandler(400)
def bad_request(error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, message=error.description), 400
    return render_template("error.html", code=400, message=error.description), 400


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify(success=False, message="Fayl 256 MB limitdan katta"), 413


@app.errorhandler(401)
def unauthorized(error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, message=error.description), 401
    return redirect(url_for("login"))


@app.errorhandler(403)
def forbidden(_error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, message="Ruxsat yetarli emas"), 403
    return render_template("error.html", code=403, message="Bu sahifaga kirish huquqi yo'q"), 403


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify(success=False, message="Topilmadi"), 404
    return render_template("error.html", code=404, message="Sahifa topilmadi"), 404


@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Unhandled server error: %s", error)
    if request.path.startswith("/api/"):
        return jsonify(success=False, message="Server xatosi"), 500
    return render_template("error.html", code=500, message="Server xatosi yuz berdi"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
