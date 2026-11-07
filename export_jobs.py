"""Bounded, restart-recoverable Excel jobs. One export runs across all web workers."""
import io
import threading
from uuid import UUID
from flask import abort, g, jsonify, request, send_file
from psycopg.types.json import Jsonb
from werkzeug.exceptions import HTTPException
from operations import selection


def register_exports(app, pool, roles):
    mutex=threading.Lock()

    def worker():
        try:
            while True:
                with pool().connection() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(193094,42)")
                    conn.execute("DELETE FROM export_jobs WHERE created_at<now()-interval '6 hours' AND state<>'running'")
                    conn.execute("""UPDATE export_jobs SET state=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,
                        error='Eksport uzildi. Davrni toraytirib qayta yarating.'
                        WHERE state='running' AND started_at<now()-interval '10 minutes'""")
                    if conn.execute("SELECT 1 FROM export_jobs WHERE state='running'").fetchone(): return
                    job=conn.execute("SELECT * FROM export_jobs WHERE state='queued' ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
                    if not job: return
                    conn.execute("UPDATE export_jobs SET state='running',started_at=now(),attempts=attempts+1 WHERE id=%s",(job["id"],))
                    user=conn.execute("SELECT * FROM users WHERE id=%s AND is_active AND role IN ('admin','techadmin')",(job["user_id"],)).fetchone()
                content=None; error=None
                try:
                    if not user: raise ValueError("Hisobot yaratish huquqi bekor qilingan")
                    with app.test_request_context('/api/admin/operations/export.xlsx',query_string=job["filters"]):
                        g.user=user
                        response=app.view_functions['operations_export']()
                        try:
                            response.direct_passthrough=False
                            content=response.get_data()
                            if len(content)>12*1024*1024: raise ValueError("Fayl 12 MB dan katta. Davr yoki nuqtani toraytiring")
                        finally: response.close()
                except HTTPException as exc: error=exc.description
                except ValueError as exc: error=str(exc)
                except Exception:
                    app.logger.exception("Background export failed job=%s",job["id"])
                    error="Hisobot tayyorlanmadi. Qayta urinib ko‘ring"
                with pool().connection() as conn:
                    conn.execute("UPDATE export_jobs SET state=%s,finished_at=now(),content=%s,error=%s WHERE id=%s AND state='running' AND attempts=%s",
                        ('failed' if error else 'ready',None if error else content,error,job["id"],job["attempts"]+1))
        except Exception:
            app.logger.exception("Export worker stopped; pending jobs remain recoverable")
        finally: mutex.release()

    def wake():
        if mutex.acquire(blocking=False): threading.Thread(target=worker,daemon=True,name='xsm-export').start()

    @app.post('/api/admin/exports')
    @roles('admin','techadmin')
    def export_create():
        data=request.get_json(silent=True) or {}
        try: jobid=UUID(data['request_id'])
        except (ValueError,TypeError,KeyError): abort(400)
        filters=data.get('filters')
        allowed={'start','end','point','method','status','operator','plate'}
        if not isinstance(filters,dict) or not set(filters)<=allowed or any(not isinstance(v,str) or len(v)>100 for v in filters.values()): abort(400)
        selection(filters,False)
        with pool().connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(193094,42)")
            old=conn.execute("SELECT user_id,filters FROM export_jobs WHERE id=%s",(jobid,)).fetchone()
            if old:
                if old['user_id']!=g.user['id'] or old['filters']!=filters: abort(409)
            else:
                pending=conn.execute("SELECT count(*) n,count(*) FILTER(WHERE user_id=%s) own FROM export_jobs WHERE state IN ('queued','running')",(g.user['id'],)).fetchone()
                if pending['n']>=10 or pending['own']>=2: return jsonify(message="Oldingi eksport tayyor bo‘lishini kuting"),429
                conn.execute("INSERT INTO export_jobs(id,user_id,filters) VALUES(%s,%s,%s)",(jobid,g.user['id'],Jsonb(filters)))
        wake(); return jsonify(id=str(jobid)),202

    def read_owned(jobid):
        with pool().connection() as conn:
            job=conn.execute("SELECT id,user_id,state,error,created_at,finished_at FROM export_jobs WHERE id=%s AND created_at>now()-interval '6 hours'",(jobid,)).fetchone()
        if not job or job['user_id']!=g.user['id']: abort(404)
        return job

    @app.get('/api/admin/exports/<uuid:jobid>')
    @roles('admin','techadmin')
    def export_state(jobid):
        job=read_owned(jobid)
        if job['state'] in ('queued','running'): wake()
        return jsonify(job)

    @app.get('/api/admin/exports/<uuid:jobid>/file')
    @roles('admin','techadmin')
    def export_file(jobid):
        if read_owned(jobid)['state']!='ready': abort(409)
        with pool().connection() as conn:
            row=conn.execute("SELECT content FROM export_jobs WHERE id=%s",(jobid,)).fetchone()
        if not row: abort(404)
        return send_file(io.BytesIO(bytes(row['content'])),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='XSM-umumiy-hisobot.xlsx')
