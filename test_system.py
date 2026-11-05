"""Run only with DATABASE_URL pointing to a disposable database named airitom_test."""

import hashlib
import re
import unittest
import uuid
from werkzeug.security import generate_password_hash
from app import app, ensure_schema, get_pool


class SystemFlow(unittest.TestCase):
    def test_full_flow(self):
        with get_pool().connection() as db:
            self.assertEqual(
                db.execute("SELECT current_database() n").fetchone()["n"],
                "airitom_test",
            )
        ensure_schema()
        suffix = uuid.uuid4().hex[:8]
        clients = {}
        users = {}
        with get_pool().connection() as db:
            for role in ("operator", "parkovka_tashqi", "techadmin", "kpp2", "ayritom"):
                hashed = generate_password_hash("test-password")
                u = db.execute(
                    "INSERT INTO users(username,role,password_hash) VALUES(%s,%s,%s) RETURNING *",
                    (role + suffix, role, hashed),
                ).fetchone()
                users[role] = u
                c = app.test_client()
                with c.session_transaction() as s:
                    s.update(
                        user_id=u["id"],
                        auth_version=hashlib.sha256(hashed.encode()).hexdigest(),
                        _csrf_token="test",
                    )
                clients[role] = c
        tokens = {}
        devices = {}
        for role in ("operator", "parkovka_tashqi"):
            c = app.test_client()
            did = str(uuid.uuid4())
            devices[role] = did
            payload = dict(
                device_id=did,
                name="test " + role,
                role=role,
                username=users[role]["username"],
                password="test-password",
            )
            self.assertEqual(c.post("/api/device/login", json=payload).status_code, 403)
            self.assertEqual(
                clients["techadmin"]
                .post(
                    "/admin/devices/" + did,
                    data={"action": "approve", "csrf_token": "test"},
                )
                .status_code,
                303,
            )
            r = c.post("/api/device/login", json=payload)
            self.assertEqual(r.status_code, 200, r.data)
            tokens[role] = {"Authorization": "Device " + r.json["token"]}
        c = app.test_client()
        h = tokens["operator"]
        self.assertEqual(c.get("/api/admin/users", headers=h).status_code, 403)
        req = dict(plate_number="01A123AA", request_id=uuid.uuid4().hex)
        r = c.post("/api/weighings", json=req, headers=h)
        self.assertEqual(r.status_code, 200, r.data)
        wid = r.json["weighing"]["id"]
        price = r.json["weighing"]["price"]
        self.assertEqual(
            c.post("/api/weighings", json=req, headers=h).json["weighing"]["id"], wid
        )
        payload = dict(weight_kg=15000, payment_method="cash", expected_total=price + 1)
        self.assertEqual(
            c.post(f"/api/weighings/{wid}/pay", json=payload, headers=h).status_code,
            409,
        )
        payload["expected_total"] = price
        for _ in range(2):
            self.assertEqual(
                c.post(
                    f"/api/weighings/{wid}/pay", json=payload, headers=h
                ).status_code,
                200,
            )
        self.assertEqual(
            c.get(f"/api/weighings/{wid}/receipt", headers=h).status_code, 200
        )
        self.assertEqual(c.get(f"/receipt/{wid}", headers=h).status_code, 200)
        ph = tokens["parkovka_tashqi"]
        r = c.post(
            "/api/device/parking/quote",
            json=dict(action="in", plate="90" + suffix.upper(), purpose="zone"),
            headers=ph,
        )
        self.assertEqual(r.status_code, 200, r.data)
        payload = dict(ticket=r.json["ticket"], method="cash", received=True)
        first = c.post("/api/device/parking/commit", json=payload, headers=ph)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(
            c.post("/api/device/parking/commit", json=payload, headers=ph).json["id"],
            first.json["id"],
        )
        self.assertEqual(
            c.get(
                f"/api/device/parking/receipt/{first.json['id']}", headers=ph
            ).status_code,
            200,
        )
        kpp = app.test_client()
        selection = kpp.get("/dastur/kpp")
        self.assertEqual(selection.status_code, 200)
        for number in (1, 2):
            self.assertIn(f'href="/dastur/kpp/{number}"'.encode(), selection.data)

        def login_tablet():
            form = kpp.get("/login?next=/dastur/kpp/2")
            csrf = re.search(rb'name="csrf_token" value="([^"]+)"', form.data)[
                1
            ].decode()
            return kpp.post(
                "/login?next=/dastur/kpp/2",
                data=dict(
                    csrf_token=csrf,
                    username=users["kpp2"]["username"],
                    password="test-password",
                ),
            )

        self.assertEqual(login_tablet().status_code, 403)
        with get_pool().connection() as db:
            tablet = str(
                db.execute(
                    "SELECT id FROM devices WHERE name=%s",
                    ("Planshet · " + users["kpp2"]["username"],),
                ).fetchone()["id"]
            )
        clients["techadmin"].post(
            "/admin/devices/" + tablet, data=dict(csrf_token="test", action="approve")
        )
        self.assertEqual(login_tablet().status_code, 302)
        with kpp.session_transaction() as s:
            self.assertEqual(s["device_id"], tablet)
            csrf = s["_csrf_token"]
        self.assertEqual(
            kpp.get("/dastur/kpp/2", follow_redirects=True).status_code, 200
        )
        self.assertEqual(kpp.get("/dastur/kpp/1").status_code, 403)
        self.assertEqual(
            kpp.post("/api/kpp/heartbeat", headers={"X-CSRF-Token": csrf}).status_code,
            200,
        )
        q = kpp.post(
            "/zone/quote",
            data=dict(csrf_token=csrf, plate="90" + suffix.upper(), action="in"),
        )
        self.assertEqual(q.status_code, 200, q.data)
        ticket = re.search(rb'name="ticket" value="([^"]+)"', q.data)[1].decode()
        self.assertEqual(
            kpp.post(
                "/zone/confirm", data=dict(csrf_token=csrf, ticket=ticket)
            ).status_code,
            303,
        )
        self.assertEqual(
            kpp.post("/logout", data=dict(csrf_token=csrf)).status_code, 302
        )
        self.assertEqual(login_tablet().status_code, 302)
        with kpp.session_transaction() as s:
            self.assertEqual(s["device_id"], tablet)
        with get_pool().connection() as db:
            db.execute(
                "UPDATE devices SET last_seen=NOW()-INTERVAL '3 minutes' WHERE id=%s",
                (devices["operator"],),
            )
        self.assertEqual(clients["techadmin"].get("/zone").status_code, 200)
        self.assertEqual(clients["techadmin"].get("/admin/devices").status_code, 200)
        self.assertEqual(
            c.post("/api/device/heartbeat", json={}, headers=h).status_code, 200
        )
        with get_pool().connection() as db:
            event_types = [
                r["event"]
                for r in db.execute(
                    "SELECT event FROM device_events WHERE device_id=%s",
                    (devices["operator"],),
                )
            ]
        self.assertIn("offline", event_types)
        self.assertIn("reconnected", event_types)
        clients["techadmin"].post(
            "/admin/devices/" + devices["operator"],
            data=dict(csrf_token="test", action="block"),
        )
        self.assertEqual(c.get("/api/device/bootstrap", headers=h).status_code, 401)
        self.assertEqual(
            clients["operator"]
            .post("/api/weighings", json=req, headers={"X-CSRF-Token": "test"})
            .status_code,
            403,
        )
        with get_pool().connection() as db:
            db.execute(
                "UPDATE users SET password_hash=%s WHERE id=%s",
                (
                    generate_password_hash("changed-password"),
                    users["parkovka_tashqi"]["id"],
                ),
            )
        self.assertEqual(c.get("/api/device/bootstrap", headers=ph).status_code, 401)


if __name__ == "__main__":
    unittest.main()
