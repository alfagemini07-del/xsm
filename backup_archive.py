"""Portable large operational backups with one JSON object per line."""
import hashlib
import json
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from flask import abort, send_file
from psycopg import sql

FORMAT = "xsm-operations-lines-v1"
MAX_BYTES = 512 * 1024**2
MAX_FILE = 250 * 1024**2
MAX_LINE = 2 * 1024**2


def export_archive(conn, tables, encode):
    target = tempfile.SpooledTemporaryFile(max_size=1024**2, mode="w+b")
    total = 0
    try:
        with ZipFile(target, "w", ZIP_DEFLATED, compresslevel=1) as archive:
            timestamp = conn.execute("SELECT now() AS at").fetchone()["at"]
            archive.writestr("manifest.json", json.dumps({"format": FORMAT, "at": timestamp.isoformat()}))
            for table, fields in tables.items():
                columns = sql.SQL(",").join(map(sql.Identifier, fields)) if fields else sql.SQL("*")
                with conn.cursor(name="backup_"+table) as cursor, archive.open(table+".jsonl","w") as output:
                    cursor.itersize = 256
                    cursor.execute(sql.SQL("SELECT {} FROM {}").format(columns, sql.Identifier(table)))
                    for row in cursor:
                        line = (json.dumps(row, default=encode, ensure_ascii=False)+"\n").encode()
                        total += len(line)
                        if len(line)>MAX_LINE or total>MAX_BYTES:
                            abort(413, description="Baza arxiv chegarasidan katta. PostgreSQL zaxirasidan foydalaning.")
                        output.write(line)
        if target.tell()>MAX_FILE:
            abort(413, description="Siqilgan arxiv 250 MB dan oshdi.")
        target.seek(0)
        return send_file(target, mimetype="application/zip", as_attachment=True, download_name="XSM-umumiy-zaxira.zip")
    except BaseException:
        target.close()
        raise


def archive_rows(stream, table):
    with ZipFile(stream) as archive, archive.open(table+".jsonl") as source:
        while line := source.readline(MAX_LINE+1):
            if len(line)>MAX_LINE:
                raise ValueError("Arxiv yozuvi juda katta")
            yield json.loads(line)


def read_archive(stream, tables):
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024**2), b""):
        size += len(chunk)
        if size>MAX_FILE:
            abort(413, description="Arxiv 250 MB dan oshdi")
        digest.update(chunk)
    try:
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            expected = {"manifest.json", *(name+".jsonl" for name in tables)}
            if len(entries)!=len(expected) or set(archive.namelist())!=expected:
                raise ValueError("Arxiv jadvallari mos emas")
            if sum(item.file_size for item in entries)>MAX_BYTES+4096:
                raise ValueError("Arxiv tarkibi 512 MB dan katta")
            if any(item.flag_bits & 1 or item.compress_type != ZIP_DEFLATED for item in entries):
                raise ValueError("Arxiv siqish formati mos emas")
            if archive.getinfo("manifest.json").file_size>4096 or json.loads(archive.read("manifest.json"))["format"]!=FORMAT:
                raise ValueError("Arxiv turi mos emas")
    except (BadZipFile, ValueError, KeyError, TypeError) as exc:
        abort(400, description=str(exc))
    return {"tables": {name:archive_rows(stream,name) for name in tables}}, digest.hexdigest()
