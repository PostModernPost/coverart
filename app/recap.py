"""Shareable listening recap cards (last 24 hours, week, month, year), drawn with Pillow.

recap_period() works out the time window, recap_stats() reads the listening history for it,
and render_card() draws a 1080 x 1350 PNG (the size Instagram and most apps show uncropped)."""
import calendar
import datetime as dt
import io
import os
import sqlite3

W, H = 1080, 1350
PAD = 72
PERIODS = ("day", "week", "month", "year")


# ---------------------------------------------------------------- the time window
def recap_period(period, offset=0, now=None):
    """(start_ts, end_ts, title, subtitle) in local time. offset 1 = the one before, and so on."""
    now = now or dt.datetime.now()
    offset = max(0, int(offset or 0))
    fmt_day = lambda d: f"{d:%b} {d.day}"
    if period == "day":
        end = now - dt.timedelta(days=offset)
        start = end - dt.timedelta(days=1)
        title = "Last 24 hours" if offset == 0 else f"{end:%A}"
        sub = f"{fmt_day(start)} to {fmt_day(end)}, {end.year}" if start.day != end.day else f"{fmt_day(end)}, {end.year}"
    elif period == "week":
        end = now - dt.timedelta(days=7 * offset)
        start = end - dt.timedelta(days=7)
        title = "This week" if offset == 0 else ("Last week" if offset == 1 else f"Week of {fmt_day(start)}")
        sub = f"{fmt_day(start)} to {fmt_day(end)}, {end.year}"
    elif period == "month":
        y, m = now.year, now.month - offset
        while m < 1:
            y, m = y - 1, m + 12
        start = dt.datetime(y, m, 1)
        last = dt.datetime(y, m, calendar.monthrange(y, m)[1], 23, 59, 59)
        end = min(now, last)
        title = f"{start:%B}"
        sub = f"{start:%B} {y}" + (" so far" if end < last else "")
    else:
        y = now.year - offset
        start = dt.datetime(y, 1, 1)
        last = dt.datetime(y, 12, 31, 23, 59, 59)
        end = min(now, last)
        title = str(y)
        sub = "The year so far" if end < last else "The whole year"
    return start.timestamp(), end.timestamp(), title, sub


# ---------------------------------------------------------------- the numbers
def recap_stats(db_path, start, end):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        q = lambda sql, *a: [dict(r) for r in db.execute(sql, (start, end, *a))]
        tot = q("SELECT COUNT(*) plays, COUNT(DISTINCT lower(artist)) artists, "
                "COUNT(DISTINCT lower(album)||'|'||lower(artist)) albums, "
                "COALESCE(SUM(COALESCE(duration_ms, 210000)), 0) / 60000.0 minutes, "
                "SUM(CASE WHEN source='vinyl' THEN 1 ELSE 0 END) vinyl, "
                "SUM(CASE WHEN source='cd' THEN 1 ELSE 0 END) cd, "
                "SUM(CASE WHEN source='cassette' THEN 1 ELSE 0 END) cassette FROM plays WHERE ts>=? AND ts<?")[0]
        albums = q("SELECT album, artist, COUNT(*) n, MAX(art) art FROM plays WHERE ts>=? AND ts<? "
                   "AND album IS NOT NULL AND album!='' GROUP BY lower(album), lower(artist) "
                   "ORDER BY n DESC, MAX(art) IS NULL, MAX(ts) DESC LIMIT 5")
        artists = q("SELECT artist, COUNT(*) n FROM plays WHERE ts>=? AND ts<? AND artist IS NOT NULL AND artist!='' "
                    "GROUP BY lower(artist) ORDER BY n DESC, MAX(ts) DESC LIMIT 4")
        songs = q("SELECT title, artist, COUNT(*) n FROM plays WHERE ts>=? AND ts<? "
                  "GROUP BY lower(title), lower(artist) ORDER BY n DESC, MAX(ts) DESC LIMIT 3")
        new = q("SELECT artist, COUNT(*) n FROM plays p WHERE ts>=? AND ts<? AND artist IS NOT NULL AND artist!='' "
                "AND NOT EXISTS (SELECT 1 FROM plays o WHERE lower(o.artist)=lower(p.artist) AND o.ts<?) "
                "GROUP BY lower(artist) ORDER BY n DESC", start)
        try:
            saved = db.execute("SELECT COUNT(*) FROM saved WHERE ts>=? AND ts<?", (start, end)).fetchone()[0]
        except sqlite3.OperationalError:
            saved = 0
    return {"total": tot, "albums": albums, "artists": artists, "songs": songs,
            "new_artists": new, "saved": saved}


