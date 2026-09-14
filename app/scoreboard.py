"""Отрисовка табло: имена, счёт по геймам, очки, кто подаёт."""
import os
from PIL import Image, ImageDraw, ImageFont

FONTS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/Library/Fonts/Arial.ttf"]

BG = (16, 18, 24, 216)
ROW_A = (32, 36, 46, 235)
ROW_B = (24, 27, 35, 235)
TEXT = (238, 240, 246, 255)
DIM = (150, 158, 175, 255)
ACCENT = (61, 220, 151, 255)      # подающий
GAMES_BG = (45, 50, 64, 255)
POINTS_BG = (77, 163, 255, 255)


def _font(size, bold=True):
    for p in FONTS:
        if os.path.exists(p) and (bold == ("Bold" in p)):
            return ImageFont.truetype(p, size)
    for p in FONTS:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render(state, setup, width=1920, height=1080, scale=None):
    """PNG-плашка со счётом для одного момента матча."""
    s = scale or width / 1920
    pad = int(18 * s)
    row_h = int(52 * s)
    name_w = int(300 * s)
    cell = int(64 * s)
    w = name_w + cell * 2 + pad * 2
    h = row_h * 2 + pad * 2 + int(26 * s)

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(12 * s)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=BG)

    f_name = _font(int(26 * s))
    f_num = _font(int(30 * s))
    f_small = _font(int(17 * s), bold=False)

    title = f"Гейм {state['game']}"
    d.text((pad, int(8 * s)), title, font=f_small, fill=DIM)

    top = pad + int(26 * s)
    rows = [
        (setup.name_a, state["games_a"], state["points_a"], state["server_a"], ROW_A),
        (setup.name_b, state["games_b"], state["points_b"], not state["server_a"], ROW_B),
    ]
    for i, (name, games, points, serving, bg) in enumerate(rows):
        y = top + i * row_h
        d.rounded_rectangle([pad, y, pad + name_w + cell * 2, y + row_h - int(4 * s)],
                            radius=int(6 * s), fill=bg)
        # метка подачи
        dot_x = pad + int(14 * s)
        cy = y + (row_h - int(4 * s)) // 2
        if serving:
            rr = int(6 * s)
            d.ellipse([dot_x - rr, cy - rr, dot_x + rr, cy + rr], fill=ACCENT)
        nm = name if len(name) <= 16 else name[:15] + "…"
        d.text((dot_x + int(16 * s), cy), nm, font=f_name, fill=TEXT, anchor="lm")

        gx = pad + name_w
        d.rounded_rectangle([gx, y, gx + cell - int(4 * s), y + row_h - int(4 * s)],
                            radius=int(6 * s), fill=GAMES_BG)
        d.text((gx + (cell - int(4 * s)) // 2, cy), str(games), font=f_num, fill=TEXT, anchor="mm")

        px = gx + cell
        d.rounded_rectangle([px, y, px + cell - int(4 * s), y + row_h - int(4 * s)],
                            radius=int(6 * s), fill=POINTS_BG)
        d.text((px + (cell - int(4 * s)) // 2, cy), str(points), font=f_num, fill=TEXT, anchor="mm")

    return img


def save(state, setup, path, width=1920):
    render(state, setup, width=width).save(path)
    return path
