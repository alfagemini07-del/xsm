"""Real Jinja templates rendered with representative long content for viewport tests."""

import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

root = Path(__file__).resolve().parent.parent
pages = {}
for folder, role in (("tarozi", "operator"), ("tashqi-parkovka", "parkovka_tashqi")):
    env = Environment(
        loader=FileSystemLoader(root / folder / "templates"), autoescape=True
    )
    pages[folder] = env.get_template("index.html").render(
        role=role, csrf="test", server="https://server.example.uz"
    )
env = Environment(
    loader=FileSystemLoader(root / "server" / "templates"), autoescape=True
)
env.filters["som"] = lambda n: f"{n:,}"
env.globals["csrf_token"] = lambda: "test"
base = dict(
    user=dict(role="ayritom", username="operator_ayritom"),
    points={"ayritom": "KPP1 · Ayritom"},
    labels={"in": "Kirish", "out": "Chiqish"},
)
for mode in ("station", "confirm", "receipt", "error"):
    fields = [
        ("Chek", "AFZ-12345"),
        ("Avtomobil", "01 A 123 BA"),
        ("Free Zone kirish darvozasi", "Ayritom darvozasi"),
    ]
    fields += [
        (name, "05.11.2026 23:18" if "vaqti" in name else "30 000 so‘m")
        for name in (
            "Navbat / tashrif",
            "Maqsad",
            "Nuqta",
            "Amal",
            "Holat",
            "Kirish vaqti",
            "Chek vaqti",
            "Kun",
            "Kunlik tarif",
            "Oldin to‘langan",
            "Hozir olindi",
            "Jami to‘langan",
            "To‘lov turi",
            "Xodim",
        )
    ]
    pages["kpp-" + mode] = env.get_template("kpp.html").render(
        **base,
        mode=mode,
        q=dict(
            plate="01 A 123 BA",
            action="in",
            days=12,
            rate=30000,
            paid=30000,
            amount=330000,
        ),
        token="test",
        queue=[dict(id=i, plate="01 A 123 BA") for i in range(25)],
        recent=[],
        recent_page=1,
        recent_pages=3,
        fields=fields,
        qr="",
        message="Bu transportning parkovkadan chiqishi avval qayd etilgan.",
    )
pages["kpp-login"] = env.get_template("login.html").render(
    error="Planshet ro‘yxatga olindi. Techadmin Qurilmalar bo‘limida ruxsat bersin, so‘ng qayta kiring.",
    username="kpp1",
)
pages["kpp-select"] = env.get_template("kpp_select.html").render(
    url_for=lambda endpoint, number: f"/dastur/kpp/{number}"
)
print(json.dumps(pages))
