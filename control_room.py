"""Shared read model for the control room. Animation never writes business events."""
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Lock
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

from flask import abort, g, jsonify, render_template, request
from psycopg.types.json import Jsonb

TZ = ZoneInfo("Asia/Tashkent")


def register_control_room(app, pool, roles):
    lock = Lock()
    cache = {}

    def snapshot():
        now = datetime.now(TZ)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with pool().connection() as conn:
            with conn.pipeline():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                config = conn.execute("SELECT * FROM dispatch_config WHERE id")
                vehicles = conn.execute("""
                    SELECT v.id,v.plate,v.started,v.parking_departed_at,t.area,t.slot,t.flow,t.number,t.day,t.called_at,
                           t.queue_state,t.priority,t.version
                    FROM dispatch_tickets t JOIN zone_visits v ON v.id=t.visit_id
                    WHERE t.area<>'closed' ORDER BY v.started,v.id LIMIT 2501""")
                totals = conn.execute("""
                    SELECT point,COUNT(*) n,SUM(amount)::bigint amount FROM operation_ledger
                    WHERE status='paid' AND occurred>=%s AND occurred<%s GROUP BY point""",
                    (midnight,midnight+timedelta(days=1)))
                latest = conn.execute("""
                    SELECT receipt_no,plate,point,action,amount,occurred FROM operation_ledger
                    WHERE status='paid' ORDER BY occurred DESC,id DESC LIMIT 12""")
                counts = conn.execute("""SELECT t.area,count(*) n,
                    count(*) FILTER (WHERE t.area='parking' AND v.parking_departed_at IS NULL) parked,
                    count(*) FILTER (WHERE t.area='parking' AND v.parking_departed_at IS NOT NULL) in_transit,
                    count(*) FILTER (WHERE t.area='parking' AND v.parking_departed_at IS NULL AND t.flow='CH') border_wait
                    FROM dispatch_tickets t JOIN zone_visits v ON v.id=t.visit_id
                    WHERE t.area<>'closed' GROUP BY t.area""")
                crossings = conn.execute("""SELECT point,count(*) FILTER (WHERE action='in') arrivals,
                         count(*) FILTER (WHERE action='out') departures FROM zone_events
                    WHERE point IN ('kpp2','ayritom','parkovka_tashqi') AND occurred >= %s AND occurred < %s
                    GROUP BY point""", (midnight, midnight+timedelta(days=1)))
                parking_flow = conn.execute("""
                    SELECT count(*) FILTER (WHERE e.action='in') AS arrivals,
                           count(*) FILTER (WHERE e.action='out') AS departures,
                           count(*) FILTER (WHERE e.action='out' AND v.purpose='border') AS border_departures,
                           count(*) FILTER (WHERE e.action='out' AND v.purpose='zone') AS zone_departures
                    FROM zone_events e JOIN zone_visits v ON v.id=e.visit_id
                    WHERE e.point='parkovka_tashqi' AND e.occurred >= %s AND e.occurred < %s
                """, (midnight, midnight+timedelta(days=1)))
                weighed = conn.execute("""SELECT count(DISTINCT plate_search) n FROM weighings
                    WHERE status='paid' AND paid_at >= %s AND paid_at < %s""",
                    (midnight, midnight+timedelta(days=1)))
                exits = conn.execute("""SELECT visit_id,point FROM zone_events
                    WHERE received_at >= now()-interval '2 minutes' AND action='out' AND location='closed'
                    ORDER BY received_at DESC,id DESC LIMIT 1000""")
                calls = conn.execute("SELECT l.at,l.action,l.actor,l.detail,v.plate FROM dispatch_log l LEFT JOIN zone_visits v ON v.id=l.visit_id ORDER BY l.id DESC LIMIT 8")
                spots = conn.execute("SELECT * FROM dispatch_spots ORDER BY area,slot")
                gates = conn.execute("SELECT * FROM dispatch_gates ORDER BY id")
                queue = conn.execute("""SELECT t.flow,t.queue_state,count(*) n,
                    ROUND(AVG(EXTRACT(epoch FROM (now()-v.started))/60)) wait_minutes
                    FROM dispatch_tickets t JOIN zone_visits v ON v.id=t.visit_id
                    WHERE t.area='parking' GROUP BY t.flow,t.queue_state""")
            return dict(at=now.isoformat(),config=config.fetchone(),vehicles=vehicles.fetchall(),
                        totals=totals.fetchall(),events=latest.fetchall(),counts=counts.fetchall(),calls=calls.fetchall(),
                        spots=spots.fetchall(),gates=gates.fetchall(),queue=queue.fetchall(),
                        crossings=crossings.fetchall(),parking_flow=parking_flow.fetchone(),
                        recent_exits=exits.fetchall(),weighed_today=weighed.fetchone()["n"])

    def cached_snapshot():
        with lock:
            if monotonic() >= cache.get("until",0):
                data = snapshot()
                stable = {key: value for key, value in data.items() if key != "at"}
                cache["etag"] = sha256(app.json.dumps(stable).encode()).hexdigest()
                cache["payload"] = app.json.dumps(data)
                cache["data"] = data
                cache["until"] = monotonic()+8
            return cache["data"], cache["payload"], cache["etag"]

    @app.get("/admin/control-room")
    @roles("admin","techadmin","observer")
    def control_room():
        return render_template("control_room.html",csrf_token_value=app.jinja_env.globals["csrf_token"](),user=g.user)

    @app.get("/api/admin/control-room")
    @roles("admin","techadmin","observer")
    def room_data():
        data, payload, tag = cached_snapshot()
        if request.if_none_match.contains_weak(tag):
            response = app.response_class(status=304)
        else:
            response = app.response_class(payload, mimetype="application/json")
        response.set_etag(tag, weak=True)
        response.headers["X-Snapshot-Time"] = data["at"]
        return response

    @app.get("/admin/queue-display")
    @roles("admin","techadmin","display","observer")
    def queue_display():
        return render_template("queue_display.html")

    @app.get("/api/admin/queue-display")
    @roles("admin","techadmin","display","observer")
    def queue_display_data():
        data, _, _ = cached_snapshot()
        # The wall display contains no payments, names, plate numbers or device details.
        return jsonify(at=data["at"],tickets=[{k:v[k] for k in ("id","flow","number","day","queue_state","called_at","priority")}
            for v in data["vehicles"] if v["area"]=="parking" and v["queue_state"] in ("waiting","called")])

    @app.post("/api/admin/control-room/call")
    @roles("admin","techadmin")
    def call_next():
        data=request.get_json() or {}
        flow=data.get("flow")
        try:
            command=UUID(data["request_id"])
        except (ValueError,KeyError,TypeError):
            abort(400)
        if flow not in ("CH","FZ"):
            abort(400)
        with pool().connection() as conn:
            conn.execute("SELECT id FROM dispatch_config WHERE id FOR UPDATE")
            old=conn.execute("SELECT visit_id,action,actor FROM dispatch_log WHERE command_id=%s",(command,)).fetchone()
            if old:
                if old["actor"]!=g.user["username"] or old["action"]!="call."+flow: abort(409)
                return jsonify(visit_id=old["visit_id"])
            row=conn.execute("""SELECT visit_id FROM dispatch_tickets WHERE flow=%s AND area='parking'
                              AND queue_state='waiting' ORDER BY priority DESC,day,number LIMIT 1 FOR UPDATE""",(flow,)).fetchone()
            if not row:
                return jsonify(message="Kutayotgan navbat yo‘q"),409
            vid=row["visit_id"]
            conn.execute("UPDATE dispatch_tickets SET called_at=now(),called_by=%s,queue_state='called',version=version+1 WHERE visit_id=%s",(g.user["id"],vid))
            conn.execute("INSERT INTO dispatch_log(visit_id,action,actor,command_id) VALUES(%s,%s,%s,%s)",(vid,"call."+flow,g.user["username"],command))
        with lock: cache.clear()
        return jsonify(visit_id=vid)

    @app.post("/api/admin/control-room/config")
    @roles("techadmin")
    def room_settings():
        data=request.get_json() or {}
        try:
            a,b,h=(int(data[k]) for k in ("parking_capacity","inside_capacity","reset_hour"))
            timeout=int(data.get("no_show_minutes",5))
        except (ValueError,TypeError,KeyError): abort(400)
        if not (1<=a<=2500 and 1<=b<=2500 and 0<=h<=23 and 1<=timeout<=120): abort(400)
        with pool().connection() as conn:
            cfg = conn.execute("SELECT * FROM dispatch_config WHERE id FOR UPDATE").fetchone()
            try:
                warning = int(data.get("warning_percent", cfg["warning_percent"]))
                critical = int(data.get("critical_percent", cfg["critical_percent"]))
            except (ValueError, TypeError):
                abort(400)
            if not 1 <= warning < critical < 100:
                return jsonify(message="Bandlik chegaralari 1–99 orasida va o‘sish tartibida bo‘lsin"), 400
            used=conn.execute("""SELECT 1 FROM (SELECT area,slot FROM dispatch_tickets UNION ALL SELECT area,slot FROM dispatch_spots) s
                WHERE (area='parking' AND slot>%s) OR (area='inside' AND slot>%s) LIMIT 1""",(a,b)).fetchone()
            if used: return jsonify(message="Band joyni o‘chirib bo‘lmaydi"),409
            conn.execute("""UPDATE dispatch_config SET parking_capacity=%s,inside_capacity=%s,reset_hour=%s,
                         no_show_minutes=%s,warning_percent=%s,critical_percent=%s""",(a,b,h,timeout,warning,critical))
            conn.execute("INSERT INTO dispatch_log(action,actor,detail) VALUES(%s,%s,%s)",
                         ("settings",g.user["username"],Jsonb(dict(parking=a,inside=b,reset_hour=h,no_show_minutes=timeout,
                                                                  warning_percent=warning,critical_percent=critical))))
        with lock: cache.clear()
        return jsonify(success=True)

    @app.post("/api/admin/control-room/command")
    @roles("admin","techadmin")
    def room_command():
        data=request.get_json(silent=True) or {}
        try:
            command=UUID(data["request_id"])
            action=str(data["action"])
            reason=str(data.get("reason","")).strip()
            if not 3<=len(reason)<=400: raise ValueError()
        except (ValueError,TypeError,KeyError):
            return jsonify(message="Amal sababi kamida 3, ko‘pi bilan 400 belgi bo‘lsin"),400
        allowed={"hold","resume","no_show","cancel","recall","priority","assign","spot","gate"}
        if action not in allowed: abort(400)
        if action in {"spot","gate","priority"} and g.user["role"]!="techadmin": abort(403)
        # This lock is also used by the visit trigger, keeping slot and queue allocation atomic.
        with pool().connection() as conn:
            cfg=conn.execute("SELECT * FROM dispatch_config WHERE id FOR UPDATE").fetchone()
            payload={k:v for k,v in data.items() if k!="request_id"}
            old=conn.execute("SELECT action,actor,detail FROM dispatch_log WHERE command_id=%s",(command,)).fetchone()
            if old:
                if old["actor"]!=g.user["username"] or old["detail"]!=payload: abort(409)
                return jsonify(success=True,repeated=True)
            vid=None
            try:
                if action=="spot":
                    area=data["area"]; slot=int(data["slot"]); state=data["state"]
                    if area not in ("parking","inside") or not 1<=slot<=cfg[area+"_capacity"] or state not in ("free","reserved","disabled"): raise ValueError()
                    if conn.execute("SELECT 1 FROM dispatch_tickets WHERE area=%s AND slot=%s",(area,slot)).fetchone():
                        return jsonify(message="Band joyni o‘zgartirib bo‘lmaydi"),409
                    conn.execute("DELETE FROM dispatch_spots WHERE area=%s AND slot=%s",(area,slot))
                    if state!="free": conn.execute("INSERT INTO dispatch_spots(area,slot,state,reason,actor) VALUES(%s,%s,%s,%s,%s)",(area,slot,state,reason,g.user["username"]))
                elif action=="gate":
                    gate=int(data["gate"]); state=data["state"]
                    if gate not in (1,2) or state not in ("open","closed","locked","fault","maintenance"): raise ValueError()
                    conn.execute("UPDATE dispatch_gates SET state=%s,reason=%s,actor=%s,updated_at=now() WHERE id=%s",(state,reason,g.user["username"],gate))
                else:
                    vid=int(data["visit_id"]); version=int(data["version"])
                    ticket=conn.execute("SELECT * FROM dispatch_tickets WHERE visit_id=%s FOR UPDATE",(vid,)).fetchone()
                    if not ticket: abort(404)
                    if ticket["version"]!=version: return jsonify(message="Avtomobil holati yangilangan. Yangilangan ma’lumotni tekshiring"),409
                    current=ticket["queue_state"]
                    transitions={"hold":({"waiting","called"},"hold"),"resume":({"hold","no_show","cancelled"},"waiting"),
                        "no_show":({"called"},"no_show"),"cancel":({"waiting","called","hold","no_show"},"cancelled"),"recall":({"called"},"called")}
                    if action in transitions:
                        source,target=transitions[action]
                        if ticket["area"]!="parking" or current not in source: return jsonify(message="Hozirgi holatda bu amal mumkin emas"),409
                        conn.execute("""UPDATE dispatch_tickets SET queue_state=%s,version=version+1,
                          called_at=CASE WHEN %s='called' THEN now() ELSE NULL END,called_by=%s WHERE visit_id=%s""",(target,target,g.user["id"],vid))
                    elif action=="priority":
                        priority=int(data["priority"])
                        if not 0<=priority<=2 or ticket["area"]!="parking": raise ValueError()
                        conn.execute("UPDATE dispatch_tickets SET priority=%s,version=version+1 WHERE visit_id=%s",(priority,vid))
                    elif action=="assign":
                        area=ticket["area"]; slot=int(data["slot"])
                        if area not in ("parking","inside") or not 1<=slot<=cfg[area+"_capacity"]: raise ValueError()
                        if conn.execute("""SELECT 1 FROM dispatch_tickets WHERE area=%s AND slot=%s AND visit_id<>%s
                            UNION ALL SELECT 1 FROM dispatch_spots WHERE area=%s AND slot=%s""",(area,slot,vid,area,slot)).fetchone():
                            return jsonify(message="Joy band, rezervda yoki xizmatdan tashqari"),409
                        conn.execute("UPDATE dispatch_tickets SET slot=%s,version=version+1 WHERE visit_id=%s",(slot,vid))
            except (ValueError,TypeError,KeyError): return jsonify(message="Amal qiymatlari noto‘g‘ri"),400
            conn.execute("INSERT INTO dispatch_log(visit_id,action,actor,command_id,detail) VALUES(%s,%s,%s,%s,%s)",
                (vid,action,g.user["username"],command,Jsonb(payload)))
        with lock: cache.clear()
        return jsonify(success=True)

    @app.get("/api/admin/visits/<int:vid>/timeline")
    @roles("admin","techadmin","observer")
    def visit_timeline(vid):
        with pool().connection() as conn:
            visit=conn.execute("SELECT id,plate,purpose,started FROM zone_visits WHERE id=%s",(vid,)).fetchone()
            if not visit: abort(404)
            events=conn.execute("""SELECT point,action,occurred,operator_name,amount,method,
                        (SELECT count(*) FROM receipt_jobs j WHERE j.event_id=e.id AND state='confirmed') printed
                        FROM zone_events e WHERE visit_id=%s ORDER BY occurred,id""",(vid,)).fetchall()
            audit=conn.execute("SELECT at,action,actor,detail FROM dispatch_log WHERE visit_id=%s ORDER BY id DESC LIMIT 100",(vid,)).fetchall()
        return jsonify(visit=visit,events=events,audit=audit)
