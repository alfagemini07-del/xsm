"""Receipt artwork and idempotent KPP print outcomes."""

import base64
import io
import uuid

from flask import abort, g, jsonify, request, session
from PIL import Image, ImageDraw, ImageFont
from receipt_render import font_path


def artwork(fields, qr):
    width = 384
    canvas = Image.new("RGB", (width, 2400), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(font_path(), 17)
    bold = ImageFont.truetype(font_path(True), 20)
    title = ImageFont.truetype(font_path(True), 23)
    y = 14

    def lines(value, f, limit):
        result = []
        line = ""
        for c in str(value):
            if draw.textlength(line + c, font=f) > limit:
                result.append(line)
                line = ""
            line += c
        return result + [line]

    def text(value, f=font, center=False):
        nonlocal y
        for line in lines(value, f, width - 28):
            draw.text(
                ((width - draw.textlength(line, font=f)) / 2 if center else 14, y),
                line,
                font=f,
                fill="black",
            )
            y += f.size + 6

    text("AIRITOM", title, True)
    text("LOGISTICS CENTER", bold, True)
    text("XIZMAT CHEKI", font, True)
    y += 8
    for key, value in fields:
        if key == "Hozir olindi":
            draw.line((14, y, width - 14, y), fill="black", width=2)
            y += 12
            text("JAMI: " + value, bold, True)
            y += 10
            continue
        if draw.textlength(key + "  " + str(value), font=font) <= width - 28:
            draw.text((14, y), key, font=font, fill="black")
            draw.text(
                (width - 14 - draw.textlength(str(value), font=font), y),
                str(value),
                font=font,
                fill="black",
            )
            y += 24
        else:
            text(key + ":")
            text(value, bold)
    y += 12
    # The server generates five pixels per QR module, including the quiet zone.
    # Preserve an integral number of printer dots per module on thermal paper.
    code = Image.open(io.BytesIO(base64.b64decode(qr))).convert("RGB")
    modules = code.width // 5
    side = modules * min(4, max(1, 304 // modules))
    code = code.resize((side, side), Image.Resampling.NEAREST)
    canvas.paste(code, ((width - side) // 2, y))
    y += side + 12
    text("Xizmatingiz uchun rahmat!", font, True)
    output = io.BytesIO()
    canvas.crop((0, 0, width, y + 18)).save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode()


def register_printing(app, pool, roles):
    @app.post("/api/kpp/receipts/<int:eid>/print")
    @roles("ayritom", "kpp2", "admin", "techadmin")
    def kpp_print(eid):
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400)
        try:
            aid = uuid.UUID(str(data["attempt_id"]))
        except (ValueError, KeyError):
            abort(400)
        state = data.get("state")
        method = data.get("method", "android")
        detail = str(data.get("detail", ""))[:500]
        if state not in {
            "requested",
            "sent",
            "confirmed",
            "failed",
            "cancelled",
            "unknown",
        } or method not in {"android", "bluetooth", "wifi", "internal", "browser"}:
            abort(400)
        with pool().connection() as conn:
            event = conn.execute(
                "SELECT point FROM zone_events WHERE id=%s", (eid,)
            ).fetchone()
            if not event:
                abort(404)
            if (
                g.user["role"] not in {"admin", "techadmin"}
                and g.user["role"] != event["point"]
            ):
                abort(403)
            conn.execute(
                "INSERT INTO receipt_jobs(id,event_id,user_id,device_id,state,method,detail) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    aid,
                    eid,
                    g.user["id"],
                    session.get("device_id"),
                    state,
                    method,
                    detail,
                ),
            )
            job = conn.execute(
                "SELECT * FROM receipt_jobs WHERE id=%s FOR UPDATE", (aid,)
            ).fetchone()
            if (
                job["event_id"] != eid
                or job["user_id"] != g.user["id"]
                or str(job["device_id"]) != str(session.get("device_id"))
            ):
                abort(403)
            # Late delivery of a queued "sent" callback must not undo a confirmation.
            if job["state"] != "confirmed" and (
                state not in {"requested", "sent"} or job["state"] == "requested"
            ):
                conn.execute(
                    "UPDATE receipt_jobs SET state=%s,method=%s,detail=%s,updated_at=NOW() WHERE id=%s",
                    (state, method, detail, aid),
                )
            summary = conn.execute(
                "SELECT COUNT(*) attempts,COUNT(*) FILTER(WHERE state='confirmed') confirmed FROM receipt_jobs WHERE event_id=%s",
                (eid,),
            ).fetchone()
        return jsonify(success=True, **summary)
