"""Starts the three real entry points together against a disposable airitom_test DB."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor
from uuid import uuid4

import psycopg
from werkzeug.security import generate_password_hash

root = Path(__file__).resolve().parent.parent
server_url = "http://127.0.0.1:17650"


def call(browser, address, data=None, csrf=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = Request(
        address,
        headers=headers,
        data=None if data is None else json.dumps(data).encode(),
    )
    try:
        with browser.open(req, timeout=4) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def stop(process):
    process.terminate()
    process.wait(timeout=10)


def main():
    import socket

    ports = (17650, 17830, 17831)
    for port in ports:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    with ExitStack() as stack:
        db = stack.enter_context(
            psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
        )
        assert db.execute("SELECT current_database()").fetchone()[0] == "airitom_test"
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        env = dict(
            os.environ, PORT="17650", AIRITOM_SERVER=server_url, AIRITOM_NO_BROWSER="1"
        )
        folders = [("server", root / "server")]
        for name in ("tashqi-parkovka", "tarozi"):
            clone = scratch / name
            for entry in ("templates", "static"):
                shutil.copytree(root / name / entry, clone / entry)
            for entry in (root / name).glob("*.py"):
                shutil.copy2(entry, clone / entry.name)
            folders.append((name, clone))
        for name, folder in folders:
            log = stack.enter_context((scratch / (name + ".log")).open("w"))
            process = subprocess.Popen(
                [sys.executable, "app.py"], cwd=folder, env=env, stdout=log, stderr=log
            )
            stack.callback(stop, process)
        password = uuid4().hex
        for name, role, port in (
            ("tashqi-parkovka", "parkovka_tashqi", 17830),
            ("tarozi", "operator", 17831),
        ):
            address = f"http://127.0.0.1:{port}"
            browser = build_opener(HTTPCookieProcessor(CookieJar()))
            for _ in range(40):
                try:
                    code, html = call(browser, address)
                    if code == 200 and call(browser, server_url + "/health")[0] == 200:
                        break
                except URLError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError(name + " ishga tushmadi")
            csrf = re.search(rb'data-csrf="([^"]+)"', html)[1].decode()
            username = "smoke_" + uuid4().hex[:12]
            db.execute(
                "INSERT INTO users(username,role,password_hash) VALUES(%s,%s,%s)",
                (username, role, generate_password_hash(password)),
            )
            credentials = dict(username=username, password=password)
            assert call(browser, address + "/login", credentials, csrf)[0] == 403
            did = (scratch / name / "device-id.txt").read_text()
            db.execute("UPDATE devices SET approved=TRUE WHERE id=%s", (did,))
            assert call(browser, address + "/login", credentials, csrf)[0] == 200
            assert (
                json.loads(call(browser, address + "/status")[1])["user"]["username"]
                == username
            )
            if role == "operator":
                path = "/bridge/api/weighings"
                data = dict(plate_number="01 A 777 BA", request_id=uuid4().hex)
            else:
                path = "/bridge/api/device/parking/quote"
                data = dict(plate=uuid4().hex[:10], action="in", purpose="zone")
            code, body = call(browser, address + path, data, csrf)
            assert code == 200, body
            assert call(browser, address + "/logout", {}, csrf)[0] == 200
            assert call(browser, address + path, data, csrf)[0] == 401
            print(name, port, "login, bridge and logout OK")


if __name__ == "__main__":
    main()
