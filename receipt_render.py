"""One black-on-white raster for preview, browser printing and receipt PDF."""
import base64
import io
from pathlib import Path

import qrcode
import reportlab
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas


def font_path(bold=False):
    return str(Path(reportlab.__file__).parent / "fonts" / ("VeraBd.ttf" if bold else "Vera.ttf"))


def render_receipt(data):
    width, margin = 576, 20
    image = Image.new("RGB", (width, 4000), "white")
    draw = ImageDraw.Draw(image)
    normal = ImageFont.truetype(font_path(), 23)
    bold = ImageFont.truetype(font_path(True), 27)
    title = ImageFont.truetype(font_path(True), 32)
    y = 20

    def wrap(text, font, limit):
        lines, line = [], ""
        for char in str(text):
            if draw.textlength(line + char, font=font) > limit:
                lines.append(line)
                line = ""
            line += char
        return lines + [line]

    def text(value, font=normal, center=False):
        nonlocal y
        for line in wrap(value, font, width - margin * 2):
            x = (width - draw.textlength(line, font=font)) / 2 if center else margin
            draw.text((x, y), line, font=font, fill="black")
            y += font.size + 10

    def pair(label, value, font=normal):
        nonlocal y
        if draw.textlength(label + "  " + str(value), font=font) <= width - margin * 2:
            draw.text((margin, y), label, font=font, fill="black")
            draw.text((width - margin - draw.textlength(str(value), font=font), y), str(value), font=font, fill="black")
            y += font.size + 12
        else:
            text(label, font)
            text(value, font)

    def line():
        nonlocal y
        y += 8
        draw.line((margin, y, width - margin, y), fill="black", width=2)
        y += 14

    text("TAROZI CHEKI", title, True)
    text(data["company"], normal, True)
    line()
    pair("Chek:", data["receipt_no"])
    pair("Vaqt:", data["created_at"])
    pair("Operator:", data.get("operator", "—"))
    line()
    pair("Avtomobil:", data["plate_number"], bold)
    pair("Vazni:", data["weight_fmt"] + " kg", bold)
    line()
    pair("Vazn o'lchash:", data["weighing_fee_fmt"] + " so'm")
    if data["entry_service"]:
        pair("Hududga kirish:", data["entry_fee_fmt"] + " so'm")
    if data["reload_service"]:
        pair("Qayta yuklash:", data["reload_fee_fmt"] + " so'm")
    line()
    pair("JAMI:", data["total_fmt"] + " so'm", bold)
    pair("To'lov:", {"cash": "Naqd pul", "card": "Uzcard / Humo", "bank": "Hisob raqam"}.get(data["payment_method"], "—"))
    pair("Holat:", "To'langan")
    line()
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=1)
    qr.add_data(data["qr_text"])
    qr.make(fit=True)
    scale = max(1, min(5, 400 // (qr.modules_count + 8)))
    qr.box_size = scale
    bitmap = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    image.paste(bitmap, ((width - bitmap.width) // 2, y))
    y += bitmap.height + 12
    text("Xizmatingiz uchun rahmat!", normal, True)
    # Every text glyph is now a pixel, just like the QR: no printer fonts needed.
    return image.crop((0, 0, width, y + 20))


def receipt_view(data):
    image = render_receipt(data)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    data["raster_b64"] = base64.b64encode(stream.getvalue()).decode("ascii")
    data["paper_height_mm"] = round(image.height * 72 / image.width + 8, 2)
    return data


def receipt_pdf(data):
    image = render_receipt(data)
    mm = 72 / 25.4
    height = image.height * 72 / image.width
    stream = io.BytesIO()
    pdf = pdf_canvas.Canvas(stream, pagesize=(80 * mm, (height + 8) * mm))
    pdf.setTitle("Chek " + data["receipt_no"])
    pdf.drawImage(ImageReader(image), 4 * mm, 4 * mm, width=72 * mm, height=height * mm)
    pdf.showPage()
    pdf.save()
    stream.seek(0)
    return stream
