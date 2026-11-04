"""Windows local companion for driver/ESC-POS printing and daily SQLite backup.

Commands:
    AgentSetup.exe                  # grafik o'rnatuvchi
    python local_agent.py configure
    python local_agent.py serve
    python local_agent.py backup
    python local_agent.py test-print
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SOURCE_DIR = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TaroziKioskAgent"
    if IS_FROZEN else SOURCE_DIR
)
CONFIG_PATH = DATA_DIR / "local_agent_config.json"
LOG_PATH = DATA_DIR / "local_agent.log"
INSTALLED_EXE = DATA_DIR / "TaroziPrinterAgent.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TaroziKioskPrinterAgent"
DEFAULT_SITE_URL = "https://xalqaro-savdo-markazi.onrender.com"


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handlers = [handler]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RuntimeError("local_agent_config.json topilmadi. Avval 'python local_agent.py configure' ni bajaring.")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ("site_url", "allowed_origin")
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise RuntimeError("Konfiguratsiyada yetishmaydi: " + ", ".join(missing))
    config.setdefault("printer_name", "")
    config.setdefault("print_mode", "windows")
    config.setdefault("backup_folder", str(Path.home() / "Tarozi Backups"))
    config.setdefault("backup_token", "")
    config.setdefault("agent_port", 17832)
    return config


def configure() -> None:
    previous = {}
    if CONFIG_PATH.exists():
        try:
            previous = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}

    def ask(label: str, key: str, default: str = "") -> str:
        current = str(previous.get(key, default))
        suffix = f" [{current}]" if current else ""
        value = input(f"{label}{suffix}: ").strip()
        return value or current

    site_url = ask("Render sayt URL (https://...onrender.com)", "site_url").rstrip("/")
    parsed = urlparse(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Sayt URL noto'g'ri")
    default_origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed_origin = ask("Brauzer origin", "allowed_origin", default_origin).rstrip("/")
    backup_folder = ask("Backup papka to'liq yo'li", "backup_folder", str(Path.home() / "Tarozi Backups"))
    backup_token = ask("Avtomatik backup tokeni (ixtiyoriy, bo'sh qoldirish mumkin)", "backup_token")
    printer_name = ask("Printer nomi (bo'sh bo'lsa Windows default printer)", "printer_name")
    print_mode = ask("Chop etish rejimi: windows yoki escpos", "print_mode", "windows").lower()
    if print_mode not in {"windows", "escpos"}:
        raise RuntimeError("Chop etish rejimi faqat windows yoki escpos bo'lishi mumkin")
    port_text = ask("Lokal agent porti", "agent_port", "17832")
    port = int(port_text)
    if port < 1024 or port > 65535:
        raise RuntimeError("Port 1024-65535 oralig'ida bo'lishi kerak")

    config = {
        "site_url": site_url,
        "allowed_origin": allowed_origin,
        "backup_folder": str(Path(backup_folder).expanduser().resolve()),
        "backup_token": backup_token,
        "printer_name": printer_name,
        "print_mode": print_mode,
        "agent_port": port,
    }
    Path(config["backup_folder"]).mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.stdout is not None:
        print(f"Saqlandi: {CONFIG_PATH}")


def list_printers() -> tuple[list[str], str]:
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = sorted({str(item[2]) for item in win32print.EnumPrinters(flags) if item[2]})
        default = win32print.GetDefaultPrinter() or ""
        if default and default not in names:
            names.insert(0, default)
        return names, default
    except Exception:
        return [], ""


def save_config(config: dict) -> None:
    site_url = str(config.get("site_url", "")).strip().rstrip("/")
    parsed = urlparse(site_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Sayt manzili https:// bilan boshlanishi kerak")
    port = int(config.get("agent_port", 17832))
    if not 1024 <= port <= 65535:
        raise RuntimeError("Agent porti 1024–65535 oralig'ida bo'lishi kerak")
    backup_folder = Path(str(config.get("backup_folder") or Path.home() / "Tarozi Backups")).expanduser().resolve()
    backup_folder.mkdir(parents=True, exist_ok=True)
    saved = {
        "site_url": site_url,
        "allowed_origin": f"{parsed.scheme}://{parsed.netloc}",
        "backup_folder": str(backup_folder),
        "backup_token": str(config.get("backup_token", "")).strip(),
        "printer_name": str(config.get("printer_name", "")).strip(),
        "print_mode": "escpos" if config.get("print_mode") == "escpos" else "windows",
        "agent_port": port,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, CONFIG_PATH)


def register_startup(executable: Path) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{executable}" serve')


def register_daily_backup(executable: Path, enabled: bool) -> str:
    task_name = "TaroziKiosk Daily Backup"
    if not enabled:
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)
        return ""
    result = subprocess.run(
        ["schtasks", "/Create", "/SC", "DAILY", "/ST", "00:05", "/TN", task_name,
         "/TR", f'"{executable}" backup', "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return "" if result.returncode == 0 else (result.stderr or result.stdout).strip()


def install_executable(config: dict, test_print: bool = False) -> tuple[Path, str]:
    if os.name != "nt" or not IS_FROZEN:
        raise RuntimeError("O'rnatish uchun GitHub Actions yaratgan AgentSetup.exe faylini ishga tushiring")
    save_config(config)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(sys.executable).resolve()
    target = INSTALLED_EXE.resolve()
    if source != target:
        try:
            shutil.copy2(source, target)
        except PermissionError as exc:
            raise RuntimeError("Eski agent ishlayapti. Task Manager orqali TaroziPrinterAgent.exe ni yoping va qayta urinib ko'ring") from exc
    register_startup(target)
    schedule_warning = register_daily_backup(target, bool(str(config.get("backup_token", "")).strip()))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([str(target), "serve"], cwd=str(DATA_DIR), creationflags=creation_flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if test_print:
        subprocess.run([str(target), "test-print"], cwd=str(DATA_DIR), creationflags=creation_flags,
                       timeout=45, check=True)
    return target, schedule_warning


def installer_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    previous = {}
    if CONFIG_PATH.exists():
        try:
            previous = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    printers, default_printer = list_printers()
    root = tk.Tk()
    root.title("Tarozi printer agentini o'rnatish")
    root.geometry("610x510")
    root.resizable(False, False)
    root.option_add("*Font", ("Segoe UI", 10))
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Tarozi Printer Agenti", font=("Segoe UI Semibold", 20)).pack(anchor="w")
    ttk.Label(frame, text="Bir marta sozlang — keyin cheklar brauzer oynasisiz avtomatik chiqadi.").pack(anchor="w", pady=(2, 18))

    site = tk.StringVar(value=previous.get("site_url", DEFAULT_SITE_URL))
    printer = tk.StringVar(value=previous.get("printer_name", default_printer))
    mode = tk.StringVar(value=previous.get("print_mode", "windows"))
    backup = tk.StringVar(value=previous.get("backup_folder", str(Path.home() / "Tarozi Backups")))
    token = tk.StringVar(value=previous.get("backup_token", ""))
    test = tk.BooleanVar(value=False)

    def field(label, variable, values=None, secret=False):
        ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 4))
        widget = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly") if values else ttk.Entry(frame, textvariable=variable, show="*" if secret else "")
        widget.pack(fill="x")
        return widget

    field("Render sayt manzili", site)
    field("Chek printeri", printer, printers or [""])
    field("Chop etish usuli", mode, ["windows", "escpos"])
    ttk.Label(frame, text="Backup papkasi (ixtiyoriy token ishlatilsa)").pack(anchor="w", pady=(8, 4))
    backup_row = ttk.Frame(frame); backup_row.pack(fill="x")
    ttk.Entry(backup_row, textvariable=backup).pack(side="left", fill="x", expand=True)
    ttk.Button(backup_row, text="Tanlash", command=lambda: backup.set(filedialog.askdirectory(initialdir=backup.get()) or backup.get())).pack(side="left", padx=(8, 0))
    field("Avtomatik backup tokeni (ixtiyoriy)", token, secret=True)
    ttk.Checkbutton(frame, text="O'rnatilgach test chek chiqarish", variable=test).pack(anchor="w", pady=12)
    status = ttk.Label(frame, text="", foreground="#146c43"); status.pack(anchor="w")

    def install():
        button.config(state="disabled")
        status.config(text="O'rnatilmoqda...")
        root.update_idletasks()
        try:
            target, warning = install_executable({
                "site_url": site.get(), "printer_name": printer.get(), "print_mode": mode.get(),
                "backup_folder": backup.get(), "backup_token": token.get(), "agent_port": 17832,
            }, test.get())
            message = f"Agent o'rnatildi va ishga tushdi.\n\n{target}"
            if warning:
                message += "\n\nBackup vazifasi yaratilmagan: " + warning
            messagebox.showinfo("Tayyor", message)
            root.destroy()
        except Exception as exc:
            logging.exception("Installer failed")
            status.config(text="O'rnatilmadi", foreground="#b42318")
            messagebox.showerror("Xatolik", str(exc))
            button.config(state="normal")

    button = ttk.Button(frame, text="O'RNATISH", command=install)
    button.pack(fill="x", ipady=8, pady=(8, 0))
    root.mainloop()
    return 0


def ascii_bytes(value: object) -> bytes:
    return str(value).encode("ascii", errors="replace")


def escpos_qr(data: bytes, module_size: int = 5) -> bytes:
    gs = b"\x1d"
    size = max(1, min(module_size, 16))
    length = len(data) + 3
    p_low = length % 256
    p_high = length // 256
    return b"".join(
        (
            gs + b"(k\x04\x00\x31\x41\x32\x00",
            gs + b"(k\x03\x00\x31\x43" + bytes([size]),
            gs + b"(k\x03\x00\x31\x45\x31",
            gs + b"(k" + bytes([p_low, p_high]) + b"\x31\x50\x30" + data,
            gs + b"(k\x03\x00\x31\x51\x30",
        )
    )


def build_receipt(receipt: dict) -> bytes:
    """Raster ESC/POS for explicitly configured compatible printers."""
    image = build_receipt_image(receipt).convert("L")
    image = image.point(lambda pixel: 0 if pixel < 180 else 255).convert("1")
    width_bytes = (image.width + 7) // 8
    pixels = image.tobytes()
    data = bytearray(b"\x1b@\x1ba\x01")
    # GS v 0: 1 means a black dot. PIL mode 1 uses 1 for white.
    for y in range(0, image.height, 128):
        height = min(128, image.height - y)
        data += b"\x1dv0\x00" + width_bytes.to_bytes(2, "little") + height.to_bytes(2, "little")
        data += bytes(byte ^ 255 for byte in pixels[y * width_bytes:(y + height) * width_bytes])
    data += b"\n\n\n\x1dVB\x00"
    return bytes(data)


def print_raw(receipt: dict, config: dict) -> str:
    try:
        import win32print
    except ImportError as exc:
        raise RuntimeError("pywin32 o'rnatilmagan: pip install pywin32") from exc

    printer_name = config.get("printer_name") or win32print.GetDefaultPrinter()
    if not printer_name:
        raise RuntimeError("Windows default printer topilmadi")
    payload = build_receipt(receipt)
    printer = None
    document_started = False
    page_started = False
    try:
        printer = win32print.OpenPrinter(printer_name)
        win32print.StartDocPrinter(printer, 1, ("Tarozi Chek", None, "RAW"))
        document_started = True
        win32print.StartPagePrinter(printer)
        page_started = True
        written = win32print.WritePrinter(printer, payload)
        if written != len(payload):
            raise RuntimeError(f"Printer {len(payload)} baytdan faqat {written} bayt qabul qildi")
        win32print.EndPagePrinter(printer)
        page_started = False
        win32print.EndDocPrinter(printer)
        document_started = False
        logging.info("Receipt %s sent to %s", receipt.get("receipt_no"), printer_name)
        return printer_name
    finally:
        if printer is not None:
            if page_started:
                try:
                    win32print.EndPagePrinter(printer)
                except Exception:
                    logging.exception("Could not end printer page")
            if document_started:
                try:
                    win32print.EndDocPrinter(printer)
                except Exception:
                    logging.exception("Could not end printer document")
            try:
                win32print.ClosePrinter(printer)
            except Exception:
                logging.exception("Could not close printer")


def build_receipt_image(receipt: dict):
    from receipt_render import render_receipt
    data = dict(receipt)
    data.setdefault("company", "AIRITOM LOGISTICS CENTER MCHJ")
    data.setdefault("operator", "—")
    data.setdefault("weight_fmt", "0")
    data.setdefault("weighing_fee_fmt", data.get("price_fmt", "0"))
    data.setdefault("total_fmt", data.get("price_fmt", "0"))
    data.setdefault("entry_service", False)
    data.setdefault("reload_service", False)
    data.setdefault("payment_method", "cash")
    data.setdefault("qr_text", f"Chek: {data['receipt_no']}\nAvtomobil: {data['plate_number']}\nVazni: {data['weight_fmt']} kg\nJami: {data['total_fmt']} so'm")
    return render_receipt(data)


def print_windows_driver(receipt: dict, config: dict) -> str:
    """Print through the installed Windows driver; works without ESC/POS support."""
    try:
        import win32print
        import win32ui
        from PIL import ImageWin
    except ImportError as exc:
        raise RuntimeError("pywin32 va Pillow o'rnatilmagan") from exc
    printer_name = config.get("printer_name") or win32print.GetDefaultPrinter()
    if not printer_name:
        raise RuntimeError("Windows default printer topilmadi")
    image = build_receipt_image(receipt).convert("RGB")
    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(printer_name)
        printable_width = max(1, dc.GetDeviceCaps(8))
        printable_height = max(1, dc.GetDeviceCaps(10))
        scale = min(printable_width / image.width, printable_height / image.height)
        target_width = max(1, int(image.width * scale))
        target_height = max(1, int(image.height * scale))
        left = max(0, (printable_width - target_width) // 2)
        dc.StartDoc("Tarozi Chek")
        dc.StartPage()
        ImageWin.Dib(image).draw(dc.GetHandleOutput(), (left, 0, left + target_width, target_height))
        dc.EndPage()
        dc.EndDoc()
        logging.info("Driver receipt %s sent to %s", receipt.get("receipt_no"), printer_name)
        return printer_name
    finally:
        try:
            dc.DeleteDC()
        except Exception:
            pass


def print_receipt(receipt: dict, config: dict) -> str:
    if str(config.get("print_mode", "windows")).lower() == "escpos":
        return print_raw(receipt, config)
    return print_windows_driver(receipt, config)


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "TaroziLocalAgent/2"
    config: dict = {}

    def log_message(self, fmt: str, *args) -> None:
        logging.info("HTTP %s - %s", self.address_string(), fmt % args)

    def allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin", "").rstrip("/")
        expected = str(self.config.get("allowed_origin", "")).rstrip("/")
        return origin if origin and hmac_compare(origin, expected) else None

    def send_json(self, status: int, payload: dict, origin: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        origin = self.allowed_origin()
        if not origin:
            self.send_json(403, {"success": False, "message": "Origin ruxsat etilmagan"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json(404, {"success": False, "message": "Topilmadi"})
            return
        origin = self.allowed_origin()
        try:
            import win32print
            printer_name = self.config.get("printer_name") or win32print.GetDefaultPrinter()
            handle = win32print.OpenPrinter(printer_name)
            win32print.ClosePrinter(handle)
            self.send_json(
                200,
                {"success": True, "service": "Tarozi Local Agent", "printer": printer_name, "printer_ready": True, "print_mode": self.config.get("print_mode", "windows")},
                origin,
            )
        except Exception as exc:
            self.send_json(
                503,
                {"success": False, "service": "Tarozi Local Agent", "printer_ready": False, "message": str(exc)},
                origin,
            )

    def do_POST(self) -> None:
        origin = self.allowed_origin()
        if not origin:
            self.send_json(403, {"success": False, "message": "Origin ruxsat etilmagan"})
            return
        if self.path not in {"/print", "/feed"}:
            self.send_json(404, {"success": False, "message": "Topilmadi"}, origin)
            return
        try:
            if self.path == "/feed":
                import win32print
                printer_name = self.config.get("printer_name") or win32print.GetDefaultPrinter()
                handle = win32print.OpenPrinter(printer_name)
                try:
                    win32print.StartDocPrinter(handle, 1, ("Tarozi Qog'oz Surish", None, "RAW"))
                    win32print.StartPagePrinter(handle)
                    win32print.WritePrinter(handle, b"\n\n\n\n\n")
                    win32print.EndPagePrinter(handle)
                    win32print.EndDocPrinter(handle)
                finally:
                    win32print.ClosePrinter(handle)
                self.send_json(200, {"success": True, "printer": printer_name}, origin)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 65536:
                raise ValueError("So'rov hajmi noto'g'ri")
            receipt = json.loads(self.rfile.read(length).decode("utf-8"))
            printer = print_receipt(receipt, self.config)
            self.send_json(200, {"success": True, "printer": printer}, origin)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"success": False, "message": str(exc)}, origin)
        except Exception as exc:
            logging.exception("Print failed")
            self.send_json(500, {"success": False, "message": f"Printer xatosi: {exc}"}, origin)


def hmac_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def serve(config: dict) -> None:
    AgentHandler.config = config
    address = ("127.0.0.1", int(config.get("agent_port", 17832)))
    server = ThreadingHTTPServer(address, AgentHandler)
    logging.info("Local agent listening on http://%s:%s", *address)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def quick_check_sqlite(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(16)
    if header != b"SQLite format 3\x00":
        raise RuntimeError("Serverdan kelgan fayl SQLite emas")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError("SQLite quick_check muvaffaqiyatsiz")
    finally:
        connection.close()


def download_backup(config: dict) -> Path:
    if not str(config.get("backup_token", "")).strip():
        raise RuntimeError("Avtomatik backup tokeni sozlanmagan; saytdagi qo'lda backup tokensiz ishlaydi")
    folder = Path(config["backup_folder"]).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    url = config["site_url"].rstrip("/") + "/api/backups/sqlite"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {config['backup_token']}",
            "User-Agent": "TaroziLocalAgent/2",
            "Accept": "application/vnd.sqlite3",
        },
    )
    fd, temp_name = tempfile.mkstemp(prefix=".tarozi-backup-", suffix=".part", dir=folder)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with urlopen(request, timeout=180) as response, temp_path.open("wb") as target:
            if response.status != 200:
                raise RuntimeError(f"Backup server HTTP {response.status}")
            shutil.copyfileobj(response, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        quick_check_sqlite(temp_path)
        filename = f"{datetime.now().strftime('%d.%m.%Y')} 00-00 holatiga backup.db"
        destination = folder / filename
        os.replace(temp_path, destination)
        for old_file in folder.glob("* 00-00 holatiga backup.db"):
            if old_file != destination:
                try:
                    old_file.unlink()
                except OSError:
                    logging.exception("Could not remove old backup %s", old_file)
        logging.info("Backup saved: %s", destination)
        return destination
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Tarozi Kiosk Windows local agent")
    parser.add_argument("command", nargs="?", default="install", choices=("install", "configure", "serve", "backup", "test-print"))
    args = parser.parse_args()
    setup_logging()
    try:
        if args.command == "install":
            return installer_gui()
        if args.command == "configure":
            configure()
            return 0
        config = load_config()
        if args.command == "serve":
            serve(config)
        elif args.command == "backup":
            destination = download_backup(config)
            if sys.stdout is not None:
                print(f"Backup tayyor: {destination}")
        elif args.command == "test-print":
            sample = {
                "receipt_no": "TEST/00001",
                "plate_number": "01 A 123 BA",
                "weight_fmt": "24 500",
                "weighing_fee_fmt": "30 000",
                "entry_service": True,
                "entry_fee_fmt": "30 000",
                "reload_service": False,
                "reload_fee_fmt": "0",
                "total_fmt": "60 000",
                "price_fmt": "60 000",
                "created_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            }
            printer = print_receipt(sample, config)
            if sys.stdout is not None:
                print(f"Test chek yuborildi: {printer}")
        return 0
    except Exception as exc:
        logging.exception("Agent command failed")
        if sys.stderr is not None:
            print(f"XATO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
