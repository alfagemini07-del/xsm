"""Integration checks run against the dedicated GitHub Actions PostgreSQL service."""
import io
import re
import unittest
from openpyxl import load_workbook
from app import app, ensure_schema, get_pool


class CheckpointFlow(unittest.TestCase):
    def test_visit_and_settlement(self):
        with get_pool().connection() as db:
            self.assertEqual(db.execute("SELECT current_database() AS n").fetchone()["n"],"airitom_test")
        ensure_schema()
        clients={}
        with get_pool().connection() as db:
            for role in ("parkovka_tashqi","ayritom","kpp2","admin","operator"):
                uid=db.execute("INSERT INTO users(username,role,password_hash) VALUES (%s,%s,'test') RETURNING id",
                               ("ci_"+role,role)).fetchone()["id"]
                client=app.test_client()
                with client.session_transaction() as session:
                    session.update(user_id=uid,_csrf_token="test")
                clients[role]=client

        def post(role,path,**data):
            return clients[role].post(path,data={"csrf_token":"test",**data})

        def move(role,action,plate,amount,purpose="zone"):
            preview=post(role,"/zone/quote",action=action,plate=plate,purpose=purpose)
            self.assertEqual(preview.status_code,200,preview.data)
            ticket=re.search(rb'name="ticket" value="([^"]+)"',preview.data)[1].decode()
            payload=dict(ticket=ticket,method="cash",amount=str(amount),received="yes")
            result=post(role,"/zone/confirm",**payload)
            self.assertEqual(result.status_code,303,result.data)
            self.assertEqual(clients[role].get(result.location).status_code,200)
            self.assertEqual(post(role,"/zone/confirm",**payload).status_code,303)

        self.assertEqual(clients["parkovka_tashqi"].get("/api/backup/download").status_code,403)
        self.assertEqual(clients["operator"].get("/zone").status_code,403)
        self.assertEqual(post("kpp2","/zone/quote",action="in",plate="01 A 111 AA").status_code,400)
        move("parkovka_tashqi","in","01 A 111 AA",30000)
        move("parkovka_tashqi","out","01 A 111 AA",0)
        move("kpp2","in","01A111AA",0)
        move("ayritom","out","01A111AA",0)
        move("ayritom","in","01 B 222 BB",30000)
        with get_pool().connection() as db:
            db.execute("UPDATE zone_visits SET started=started-INTERVAL '1 day' WHERE plate_key='01B222BB'")
        move("kpp2","out","01B222BB",30000)
        move("parkovka_tashqi","in","01C333CC",30000,"border")
        self.assertEqual(post("kpp2","/zone/quote",action="in",plate="01C333CC").status_code,400)
        move("parkovka_tashqi","out","01C333CC",0)
        scale=clients["operator"]
        weighing=scale.post("/api/weighings",json={"plate_number":"01D444DD"},
                            headers={"X-CSRF-Token":"test"})
        self.assertEqual(weighing.status_code,200)
        wid=weighing.json["weighing"]["id"]
        payment=scale.post(f"/api/weighings/{wid}/pay",json={"weight_kg":12500,"payment_method":"card"},
                           headers={"X-CSRF-Token":"test"})
        self.assertEqual(payment.status_code,200)
        self.assertEqual(clients["admin"].get("/zone").status_code,200)
        export=clients["admin"].get("/zone/report.xlsx?period=year")
        self.assertEqual(export.status_code,200)
        book=load_workbook(io.BytesIO(export.data),read_only=True)
        self.assertEqual(book["Operatsiyalar"].max_row,10)
        self.assertEqual(sum(row[-1] for row in list(book["Operatsiyalar"].values)[1:]),150000)