# ---------------------------------------------------------------- drawing
def _fonts(fonts_dir):
    from PIL import ImageFont

    def f(name, size):
        for path in (os.path.join(fonts_dir, name), os.path.join(r"C:\Windows\Fonts", "segoeuib.ttf")):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default(size)
    return {
        "kicker": f("Inter-SemiBold.otf", 26), "title": f("InterDisplay-Black.otf", 112),
        "sub": f("Inter-Regular.otf", 34), "num": f("InterDisplay-Bold.otf", 88), "numlab": f("Inter-Regular.otf", 28),
        "album": f("InterDisplay-Bold.otf", 48), "artist": f("Inter-Regular.otf", 36), "small": f("Inter-Regular.otf", 30),
        "line": f("Inter-SemiBold.otf", 34), "lineb": f("Inter-Regular.otf", 34), "rank": f("InterDisplay-Bold.otf", 34),
        "brand": f("InterDisplay-Bold.otf", 30),
    }


def _fit(draw, text, font, maxw):
    text = text or ""
    if draw.textlength(text, font=font) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw, text, font, maxw, lines):
    words, out, cur = (text or "").split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= maxw:
            cur = test
        else:
            if cur:
                out.append(cur)
            cur = w
        if len(out) == lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    if len(out) == lines and " ".join(out) != " ".join(words):
        out[-1] = _fit(draw, out[-1] + " " + " ".join(words[len(" ".join(out).split()):]), font, maxw)
    return [_fit(draw, l, font, maxw) for l in out]


