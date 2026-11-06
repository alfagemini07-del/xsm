"""Mirror the server source into the named repository. No credentials in source."""

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
REMOTE = "https://github.com/alfagemini07-del/xsm.git"
ROOT_FILES = {
    "app.py",
    "freezone.py",
    "devices.py",
    "reports.py",
    "receipt_render.py",
    "station_sync.py",
    "test_system.py",
    "schema.sql",
    "requirements.txt",
    "render.yaml",
    "Procfile",
    ".gitignore",
    ".env.example",
    "README.md",
    "publish_server.py",
    "GitHub-yangilash.cmd",
    "GitHub-kirish.cmd",
    "GitHub-tekshirish.cmd",
}
FOLDERS = {"templates", "static", "android", ".github"}
SKIP = {
    "build",
    ".gradle",
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "local.properties",
}
EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".jpg",
    ".jpeg",
    ".png",
    ".ico",
    ".svg",
    ".apk",
    ".xml",
    ".java",
    ".gradle",
    ".yml",
    ".yaml",
    ".webp",
    ".ttf",
    ".woff2",
}


def files():
    root_files = ROOT_FILES | {
        p.name
        for p in SOURCE.glob("*.py")
        if not p.name.startswith(("test_", "ui_fixtures"))
    }
    for filename in sorted(root_files):
        p = SOURCE / filename
        if not p.is_file() or p.is_symlink():
            raise RuntimeError(f"Server fayli topilmadi: {filename}")
        yield Path(filename)
    for name in sorted(FOLDERS):
        for parent, children, names in os.walk(SOURCE / name, followlinks=False):
            folder = Path(parent)
            children[:] = [
                d
                for d in children
                if d not in SKIP
                and not (folder / d).is_symlink()
                and not getattr(folder / d, "is_junction", lambda: False)()
            ]
            for n in names:
                p = folder / n
                if not p.is_symlink() and p.suffix.lower() in EXTENSIONS:
                    yield p.relative_to(SOURCE)


LOG_PATH = SOURCE / "github-yangilash.log"


def log(message):
    print(message, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as target:
        target.write(message + "\n")


def git(cwd, *args, **opts):
    if "stdout" in opts:
        return subprocess.run(["git", *args], cwd=cwd, check=True, **opts)
    with subprocess.Popen(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        for line in process.stdout:
            log(line.rstrip())
        code = process.wait()
        if code:
            raise subprocess.CalledProcessError(code, ["git", *args])


def mirror(repo):
    wanted = set(files())
    for required in (
        "static/apk/kpp1.apk",
        "static/apk/kpp2.apk",
        "templates/admin.html",
        "android/build.gradle",
        ".github/workflows/kpp-apk.yml",
    ):
        if Path(required) not in wanted:
            raise RuntimeError(f"To‘liq nusxa kerak: {required}")
    tracked = (
        git(repo, "ls-files", "-z", stdout=subprocess.PIPE).stdout.decode().split("\0")
    )
    for name in filter(None, tracked):
        dest = repo / name
        if dest.is_symlink() or not dest.resolve().is_relative_to(repo.resolve()):
            raise RuntimeError("Kutilmagan Git yo‘li")
        if Path(name) not in wanted:
            dest.unlink(missing_ok=True)
    for rel in sorted(wanted):
        dest = repo / rel
        if not dest.resolve().is_relative_to(repo.resolve()):
            raise RuntimeError("Noto‘g‘ri fayl yo‘li")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / rel, dest)
    git(repo, "add", "-A")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    options = sys.argv[1:]
    if options not in ([], ["--preview"], ["--check-access"], ["--login"]):
        raise RuntimeError("Parametrlar: --preview, --check-access yoki --login")
    log("\n" + datetime.now().isoformat(timespec="seconds") + " | XSM GitHub yangilash")
    if not shutil.which("git"):
        raise RuntimeError(
            "Git for Windows topilmadi. Uni o‘rnatgach faylni qayta oching."
        )
    if options == ["--login"]:
        git(
            SOURCE,
            "credential-manager",
            "github",
            "login",
            "--browser",
            "--username",
            "alfagemini07-del",
        )
        log("GitHub kirishi yakunlandi. Endi GitHub-yangilash.cmd faylini oching.")
        return
    cache = SOURCE / ".publish-cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=cache) as temp:
        repo = Path(temp) / "checkout"
        log("GitHub dagi main nusxasi olinmoqda...")
        git(
            temp,
            "-c",
            "core.longpaths=true",
            "clone",
            "--quiet",
            "--branch",
            "main",
            "--single-branch",
            REMOTE,
            str(repo),
        )
        if options != ["--preview"]:
            git(
                repo,
                "config",
                "credential.https://github.com.username",
                "alfagemini07-del",
            )
        if options == ["--check-access"]:
            git(repo, "push", "--dry-run", "origin", "HEAD:main")
            log("GitHub yozish ruxsati tekshirildi.")
            return
        if not options:
            log("alfagemini07-del hisobining yozish ruxsati tekshirilmoqda...")
            git(repo, "push", "--dry-run", "origin", "HEAD:main")
        mirror(repo)
        git(repo, "diff", "--cached", "--name-status")
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo
        ).returncode
        if result == 0:
            log("GitHub allaqachon yangilangan.")
            return
        if result != 1:
            raise RuntimeError("Git o‘zgarishlarni o‘qiy olmadi.")
        if options == ["--preview"]:
            log(
                "Ko‘rib chiqish tugadi. Yuborish uchun GitHub-yangilash.cmd faylini oching."
            )
            return
        git(
            repo,
            "-c",
            "user.name=XSM Release",
            "-c",
            "user.email=xsm-release@localhost",
            "commit",
            "-m",
            "Update XSM server from local release",
        )
        log(
            "O‘zgarishlar GitHub ga yuborilmoqda. Kirish so‘ralsa, xsm uchun yozish huquqi bor hisobni tanlang."
        )
        git(repo, "push", "origin", "HEAD:main")
        log(
            "GitHub main yangilandi. Render Auto-Deploy yoqilgan bo‘lsa, joylash boshlanadi."
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        log("Yangilash tugamadi: " + str(error))
        log("Tafsilot: " + str(LOG_PATH))
        log(
            "GitHub ruxsati rad etilsa GitHub-kirish.cmd faylini oching. Boshqa kishi main ni yangilagan bo‘lsa yangilashni qayta oching."
        )
        sys.exit(1)
