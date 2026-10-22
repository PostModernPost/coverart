"""The Liner Notes mark: a record sliding out of a sleeve that has lines of notes on it.

One drawing used everywhere (app icon, tray, phone home-screen icon, recap cards); LOGO_SVG is the
same shapes for the web page. Coordinates are on a 100 x 100 grid."""
GOLD = (201, 160, 74)
INK = (20, 20, 22)
GROOVE = (58, 58, 66)
RIM = (92, 92, 102)
TILE = (38, 38, 44)
CREAM = (244, 241, 234)
BG = (11, 11, 12)

# record: centre, radius; sleeve: square; notes: three lines on the sleeve
REC = (68, 50, 31)
SLEEVE = (2, 17, 62, 83)
LINES = ((12, 36, 51), (12, 50, 45), (12, 64, 35))
LINE_W = 6.5

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<circle cx="68" cy="50" r="30" fill="#141416" stroke="#5c5c66" stroke-width="2.2"/>
<circle cx="68" cy="50" r="24" fill="none" stroke="#3a3a42" stroke-width="1.6"/>
<circle cx="68" cy="50" r="17.5" fill="none" stroke="#3a3a42" stroke-width="1.6"/>
<circle cx="68" cy="50" r="10" fill="#c9a04a"/>
<circle cx="68" cy="50" r="1.8" fill="#141416"/>
<rect x="2" y="17" width="60" height="66" rx="5" fill="#f4f1ea"/>
<g stroke="#141416" stroke-width="6.5" stroke-linecap="round">
<line x1="12" y1="36" x2="51" y2="36"/><line x1="12" y1="50" x2="45" y2="50"/><line x1="12" y1="64" x2="35" y2="64"/>
</g></svg>"""

# the same mark on a dark rounded tile, for places that need a filled square (favicon, home screen)
LOGO_TILE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="22" fill="#26262c"/>
<g transform="translate(12 12) scale(.76)">""" + LOGO_SVG.split(">", 1)[1].rsplit("</svg>", 1)[0] + "</g></svg>"


def draw_mark(size, background=None, padding=0.0, tile=False):
    """The mark as a PIL RGBA image. background: an RGB tuple, or None for transparent.
    tile=True puts it on a dark rounded square (app and home-screen icons)."""
    from PIL import Image, ImageDraw
    ss = 4                                    # draw big, then shrink, for smooth edges
    S = size * ss
    img = Image.new("RGBA", (S, S), (*background, 255) if background else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tile:
        d.rounded_rectangle((0, 0, S - 1, S - 1), int(S * 0.22), fill=(*TILE, 255))
        padding = max(padding, 0.12)
    inner = S * (1 - 2 * padding)
    off = S * padding
    u = inner / 100.0
    P = lambda x, y: (off + x * u, off + y * u)

    cx, cy, r = REC
    def disc(rad, fill=None, outline=None, width=0):
        x0, y0 = P(cx - rad, cy - rad)
        x1, y1 = P(cx + rad, cy + rad)
        d.ellipse((x0, y0, x1, y1), fill=fill, outline=outline, width=width)
    disc(r, fill=(*INK, 255), outline=(*RIM, 255), width=max(1, int(2.2 * u)))
    if size >= 32:                            # grooves only where they'd show
        disc(24, outline=(*GROOVE, 255), width=max(1, int(1.6 * u)))
        disc(17.5, outline=(*GROOVE, 255), width=max(1, int(1.6 * u)))
    disc(10, fill=(*GOLD, 255))
    disc(1.8, fill=(*INK, 255))

    x0, y0, x1, y1 = SLEEVE
    d.rounded_rectangle((*P(x0, y0), *P(x1, y1)), int(5 * u), fill=(*CREAM, 255))
    w = LINE_W * u
    for lx0, ly, lx1 in LINES:
        (ax, ay), (bx, by) = P(lx0, ly), P(lx1, ly)
        d.rounded_rectangle((ax - w / 2, ay - w / 2, bx + w / 2, by + w / 2), int(w / 2), fill=(*INK, 255))
    return img.resize((size, size), Image.LANCZOS)


def draw_lockup(height, font_path, color=CREAM):
    """Mark plus the words "Liner Notes", for the recap cards. Returns an RGBA image."""
    from PIL import Image, ImageDraw, ImageFont
    mark = draw_mark(height)
    try:
        font = ImageFont.truetype(font_path, int(height * 0.62))
    except Exception:
        font = ImageFont.load_default(int(height * 0.62))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = "Liner Notes"
    tw = int(tmp.textlength(text, font=font))
    gap = int(height * 0.28)
    out = Image.new("RGBA", (height + gap + tw + 4, height), (0, 0, 0, 0))
    out.paste(mark, (0, 0), mark)
    d = ImageDraw.Draw(out)
    bbox = d.textbbox((0, 0), text, font=font)
    d.text((height + gap, (height - (bbox[3] - bbox[1])) / 2 - bbox[1]), text, font=font, fill=color)
    return out