def _cover(img, size, radius=10):
    from PIL import Image, ImageDraw, ImageOps
    if img is None:
        tile = Image.new("RGB", (size, size), (34, 34, 38))
        d = ImageDraw.Draw(tile)                       # a little record where the cover would be
        c = size // 2
        for r, col in ((int(size * .42), (22, 22, 24)), (int(size * .3), (30, 30, 33)), (int(size * .13), (201, 160, 74))):
            d.ellipse((c - r, c - r, c + r, c + r), fill=col)
    else:
        tile = ImageOps.fit(img.convert("RGB"), (size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return tile, mask


def _base_color(img):
    """A dark tint of the top record's cover for the background."""
    if img is None:
        return (32, 28, 24)
    small = img.convert("RGB").resize((24, 24))
    px = list(small.getdata())
    # weight toward saturated pixels so the tint isn't just grey
    best = sorted(px, key=lambda p: max(p) - min(p), reverse=True)[: len(px) // 3] or px
    r = sum(p[0] for p in best) // len(best)
    g = sum(p[1] for p in best) // len(best)
    b = sum(p[2] for p in best) // len(best)
    k = 0.42
    return (int(r * k + 11 * (1 - k)), int(g * k + 11 * (1 - k)), int(b * k + 12 * (1 - k)))


def _hours(minutes):
    h = minutes / 60
    return f"{h:.1f}" if h < 10 else f"{round(h):,}"


def render_card(stats, title, subtitle, images, fonts_dir):
    """images: {art url: PIL image}. Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFilter
    F = _fonts(fonts_dir)
    albums = stats["albums"]
    top_img = images.get(albums[0]["art"]) if albums and albums[0].get("art") else None
    base = _base_color(top_img)

    card = Image.new("RGB", (W, H), (11, 11, 12))
    # background: the top cover, hugely blurred, fading into near-black
    if top_img is not None:
        bg = top_img.convert("RGB").resize((W // 8, H // 8)).filter(ImageFilter.GaussianBlur(3)).resize((W, H), Image.BICUBIC)
        bg = Image.blend(bg, Image.new("RGB", (W, H), base), 0.55)
    else:
        bg = Image.new("RGB", (W, H), base)
    fade = Image.linear_gradient("L").resize((W, H))            # dark toward the bottom
    card = Image.composite(Image.new("RGB", (W, H), (11, 11, 12)), bg, fade.point(lambda v: int(v * 0.85)))
    d = ImageDraw.Draw(card)
    FG, DIM, GOLD = (244, 241, 234), (186, 182, 175), (201, 160, 74)

    # header
    try:
        import logo
        lk = logo.draw_lockup(40, os.path.join(fonts_dir, "InterDisplay-Bold.otf"))
        card.paste(lk, (PAD, PAD - 8), lk)
    except Exception:
        d.text((PAD, PAD), "LINER NOTES", font=F["kicker"], fill=GOLD)
    d.text((PAD, PAD + 44), _fit(d, title, F["title"], W - 2 * PAD), font=F["title"], fill=FG)
    d.text((PAD, PAD + 176), subtitle, font=F["sub"], fill=DIM)

    t = stats["total"]
    if not t["plays"]:
        d.text((PAD, 560), "Nothing played yet.", font=F["album"], fill=FG)
        d.text((PAD, 640), "Play a record or put on Spotify and it lands here.", font=F["small"], fill=DIM)
        return _png(card)

    # the numbers
    nums = [(f"{t['plays']:,}", "songs"), (_hours(t["minutes"]), "hours"), (f"{t['artists']:,}", "artists")]
    kinds = [(k, t.get(k) or 0) for k in ("vinyl", "cd", "cassette")]
    kinds = [(k, n) for k, n in kinds if n]
    if kinds:
        phys = sum(n for _, n in kinds)
        names = {"vinyl": "vinyl", "cd": "CD", "cassette": "tape"}
        kinds.sort(key=lambda x: -x[1])
        label = "on " + " & ".join(names[k] for k, _ in kinds[:2]) if len(kinds) <= 2 else "on vinyl, CD & tape"
        nums.append((f"{round(100 * phys / t['plays'])}%", label))
    # lay the numbers out by their real widths; shrink them if a big count would crowd the row
    from PIL import ImageFont
    numf, size = F["num"], 88
    avail = W - 2 * PAD
    while True:
        widths = [max(d.textlength(n, font=numf), d.textlength(lab, font=F["numlab"])) for n, lab in nums]
        if sum(widths) + 56 * (len(nums) - 1) <= avail or size <= 52:
            break
        size -= 6
        try:
            numf = ImageFont.truetype(numf.path, size)
        except Exception:
            break
    gap = min(110, (avail - sum(widths)) / max(1, len(nums) - 1))
    x, y = PAD, 330
    for (n, lab), w in zip(nums, widths):
        d.text((x, y + (88 - size)), n, font=numf, fill=FG)
        d.text((x + 4, y + 104), lab, font=F["numlab"], fill=DIM)
        x += w + gap

    # top record, with the next four beside it
    y = 520
    if albums:
        a = albums[0]
        size = 350
        tile, mask = _cover(images.get(a.get("art")), size, 14)
        card.paste(tile, (PAD, y), mask)
        tx = PAD + size + 44
        tw = W - PAD - tx
        more = albums[1:5]
        gap = 16
        s_ = int((tw - 3 * gap) / 4)
        room = y + (size - s_ - 18 if more else size)          # text stays above the small covers
        d.text((tx, y), "TOP RECORD", font=F["kicker"], fill=GOLD)
        ly = y + 42
        lines = _wrap(d, a["album"], F["album"], tw, 2)
        for line in lines:
            d.text((tx, ly), line, font=F["album"], fill=FG)
            ly += 56
        if ly + 44 <= room:
            d.text((tx, ly + 2), _fit(d, a["artist"], F["artist"], tw), font=F["artist"], fill=FG)
            ly += 48
        if ly + 38 <= room:
            d.text((tx, ly), f"{a['n']} play{'s' if a['n'] != 1 else ''}", font=F["small"], fill=DIM)
        if more:
            for i, b in enumerate(more):
                t2, m2 = _cover(images.get(b.get("art")), s_, 8)
                card.paste(t2, (tx + i * (s_ + gap), y + size - s_), m2)
        y = y + size + 64

    # top artists, and artists new to you
    colx2 = W // 2 + 20
    d.text((PAD, y), "TOP ARTISTS", font=F["kicker"], fill=GOLD)
    for i, ar in enumerate(stats["artists"][:4]):
        ly = y + 46 + i * 50
        d.text((PAD, ly), str(i + 1), font=F["rank"], fill=DIM)
        d.text((PAD + 44, ly), _fit(d, ar["artist"], F["line"], colx2 - PAD - 44 - 30), font=F["line"], fill=FG)
    new = stats["new_artists"]
    right = []
    if new:
        right.append(("NEW TO YOU", f"{len(new)} artist{'s' if len(new) != 1 else ''}", [x["artist"] for x in new[:2]]))
    elif stats["songs"]:
        right.append(("TOP SONG", stats["songs"][0]["title"], [stats["songs"][0]["artist"]]))
    ry = y
    for head, big, rest in right:
        d.text((colx2, ry), head, font=F["kicker"], fill=GOLD)
        d.text((colx2, ry + 46), _fit(d, big, F["line"], W - PAD - colx2), font=F["line"], fill=FG)
        for j, r in enumerate(rest):
            d.text((colx2, ry + 96 + j * 46), _fit(d, r, F["lineb"], W - PAD - colx2), font=F["lineb"], fill=DIM)
        ry += 96 + len(rest) * 46 + 20
    if stats["saved"]:
        d.text((colx2, ry), f"{stats['saved']} song{'s' if stats['saved'] != 1 else ''} saved", font=F["lineb"], fill=DIM)
    # footer: the most-played song
    if stats["songs"] and right and right[0][0] != "TOP SONG":
        sg = stats["songs"][0]
        fy = H - PAD - 34
        d.text((PAD, fy - 46), "TOP SONG", font=F["kicker"], fill=GOLD)
        d.text((PAD, fy), _fit(d, f"{sg['title']}  ·  {sg['artist']}", F["line"], W * 0.62 - PAD), font=F["line"], fill=FG)
    mark = "made with Liner Notes"
    mx = W - PAD - d.textlength(mark, font=F["numlab"])
    d.text((mx, H - PAD - 26), mark, font=F["numlab"], fill=(150, 146, 140))
    try:
        import logo
        m = logo.draw_mark(34)
        card.paste(m, (int(mx - 46), H - PAD - 28), m)
    except Exception:
        pass
    return _png(card)


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
