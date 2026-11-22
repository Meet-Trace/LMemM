import io
import random

from PIL import Image, ImageDraw, ImageFont

W, H = 2880, 1800


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Canvas:
    def __init__(self, bg, size=(W, H), mode="RGB"):
        self.image = Image.new(mode, size, bg)
        self.draw = ImageDraw.Draw(self.image)
        self.lines = []

    def text(self, xy, text, fill, size=26, confidence=0.95):
        font = _font(size)
        self.draw.text(xy, text, fill=fill, font=font)
        x0, y0, x1, y1 = self.draw.textbbox(xy, text, font=font)
        self.lines.append({"text": text, "confidence": confidence, "box": [x0, y0, x1 - x0, y1 - y0]})

    def png(self):
        out = io.BytesIO()
        self.image.save(out, "PNG")
        return out.getvalue()

    def ocr(self, scale=1.0):
        lines = [{**l, "box": [v / scale for v in l["box"]]} for l in self.lines]
        return {"lines": lines, "image_size": [self.image.width / scale, self.image.height / scale],
                "recognition_level": "accurate", "languages": ["en-US"], "os_version": "test"}


def dense_grid():
    c = Canvas((255, 255, 255))
    c.draw.rectangle([0, 0, W, 120], fill=(240, 240, 240))
    c.draw.line([0, 120, W, 120], fill=(200, 200, 200), width=2)
    for r in range(40):
        y = 160 + r * 40
        c.draw.line([0, y, W, y], fill=(218, 218, 218), width=2)
        for k in range(12):
            x = k * 240
            if r == 0:
                c.draw.line([x, 160, x, H], fill=(218, 218, 218), width=2)
            if r < 39:
                c.text((x + 12, y + 8), f"{(r * 37 + k * 11) % 9973}.{k}", (30, 30, 30), size=22)
    return c


def code_editor():
    rng = random.Random(7)
    c = Canvas((30, 30, 30))
    c.draw.rectangle([0, 0, 480, H], fill=(37, 37, 38))
    c.draw.line([480, 0, 480, H], fill=(60, 60, 60), width=2)
    c.draw.rectangle([480, 0, W, 70], fill=(45, 45, 45))
    for i in range(30):
        c.text((40, 100 + i * 52), f"module_{i}.py", (200, 200, 200), size=24)
    colours = [(86, 156, 214), (206, 145, 120), (220, 220, 170), (156, 220, 254), (106, 153, 85)]
    for i in range(38):
        y = 100 + i * 44
        c.text((500, y), f"{i + 1:>3}", (120, 120, 120), size=24)
        x = 620 + 40 * rng.randint(0, 4)
        for _ in range(rng.randint(0, 5)):
            word = "".join(rng.choice("abcdefghij_") for _ in range(rng.randint(2, 9)))
            c.text((x, y), word, rng.choice(colours), size=24)
            x += 30 + 15 * len(word)
    return c


def dashboard():
    c = Canvas((245, 246, 250))
    c.draw.rectangle([0, 0, 360, H], fill=(28, 35, 51))
    for i in range(8):
        c.text((40, 140 + i * 90), f"Section {i}", (210, 215, 230), size=26)
    for k in range(4):
        x = 420 + k * 610
        c.draw.rounded_rectangle([x, 60, x + 570, 300], 24, fill=(255, 255, 255), outline=(225, 228, 235), width=2)
        c.text((x + 30, 90), f"Metric {k}", (90, 95, 110), size=24)
        c.text((x + 30, 150), f"{1200 + 37 * k}", (20, 25, 40), size=60)
    c.draw.rounded_rectangle([420, 360, 1800, 1100], 24, fill=(255, 255, 255), outline=(225, 228, 235), width=2)
    for b in range(14):
        h = 80 + (b * 53) % 500
        c.draw.rectangle([480 + b * 90, 1040 - h, 540 + b * 90, 1040], fill=(64, 120, 242))
    c.draw.rounded_rectangle([1860, 360, 2840, 1100], 24, fill=(255, 255, 255), outline=(225, 228, 235), width=2)
    pts = [(1900 + i * 45, 900 - ((i * 97) % 380)) for i in range(21)]
    c.draw.line(pts, fill=(240, 120, 60), width=6)
    c.draw.rounded_rectangle([420, 1160, 2840, 1760], 24, fill=(255, 255, 255), outline=(225, 228, 235), width=2)
    for s, col in enumerate([(64, 120, 242), (240, 120, 60), (60, 190, 120), (200, 80, 200)]):
        c.draw.pieslice([520, 1220, 1000, 1700], s * 90, s * 90 + 80, fill=col)
    return c


def form():
    c = Canvas((250, 250, 250))
    c.draw.rectangle([0, 0, W, 90], fill=(255, 255, 255))
    c.draw.line([0, 90, W, 90], fill=(220, 220, 220), width=2)
    for i in range(7):
        y = 200 + i * 190
        c.text((900, y), f"Field label {i}", (60, 60, 60), size=26)
        c.draw.rounded_rectangle([900, y + 45, 1980, y + 125], 12, fill=(255, 255, 255), outline=(190, 190, 190), width=3)
        if i % 2 == 0:
            c.text((925, y + 68), "placeholder", (160, 160, 160), size=24)
    for i in range(3):
        c.draw.rectangle([900, 1560 + i * 0, 940, 1600], outline=(120, 120, 120), width=3)
    c.draw.rounded_rectangle([1700, 1600, 1980, 1690], 14, fill=(40, 110, 240))
    c.text((1770, 1625), "Submit", (255, 255, 255), size=30)
    return c


def blank():
    c = Canvas((236, 236, 236))
    c.draw.ellipse([1300, 760, 1580, 1040], fill=(180, 180, 190))
    c.draw.rectangle([200, 200, 260, 260], fill=(90, 90, 90))
    return c


def unfamiliar():
    rng = random.Random(42)
    c = Canvas((18, 40, 32))
    for i in range(0, W, 8):
        c.draw.line([i, 0, i, 300], fill=(18 + i % 60, 40, 32 + (i // 8) % 80))
    for _ in range(25):
        x, y, r = rng.randint(100, W - 100), rng.randint(400, H - 100), rng.randint(20, 160)
        c.draw.ellipse([x - r, y - r, x + r, y + r], outline=(rng.randint(80, 255), rng.randint(80, 255), 90), width=5)
    for _ in range(12):
        c.draw.line([rng.randint(0, W), rng.randint(0, H), rng.randint(0, W), rng.randint(0, H)],
                    fill=(230, 230, 120), width=4)
    c.draw.polygon([(2000, 500), (2600, 900), (2100, 1500)], fill=(90, 30, 120))
    c.text((300, 1600), "Ʃ glyph 稀 ∆ 42", (240, 240, 240), size=40, confidence=0.4)
    return c


def front_app_only():
    c = Canvas((0, 0, 0, 0), mode="RGBA")
    c.draw.rectangle([600, 300, 2200, 1400], fill=(255, 255, 255, 255))
    c.draw.rectangle([600, 300, 2200, 380], fill=(235, 235, 235, 255))
    c.draw.line([600, 380, 2200, 380], fill=(200, 200, 200, 255), width=2)
    for i in range(10):
        c.text((660, 430 + i * 80), f"Visible line {i}", (20, 20, 20, 255), size=28)
    return c


LAYOUTS = {"dense_grid": dense_grid, "code_editor": code_editor, "dashboard": dashboard, "form": form,
           "blank": blank, "unfamiliar": unfamiliar}
