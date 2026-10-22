"""
Liner Notes: shows the album art and story of whatever is playing.

Shows the album art of whatever is playing:
  1. Anything playing on this PC (Spotify app, Spotify web, etc.) via Windows media info.
  2. Otherwise, whatever the microphone hears (your turntable), identified with Shazam.

Run:   python now_playing.py
Then open http://localhost:8765 on the small screen (start.bat does this for you).

Other options:
  python now_playing.py --list-devices   show microphones / inputs and their numbers
  python now_playing.py --demo           cycle fake tracks so you can see the screen
"""

import argparse
import asyncio
import collections
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
import wave

from aiohttp import web, ClientSession, ClientTimeout

APP_NAME = "Liner Notes"
# Bundled files (display.html) live next to this file, or in PyInstaller's unpack folder when installed.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# Settings and the log live in the user's AppData, since Program Files is read-only.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)

def _move_old_data(new_dir):
    """Cover Art was renamed Liner Notes: carry its history and settings over, once."""
    old_dir = os.path.join(os.path.dirname(new_dir), "Cover Art")
    if os.path.isdir(old_dir) and not os.path.exists(new_dir):
        try:
            import shutil
            shutil.move(old_dir, new_dir)
        except Exception:
            pass


_move_old_data(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------- settings
# Override any of these in config.json in DATA_DIR (same keys). The tray menu edits it for you.
CFG = {
    "port": 8765,
    "allow_other_devices": False,  # True = phones/tablets on your Wi-Fi can open the display
    "audio_device": None,          # None = Windows default mic, "phone" = the phone's mic, or part of a mic's name
    "silence_threshold": 0.003,    # below this loudness counts as silence / no music
    "gap_ratio": 0.3,              # this much quieter than the surrounding music counts as a gap between songs
    "gap_seconds": 1.0,            # a gap this long triggers a fresh identify
    "probe_every_seconds": 20,     # standby: how often to take a short sample
    "probe_seconds": 2,            # standby: length of that sample
    "clip_seconds": 8,             # length of audio sent to Shazam
    "retry_seconds": 15,           # when nothing is identified yet, try this often
    "recheck_seconds": 45,         # while a record is playing, re-check this often (catches gapless track changes)
    "idle_after_seconds": 20,      # silence this long = turntable stopped
    "poll_seconds": 1.0,           # how often to check Spotify / PC media
    "ignore_apps": [],             # e.g. ["msedge", "chrome"] to ignore browser videos
    "phone_follows_pc": True,      # phone screen turns off when this PC sleeps, back on when it wakes
    "discogs_token": "",           # optional personal Discogs token (the app has its own key when built)
    "donate_url": "https://buymeacoffee.com/goodisanoun",  # shown in the info panel ("" hides it)
}
cfg_path = os.path.join(DATA_DIR, "config.json")
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        CFG.update(json.load(f))

# Windows media info (Spotify etc.). Optional so the file still runs elsewhere.
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import Buffer, InputStreamOptions, DataReader
    HAVE_WINRT = True
except Exception:
    HAVE_WINRT = False


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def make_id(artist, title):
    return hashlib.sha1(f"{norm(artist)}|{norm(title)}".encode()).hexdigest()[:12]


def search_links(artist, title, album=None, shazam_url=None):
    q = urllib.parse.quote(f"{artist} {title}")
    qa = urllib.parse.quote(artist or "")
    links = [
        {"name": "Spotify", "url": f"https://open.spotify.com/search/{q}"},
        {"name": "YouTube", "url": f"https://www.youtube.com/results?search_query={q}"},
        {"name": "Bandcamp", "url": f"https://bandcamp.com/search?q={qa}"},
        {"name": "Discogs", "url": f"https://www.discogs.com/search/?q={urllib.parse.quote(f'{artist} {album or title}')}&type=all"},
        {"name": "Genius", "url": f"https://genius.com/search?q={q}"},
        {"name": "Last.fm", "url": f"https://www.last.fm/music/{urllib.parse.quote((artist or '').replace(' ', '+'))}"},
    ]
    if shazam_url:
        links.insert(0, {"name": "Shazam", "url": shazam_url})
    return links


# ---------------------------------------------------------------- shared state
class State:
    def __init__(self):
        self.track = None          # what the screen shows
        self.version = 0
        self.db = None             # History, set by build_app
        self.last_logged = None
        self.art = {}              # id -> (bytes, mime) for thumbnails from Windows
        self.history = {}          # id -> track, so info lookups work after a change
        self.info_cache = {}

    def _bump(self, track):
        if track and track.get("label"):
            track = {**track, "label": tidy_label(track["label"])}
        self.track = track
        self.version += 1
        if track and track.get("id"):
            self.history[track["id"]] = track
        if track and track.get("status") == "playing" and track.get("id") != self.last_logged and self.db:
            self.last_logged = track["id"]
            try:
                self.db.add(track)
            except Exception as e:
                log("history write failed:", e)
        if track:
            log(f"[{track['source']}] {track.get('status')}: {track.get('artist')} - {track.get('title')}")
        else:
            log("idle")

    def show(self, track):
        t = self.track
        if t and t.get("id") == track.get("id") and t.get("status") == track.get("status") and t.get("source") == track.get("source"):
            return
        self._bump(track)

    def idle(self):
        if self.track is not None:
            self._bump(None)

    def enrich(self, tid, fields):
        """Fill in details looked up after the song went on screen (year, label, length, BPM...)."""
        if not fields:
            return
        if fields.get("label"):
            fields = {**fields, "label": tidy_label(fields["label"])}
        if tid in self.history:
            self.history[tid].update(fields)
        if self.track and self.track.get("id") == tid:
            self.track = {**self.track, **fields}
            self.version += 1


# ---------------------------------------------------------------- lookups
ITUNES = "https://itunes.apple.com"
_itunes_cache = {}


LABEL_CUTS = (r",?\s+a division of\b", r",?\s+a (?:[\w&.' -]+ )?company\b", r",?\s+an? [\w&.' -]+ (?:label|imprint)\b",
              r"\s+under (?:exclusive )?licen[cs]e\b",
              r"\.?,?\s+(?:distributed|marketed|manufactured)(?:,? (?:and |& )?(?:distributed|marketed|manufactured))* (?:by|for)\b",
              r"\s+for the (?:world|united states|us|uk)\b", r"\s+in association with\b", r"\s+/\s+", r";\s")
LABEL_SUFFIX = r"(?:,?\s+(?:LLC|L\.L\.C\.|Ltd\.?|Limited|Inc\.?|Incorporated|Corp\.?|GmbH|S\.A\.|B\.V\.|Pty\.? Ltd\.?|plc))+\.?$"
LABEL_NAMES = {"atlantic recording corporation": "Atlantic Records", "umg recordings": "UMG Recordings",
               "warner records": "Warner Records", "sony music entertainment": "Sony Music"}


def tidy_label(label):
    """'Island Records, a division of Universal Music Operations Limited' -> 'Island Records'"""
    if not label:
        return label
    s = str(label).strip()
    s = re.sub(r"^.*?[℗©]\s*(?:\d{4}\s*)?", "", s) or s          # leftover copyright marks and year
    s = re.sub(r"^\(?[PC]\)\s*(?:\d{4}\s*)?", "", s)
    s = re.sub(r"^(?:\d{4}(?:\s*[-–,/&]\s*|\s+|$))+", "", s).strip() or s   # "1974, 1976, 1988 Sony" -> "Sony"
    for cut in LABEL_CUTS:
        s = re.split(cut, s, maxsplit=1, flags=re.I)[0]
    s = re.sub(r"\b(Inc|Ltd|LLC|Corp|Limited)\.\s+\S.*$", r"\1", s)     # "Warner Records Inc. Anything else" -> "Warner Records Inc"
    s = re.sub(r"\s*\((?:[^)]*(?:music|records|recordings|entertainment|group|ltd|inc|llc)[^)]*)\)$", "", s, flags=re.I)
    s = re.sub(LABEL_SUFFIX, "", s, flags=re.I).strip(" ,.-")
    if s.isupper() and len(s) > 4:                                          # "SONY BMG MUSIC" -> "Sony BMG Music"
        small = {"of", "and", "the", "a", "an", "for", "de", "la"}
        words = s.split()
        s = " ".join(w if len(w) <= 3 and w.isalpha() and w.lower() not in small else
                     (w.lower() if i and w.lower() in small else w.capitalize()) for i, w in enumerate(words))
    return LABEL_NAMES.get(s.lower(), s) or str(label).strip()


def clean_label(copyright_text):
    """'℗ 2019 Dead Oceans' -> 'Dead Oceans'"""
    if not copyright_text:
        return None
    c = re.sub(r"^.*?[℗©]\s*(\d{4})?\s*", "", copyright_text).strip()
    return tidy_label(c) or None


def itunes_fields(res):
    """Turn an iTunes song result into our track fields."""
    art = res.get("artworkUrl100")
    return {
        "title": res.get("trackName"), "artist": res.get("artistName"), "album": res.get("collectionName"),
        "year": (res.get("releaseDate") or "")[:4] or None, "genre": res.get("primaryGenreName"),
        "art": art.replace("100x100bb", "1000x1000bb") if art else None,
        "thumb": art.replace("100x100bb", "300x300bb") if art else None,
        "duration_ms": res.get("trackTimeMillis"), "collection_id": res.get("collectionId"),
        "track_id": res.get("trackId"),
    }


async def itunes_label(http, collection_id):
    if not collection_id:
        return None
    key = ("label", collection_id)
    if key not in _itunes_cache:
        try:
            async with http.get(f"{ITUNES}/lookup?id={collection_id}") as r:
                data = await r.json(content_type=None)
            res = (data.get("results") or [{}])[0]
            _itunes_cache[key] = clean_label(res.get("copyright"))
        except Exception as e:
            log("label lookup failed:", e)
            return None
    return _itunes_cache[key]


async def itunes_lookup(http, artist, title):
    """Best iTunes catalog match for this artist + title (artist must match): art, album, year, genre, label."""
    key = (norm(artist), norm(title))
    if key in _itunes_cache:
        return _itunes_cache[key]
    info = None
    try:
        q = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
        async with http.get(f"{ITUNES}/search?" + q) as r:
            data = await r.json(content_type=None)
        best = None
        for res in data.get("results", []):
            a, b = norm(artist), norm(res.get("artistName"))
            if not (a and b and (a in b or b in a)):
                continue
            if norm(res.get("trackName")) == norm(title):
                best = res
                break
            best = best or res
        if best:
            info = itunes_fields(best)
            info["label"] = await itunes_label(http, info["collection_id"])
    except Exception as e:
        log("itunes lookup failed:", e)
        return None
    _itunes_cache[key] = info
    return info


async def itunes_art(http, artist, title):
    info = await itunes_lookup(http, artist, title)
    return (info["art"], info["album"]) if info else (None, None)


async def itunes_search(http, query, limit=15):
    q = urllib.parse.urlencode({"term": query, "entity": "song", "limit": limit})
    async with http.get(f"{ITUNES}/search?" + q) as r:
        data = await r.json(content_type=None)
    return [itunes_fields(res) for res in data.get("results", []) if res.get("trackId")]


async def itunes_track(http, track_id):
    async with http.get(f"{ITUNES}/lookup?id={int(track_id)}") as r:
        data = await r.json(content_type=None)
    res = (data.get("results") or [None])[0]
    if not res:
        return None
    info = itunes_fields(res)
    info["label"] = await itunes_label(http, info["collection_id"])
    return info


def parse_lrc(text):
    """'[01:23.45] words' lines -> [[83.45, 'words'], ...] (timed lyrics from LRCLIB)."""
    out = []
    for line in (text or "").splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        words = re.sub(r"\[[^\]]*\]", "", line).strip()
        for m, sec in stamps:
            out.append([int(m) * 60 + float(sec), words])
    out.sort(key=lambda x: x[0])
    return out


# Words streaming services tack onto titles that lyric sites don't use
TITLE_EXTRAS = re.compile(
    r"\s*(?:[-–]\s*(?:\d{4}\s+)?(?:re-?master(?:ed)?|remix|mix|live|mono|stereo|single|radio|edit|version|acoustic|demo|bonus|deluxe|from|feat|with)\b.*$"
    r"|[\(\[](?:[^\)\]]*\b(?:re-?master(?:ed)?|feat\.?|ft\.|with|live|mono|stereo|version|edit|explicit|clean|bonus|deluxe|anniversary)\b[^\)\]]*)[\)\]])",
    re.I)


def clean_title(title):
    """'Lido Shuffle - 2007 Remaster' -> 'Lido Shuffle'; 'Song (feat. X) [Live]' -> 'Song'"""
    t = title or ""
    for _ in range(3):
        t2 = TITLE_EXTRAS.sub("", t).strip()
        if t2 == t or not t2:
            break
        t = t2
    return t


def main_artist(artist):
    """'Khruangbin & Leon Bridges' -> 'Khruangbin'. Only used as a last try, since bands like Simon & Garfunkel exist."""
    return re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.|\bx\b|\band\b|\bwith\b)\s*", artist or "", maxsplit=1, flags=re.I)[0].strip()


async def _lrclib(http, params):
    async with http.get("https://lrclib.net/api/search?" + urllib.parse.urlencode(params),
                        headers={"User-Agent": "LinerNotes/1.0 +https://github.com/PostModernPost/coverart"}) as r:
        return (await r.json(content_type=None)) or []


def _pick_lrclib(results, duration_s):
    if duration_s:      # versions of the same song (live, edits) differ in length; take the closest
        results = sorted(results, key=lambda x: abs((x.get("duration") or 0) - duration_s))
    for res in results:
        if res.get("syncedLyrics"):
            return res.get("plainLyrics") or "", parse_lrc(res["syncedLyrics"])
    for res in results:
        if res.get("plainLyrics"):
            return res["plainLyrics"], None
        if res.get("instrumental"):
            return "(Instrumental)", None
    return None


async def fetch_lyrics(http, artist, title, album, duration_s=None):
    """(plain text, timed lines or None). LRCLIB first (it has timed lyrics), trying the exact title, then
    the title without "Remaster"/"Live"/"feat." extras, then a looser search; lyrics.ovh as a last resort."""
    title, artist = (title or "").strip(), (artist or "").strip()
    if not title or not artist:
        return None, None
    short, lead = clean_title(title), main_artist(artist)
    tries = [{"artist_name": artist, "track_name": title}]
    if short.lower() != title.lower():
        tries.append({"artist_name": artist, "track_name": short})
    if lead.lower() != artist.lower():
        tries.append({"artist_name": lead, "track_name": short})
    tries.append({"q": f"{lead} {short}"})
    seen = set()
    for params in tries:
        key = tuple(sorted(params.items()))
        if key in seen:
            continue
        seen.add(key)
        try:
            results = await _lrclib(http, params)
        except Exception as e:
            log("lyrics lookup failed:", e)
            break
        if "q" in params:       # a loose search can return other songs; keep the ones that match
            results = [x for x in results if norm(lead) in norm(x.get("artistName")) and
                       norm(clean_title(x.get("trackName"))) == norm(short)]
        got = _pick_lrclib(results, duration_s)
        if got:
            return got
    # lyrics.ovh: plain lyrics only, from other lyric sites
    for a in dict.fromkeys((artist, lead)):
        try:
            url = "https://api.lyrics.ovh/v1/" + urllib.parse.quote(a, safe="") + "/" + urllib.parse.quote(short, safe="")
            async with http.get(url, timeout=ClientTimeout(total=6)) as r:
                if r.status != 200:
                    continue
                d = await r.json(content_type=None)
            text = (d or {}).get("lyrics") or ""
            text = re.sub(r"^Paroles de la chanson .*? par .*?\r?\n", "", text).replace("\r\n", "\n").strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) > 20:
                return text, None
        except Exception as e:
            log("lyrics.ovh lookup failed:", e)
    return None, None


MUSIC_WORDS = ("band", "musician", "singer", "rapper", "group", "duo", "songwriter", "music",
               "dj", "producer", "composer", "guitarist", "drummer", "pianist", "artist", "project")


async def fetch_bio(http, artist):
    if not artist:
        return None
    for title in (f"{artist} (band)", f"{artist} (musician)", f"{artist} (singer)", f"{artist} (rapper)", artist):
        try:
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"))
            async with http.get(url, headers={"User-Agent": "LinerNotes/1.0"}) as r:
                if r.status != 200:
                    continue
                d = await r.json(content_type=None)
            if d.get("type") != "standard":
                continue
            blurb = (d.get("description", "") + " " + d.get("extract", "")[:300]).lower()
            if not any(w in blurb for w in MUSIC_WORDS):
                continue
            return {
                "text": d.get("extract"),
                "url": d.get("content_urls", {}).get("desktop", {}).get("page"),
                "image": (d.get("thumbnail") or {}).get("source"),
            }
        except Exception as e:
            log("bio lookup failed:", e)
    return None


# ---------------------------------------------------------------- PC media (Spotify etc.)
async def read_thumbnail(props):
    ref = props.thumbnail
    if ref is None:
        return None
    stream = await ref.open_read_async()
    size = int(stream.size)
    if size <= 0:
        return None
    buf = Buffer(size)
    buf = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
    try:
        data = bytes(memoryview(buf))
    except Exception:
        reader = DataReader.from_buffer(buf)
        arr = bytearray(buf.length)
        reader.read_bytes(arr)
        data = bytes(arr)
    return data, (stream.content_type or "image/png")


async def current_pc_media(mgr):
    best = None
    for s in mgr.get_sessions():
        try:
            app_id = s.source_app_user_model_id or ""
            if any(x.lower() in app_id.lower() for x in CFG["ignore_apps"]):
                continue
            pb = s.get_playback_info()
            if pb is None or pb.playback_status != PlaybackStatus.PLAYING:
                continue
            score = 2 if "spotify" in app_id.lower() else 1
            if best is None or score > best[0]:
                best = (score, s, app_id)
        except Exception:
            continue
    if not best:
        return None
    _, s, app_id = best
    props = await s.try_get_media_properties_async()
    if not props or not props.title:
        return None
    return props, app_id, s


def timeline_position(session):
    """Spotify & co. tell Windows exactly where in the song they are."""
    tl = session.get_timeline_properties()

    def secs(v):
        if v is None:
            return None
        if hasattr(v, "total_seconds"):
            return v.total_seconds()
        try:
            return int(v) / 1e7            # 100-nanosecond ticks
        except Exception:
            return None
    pos, end = secs(tl.position), secs(tl.end_time)
    upd = tl.last_updated_time
    if pos is not None and upd is not None and hasattr(upd, "timestamp"):
        try:
            pos += max(0.0, time.time() - upd.timestamp())   # reported as of its last update
        except Exception:
            pass
    if end is not None and end <= 0:
        end = None
    return pos, end


def app_label(app_id):
    a = app_id.lower()
    for key, name in (("spotify", "Spotify"), ("msedge", "Edge"), ("chrome", "Chrome"), ("firefox", "Firefox"),
                      ("applemusic", "Apple Music"), ("zune", "Media Player"), ("vlc", "VLC")):
        if key in a:
            return name
    return app_id.split("!")[-1].split("\\")[-1].replace(".exe", "") or "PC"


async def pc_media_loop(app):
    st: State = app["state"]
    http = app["http"]
    app["pc_playing"] = False
    if not HAVE_WINRT:
        log("Windows media info not available (winrt packages missing) - Spotify detection off")
        return
    mgr = await MediaManager.request_async()
    stopped_since = None
    while True:
        try:
            res = await current_pc_media(mgr)
        except Exception as e:
            log("media read error:", e)
            res = None
        if res:
            stopped_since = None
            app["pc_playing"] = True
            props, app_id, session = res
            artist = props.artist or props.album_artist or ""
            title = props.title
            tid = make_id(artist, title)
            cur = st.track
            if not (cur and cur.get("id") == tid and cur.get("source") == "pc"):
                album = props.album_title or None
                art = None
                try:
                    thumb = await read_thumbnail(props)
                    if thumb:
                        st.art[tid] = thumb
                        art = f"/api/art/{tid}"
                except Exception as e:
                    log("thumbnail read failed:", e)
                info = await itunes_lookup(http, artist, title) or {}
                album = album or info.get("album")
                show_and_enrich(app, {
                    "id": tid, "title": title, "artist": artist, "album": album,
                    "art": info.get("art") or art, "source": "pc", "app": app_label(app_id), "status": "playing",
                    "year": info.get("year"), "label": info.get("label"), "genre": info.get("genre"),
                    "duration_ms": info.get("duration_ms"),
                    "links": search_links(artist, title, album),
                })
            try:
                pos, end = timeline_position(session)
                if pos is not None:
                    set_progress(app, tid, pos, end, "exact")
            except Exception:
                pass
        else:
            app["pc_playing"] = False
            if st.track and st.track.get("source") == "pc":
                stopped_since = stopped_since or time.time()
                if time.time() - stopped_since > 4:   # brief gaps happen when skipping tracks
                    st.idle()
        await asyncio.sleep(CFG["poll_seconds"])


# ---------------------------------------------------------------- song position
def set_progress(app, tid, pos_s, dur_s=None, kind="exact", at=None):
    """Where in the song we are: pos_s seconds at time `at`. kind: exact (Spotify, Shazam) or estimate."""
    app["progress"] = {"id": tid, "pos_s": pos_s, "at": at or time.time(), "dur_s": dur_s, "kind": kind}


def progress_now(app):
    t = app["state"].track
    if not t or t.get("status") != "playing":
        return None
    dur = (t.get("duration_ms") or 0) / 1000 or None
    p = app.get("progress")
    if p and p["id"] == t["id"]:
        dur = p.get("dur_s") or dur
        pos = None if p["pos_s"] is None else p["pos_s"] + (time.time() - p["at"]) * (1 if p.get("playing", True) else 0)
        if pos is not None and dur:
            pos = max(0, min(pos, dur))
        return {"pos": pos, "dur": dur, "kind": p["kind"]}
    return {"pos": None, "dur": dur, "kind": None} if dur else None


def record_progress(app, rec):
    """Record mode without a Shazam match: count from when the song started."""
    if rec and rec.idx is not None:
        t = rec.tracks[rec.idx]
        set_progress(app, make_id(rec.track()["artist"], t["title"]), 0, t["duration"], "estimate", at=rec.track_started)


# ---------------------------------------------------------------- more song details (Deezer) and tempo
DEEZER = "https://api.deezer.com"


async def deezer_lookup(http, artist, title):
    """Deezer's free catalog: BPM, length, release year, label, genre."""
    key = ("dz", norm(artist), norm(title))
    if key in _itunes_cache:
        return _itunes_cache[key]
    info = None
    try:
        q = f'artist:"{artist}" track:"{title}"'
        async with http.get(f"{DEEZER}/search", params={"q": q, "limit": 8}) as r:
            data = await r.json(content_type=None)
        best = None
        for d in data.get("data") or []:
            a, b = norm(artist), norm((d.get("artist") or {}).get("name"))
            if a and b and (a in b or b in a):
                if norm(d.get("title")) == norm(title) or norm(d.get("title_short")) == norm(title):
                    best = d
                    break
                best = best or d
        if best:
            async with http.get(f"{DEEZER}/track/{best['id']}") as r:
                tr = await r.json(content_type=None)
            info = {"bpm": round(tr["bpm"]) if tr.get("bpm") else None,
                    "duration_ms": (tr.get("duration") or 0) * 1000 or None,
                    "year": (tr.get("release_date") or "")[:4] or None,
                    "album": (tr.get("album") or {}).get("title"), "label": None, "genre": None}
            album_id = (tr.get("album") or {}).get("id")
            if album_id:
                async with http.get(f"{DEEZER}/album/{album_id}") as r:
                    al = await r.json(content_type=None)
                info["label"] = al.get("label")
                info["genre"] = ((al.get("genres") or {}).get("data") or [{}])[0].get("name")
                info["year"] = info["year"] if info["year"] and info["year"] != "0000" else (al.get("release_date") or "")[:4] or None
    except Exception as e:
        log("deezer lookup failed:", e)
        return None
    _itunes_cache[key] = info
    return info


def estimate_bpm(samples, sr):
    """Tempo from the sound itself: how often loudness jumps repeat (60 to 180 BPM)."""
    np = __import__("numpy")
    hop = max(1, int(sr / 100))                          # 100 loudness readings per second
    frames = len(samples) // hop
    if frames < 600:                                     # need at least 6 seconds
        return None
    x = samples[:frames * hop].reshape(frames, hop)
    env = np.log1p(np.sqrt((x * x).mean(axis=1)) * 1000)
    onset = np.maximum(0, np.diff(env))
    onset = onset - onset.mean()
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lo, hi = int(6000 / 180), int(6000 / 60)
    if hi >= len(ac) or ac[0] <= 0:
        return None
    lags = np.arange(lo, hi + 1)
    seg = ac[lo:hi + 1] / ac[0]
    weight = np.exp(-0.5 * (np.log2((6000 / lags) / 120) / 0.7) ** 2)    # prefer ~120 to avoid half/double
    i = int(np.argmax(seg * weight))
    if seg[i] < 0.1:                                     # no steady beat
        return None
    # refine with the neighbours for a non-integer lag
    if 0 < i < len(seg) - 1:
        a, b, c = seg[i - 1], seg[i], seg[i + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0
    else:
        d = 0
    return round(6000 / (lags[i] + d))


# MusicBrainz knows the label each release came out on, as a plain name ("Warner Bros. Records"),
# so it's asked first. Streaming copyright lines describe today's reissue ("Warner Records Inc.
# Manufactured and Marketed by Rhino...").
MB = "https://musicbrainz.org/ws/2"
MB_HEADERS = {"User-Agent": "LinerNotes/0.8 ( https://github.com/PostModernPost/coverart )", "Accept": "application/json"}
_mb_lock = None
_mb_last = 0.0


async def _mb_get(http, path, params):
    global _mb_lock, _mb_last
    if _mb_lock is None:
        _mb_lock = asyncio.Lock()
    async with _mb_lock:                       # MusicBrainz allows about one request a second
        wait = _mb_last + 1.1 - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _mb_last = time.time()
        async with http.get(MB + path, params={**params, "fmt": "json"}, headers=MB_HEADERS) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)


async def musicbrainz_label(http, artist, title, album=None):
    """The label of the song's original release (the album it's on when we know it)."""
    key = ("mb-label", norm(artist), norm(title), norm(album))
    if key in _itunes_cache:
        return _itunes_cache[key]
    label = None
    try:
        q = lambda x: (x or "").replace("\\", " ").replace('"', " ")
        d = await _mb_get(http, "/recording", {"query": f'recording:"{q(clean_title(title))}" AND artist:"{q(main_artist(artist))}"',
                                                "limit": "25"})
        releases = []
        for rec in (d or {}).get("recordings") or []:
            credit = " ".join(a.get("name", "") for a in rec.get("artist-credit") or [] if isinstance(a, dict))
            if norm(main_artist(artist)) not in norm(credit):
                continue
            for rel in rec.get("releases") or []:
                if rel.get("status", "Official") != "Official":
                    continue
                rg = rel.get("release-group") or {}
                releases.append((rel, rg))
        if releases:
            same_album = [x for x in releases if album and norm(clean_title(x[0].get("title"))) == norm(clean_title(album))]
            pool = same_album or [x for x in releases if (x[1].get("primary-type") in ("Album", "Single", "EP"))
                                  and "Compilation" not in (x[1].get("secondary-types") or [])] or releases
            pool.sort(key=lambda x: x[0].get("date") or "9999")
            for rel, _ in pool[:3]:              # the earliest releases; skip any without a label
                full = await _mb_get(http, f"/release/{rel['id']}", {"inc": "labels"})
                names = [((li or {}).get("label") or {}).get("name") for li in (full or {}).get("label-info") or []]
                names = [n for n in names if n and n.lower() not in ("[no label]", "not on label")]
                if names:
                    label = names[0]
                    break
    except Exception as e:
        log("musicbrainz lookup failed:", e)
        return None                              # don't cache: try again next time
    _itunes_cache[key] = label
    return label


async def enrich_track(app, t):
    """Look up whatever the source didn't give us, then update the screen."""
    try:
        if not t or t.get("status") != "playing" or not t.get("artist"):
            return
        http = app["http"]
        fields = {}
        want = ("year", "genre", "duration_ms", "album")
        it = await itunes_lookup(http, t["artist"], t["title"]) or {}      # cached, usually already looked up
        fields.update({k: it.get(k) for k in want if not t.get(k) and it.get(k)})
        # The label: keep one that came with the song (Shazam, Discogs). One that only came from the
        # iTunes copyright line gets replaced by MusicBrainz's, or Deezer's, when they have one.
        itunes_lab = tidy_label(it.get("label")) if it.get("label") else None
        need_label = not t.get("label") or tidy_label(t["label"]) == itunes_lab
        merged = {**t, **fields}
        dz = {}
        if not merged.get("bpm") or need_label or any(not merged.get(k) for k in ("year", "genre", "duration_ms")):
            dz = await deezer_lookup(http, t["artist"], t["title"]) or {}
            fields.update({k: dz.get(k) for k in ("bpm", "year", "genre", "duration_ms", "album")
                           if not merged.get(k) and dz.get(k)})
        if need_label:
            better = await musicbrainz_label(http, t["artist"], t["title"], merged.get("album")) or dz.get("label")
            if better or not t.get("label"):
                fields["label"] = better or itunes_lab
        if not t.get("bpm") and not fields.get("bpm") and t.get("source") in ("turntable", "record"):
            mic = app.get("mic")
            if mic is not None:
                samples = mic.buf.last_samples(10)
                if samples is not None:
                    bpm = await asyncio.get_running_loop().run_in_executor(None, estimate_bpm, samples, mic.sr)
                    if bpm:
                        fields["bpm"], fields["bpm_estimated"] = bpm, True
        app["state"].enrich(t["id"], {k: v for k, v in fields.items() if v})
    except Exception as e:
        log("details lookup failed:", repr(e))


def show_and_enrich(app, track):
    st = app["state"]
    same = st.track and st.track.get("id") == track.get("id") and st.track.get("source") == track.get("source")
    st.show(track)
    if not same:
        asyncio.get_running_loop().create_task(enrich_track(app, track))


def show_record(app, rec, track):
    """A song from the record's tracklist: show it, and count its position from when it started."""
    show_and_enrich(app, track)
    record_progress(app, rec)


# ---------------------------------------------------------------- record mode (Discogs)
#
# You tell Liner Notes which record is on the turntable. It loads the tracklist from Discogs, then
# follows along: a gap between songs moves to the next track, Shazam matches correct the position,
# and songs Shazam doesn't know still get their real title and artist from the tracklist.

DISCOGS = "https://api.discogs.com"
try:
    from _secrets import DISCOGS_KEY, DISCOGS_SECRET          # written by the build from GitHub secrets
except Exception:
    DISCOGS_KEY = DISCOGS_SECRET = ""


def discogs_headers():
    h = {"User-Agent": "LinerNotes/1.0 +https://github.com/PostModernPost/coverart"}
    token = (CFG.get("discogs_token") or "").strip()
    if token:
        h["Authorization"] = f"Discogs token={token}"
    elif DISCOGS_KEY and DISCOGS_SECRET:
        h["Authorization"] = f"Discogs key={DISCOGS_KEY}, secret={DISCOGS_SECRET}"
    return h


def discogs_ready():
    return "Authorization" in discogs_headers()


async def discogs_get(http, path, params=None):
    async with http.get(DISCOGS + path, params=params or {}, headers=discogs_headers()) as r:
        if r.status == 401:
            raise PermissionError("Discogs didn't accept the key")
        if r.status == 429:
            raise RuntimeError("Discogs is busy, try again in a minute")
        r.raise_for_status()
        return await r.json(content_type=None)


async def discogs_search(http, q="", barcode=""):
    params = {"type": "release", "per_page": 20}
    if barcode:
        params["barcode"] = barcode
    else:
        params["q"] = q
    data = await discogs_get(http, "/database/search", params)
    out = []
    for r in data.get("results", []):
        out.append({"id": r.get("id"), "title": r.get("title"), "year": r.get("year"),
                    "format": ", ".join(r.get("format") or []), "label": ", ".join((r.get("label") or [])[:2]),
                    "catno": r.get("catno"), "thumb": r.get("thumb") or r.get("cover_image"),
                    "country": r.get("country")})
    return out


def clean_artist(name):
    return re.sub(r"\s*\(\d+\)$", "", name or "").strip()      # Discogs adds "(2)" to tell artists apart


def join_artists(artists):
    out = ""
    for a in artists or []:
        out += clean_artist(a.get("anv") or a.get("name"))
        j = (a.get("join") or "").strip()
        if j == ",":
            out += ", "
        elif j:
            out += f" {j} "
    return out.strip(" ,")


def parse_duration(d):
    try:
        parts = [int(x) for x in str(d).split(":")]
        return parts[0] * 60 + parts[1] if len(parts) == 2 else (parts[0] * 3600 + parts[1] * 60 + parts[2])
    except Exception:
        return None


async def discogs_find_record(http, first, second):
    """Two songs heard back to back: find the record that has both, in that order.
    Same artist -> probably their album. Different artists -> a compilation."""
    same_artist = norm(first["artist"]) == norm(second["artist"])
    base = {"type": "release", "per_page": 100}
    if same_artist:
        base["artist"] = first["artist"]
    else:
        base["format"] = "Compilation"
    ra = await discogs_get(http, "/database/search", {**base, "track": first["title"]})
    rb = await discogs_get(http, "/database/search", {**base, "track": second["title"]})
    in_b = {r.get("id") for r in rb.get("results", [])}
    both = [r for r in ra.get("results", []) if r.get("id") in in_b]
    both.sort(key=lambda r: 0 if "Vinyl" in (r.get("format") or []) else 1)   # it's a turntable
    for r in both[:4]:
        rec = RecordSession(await discogs_get(http, f"/releases/{r['id']}"))
        ia, ib = rec.find(first["title"], first["artist"]), rec.find(second["title"], second["artist"])
        if ia is not None and ib is not None and ib > ia:
            return rec, ib
    return None, None


async def discogs_find_album_start(http, title, artist, album):
    """The first song after the needle went down opens a side of its own album (A1, B1...):
    you're probably playing that album. Returns (record, position) or (None, None)."""
    if not album:
        return None, None
    base = {"type": "release", "artist": artist, "release_title": album, "per_page": 10}
    results = (await discogs_get(http, "/database/search", {**base, "format": "Vinyl"})).get("results", [])
    if not results:
        results = (await discogs_get(http, "/database/search", base)).get("results", [])
    for r in results[:3]:
        rec = RecordSession(await discogs_get(http, f"/releases/{r['id']}"))
        i = rec.find(title, artist)
        if i is not None and (i == 0 or rec.tracks[i - 1]["side"] != rec.tracks[i]["side"]):
            return rec, i
    return None, None


class RecordSession:
    def __init__(self, rel):
        self.id = rel.get("id")
        self.title = rel.get("title")
        self.artist = join_artists(rel.get("artists"))
        self.various = self.artist.lower() in ("various", "various artists", "")
        self.year = str(rel.get("year") or "") or None
        labels = rel.get("labels") or [{}]
        self.label = clean_artist(labels[0].get("name")) or None
        self.catno = labels[0].get("catno")
        self.genre = (rel.get("styles") or rel.get("genres") or [None])[0]
        imgs = rel.get("images") or []
        prim = next((i for i in imgs if i.get("type") == "primary"), imgs[0] if imgs else {})
        self.cover = prim.get("uri") or prim.get("uri150")
        self.tracks = []
        for t in rel.get("tracklist") or []:
            if t.get("type_", "track") != "track":
                continue                                   # side headings, index tracks
            pos = (t.get("position") or "").strip()
            m = re.match(r"^([A-Za-z]+)", pos)
            side = m.group(1).upper() if m else (pos.split("-")[0] if "-" in pos else "1")
            self.tracks.append({"pos": pos, "side": side, "title": t.get("title"),
                                "artist": join_artists(t.get("artists")) or self.artist,
                                "duration": parse_duration(t.get("duration"))})
        self.idx = None
        self.side_done = False
        self.track_started = 0.0
        self.last_active = time.time()
        self.auto = False          # True when Liner Notes worked out the record itself

    # ---- positions
    def last_on_side(self, i):
        return i >= len(self.tracks) - 1 or self.tracks[i + 1]["side"] != self.tracks[i]["side"]

    def next_side_start(self, i):
        side = self.tracks[i]["side"]
        for j in range(i + 1, len(self.tracks)):
            if self.tracks[j]["side"] != side:
                return j
        return None

    def side_info(self, i):
        side = self.tracks[i]["side"]
        on_side = [k for k, t in enumerate(self.tracks) if t["side"] == side]
        return side, on_side.index(i) + 1, len(on_side)

    def set_index(self, i):
        self.idx = max(0, min(int(i), len(self.tracks) - 1))
        self.side_done = False
        self.track_started = time.time()
        self.last_active = time.time()
        return self.track()

    # ---- events from the listening loop
    def on_music_start(self):
        """Music started after a silence (needle down, or a flipped record)."""
        if self.idx is None:
            return self.set_index(0)
        if self.side_done:
            j = self.next_side_start(self.idx)
            return self.set_index(j if j is not None else 0)
        self.last_active = time.time()
        return self.track()

    def on_stop(self):
        """The room went quiet for a while."""
        if self.idx is not None and self.last_on_side(self.idx):
            self.side_done = True

    def gap_means_next(self, now):
        """Is this gap a real song change? (Quiet passages inside a song can look like gaps.)"""
        if self.idx is None or self.last_on_side(self.idx):
            return False
        dur = self.tracks[self.idx]["duration"] or 0
        return now - self.track_started >= max(30, 0.5 * dur)

    def overdue(self, now):
        """Songs that run straight into each other have no gap: use the listed length instead."""
        if self.idx is None or self.last_on_side(self.idx):
            return False
        dur = self.tracks[self.idx]["duration"]
        return bool(dur) and now - self.track_started > dur + 10

    def advance(self):
        return self.set_index(self.idx + 1)

    def find(self, title, artist):
        nt, na = norm(title), norm(artist)
        best = None
        for i, t in enumerate(self.tracks):
            tt, ta = norm(t["title"]), norm(t["artist"])
            if not tt:
                continue
            if tt == nt or (len(nt) > 3 and (nt in tt or tt in nt)):
                if not na or not ta or na in ta or ta in na:
                    return i
                best = best if best is not None else i
        return best

    # ---- what goes on screen
    def track(self):
        t = self.tracks[self.idx]
        side, n, count = self.side_info(self.idx)
        artist = t["artist"] if (self.various or t["artist"]) else self.artist
        return {"id": make_id(artist, t["title"]), "title": t["title"], "artist": artist, "album": self.title,
                "art": self.cover, "source": "record", "app": f"Side {side} · {n} of {count}", "status": "playing",
                "year": self.year, "label": self.label, "genre": self.genre,
                "duration_ms": (t["duration"] or 0) * 1000 or None, "record_pos": t["pos"],
                "links": search_links(artist, t["title"], self.title)}

    def summary(self):
        nxt = None
        if self.side_done and self.idx is not None:
            j = self.next_side_start(self.idx)
            nxt = self.tracks[j]["side"] if j is not None else None
        return {"id": self.id, "title": self.title, "artist": self.artist, "year": self.year, "label": self.label,
                "catno": self.catno, "cover": self.cover, "tracks": self.tracks, "idx": self.idx,
                "side_done": self.side_done, "done_side": self.tracks[self.idx]["side"] if self.side_done else None,
                "next_side": nxt, "auto": self.auto}


# ---------------------------------------------------------------- turntable (mic + Shazam)
#
# The mic is NOT on all the time. In standby it takes a short sample every
# `probe_every_seconds`. Only when that sample sounds like music does it listen
# continuously (and identify songs), until the room has been quiet for
# `idle_after_seconds`, then it goes back to standby.

NOISE = {"floor": 0.0}      # how loud the room is with no music, learned from standby samples


def music_cutoff():
    """Loudness that counts as music: the configured minimum, or well above this room's usual noise."""
    return max(CFG["silence_threshold"], NOISE["floor"] * 2.0)


def learn_room_noise(level):
    NOISE["floor"] = level if NOISE["floor"] == 0 else NOISE["floor"] * 0.8 + level * 0.2


class AudioBuffer:
    """The last ~14 seconds of mic audio, plus loudness bookkeeping."""

    BLOCK = 0.02          # seconds per loudness measurement (short, so gaps between key clicks show)

    def __init__(self, sr):
        import numpy as np
        self.np = np
        self.sr = int(sr)
        self.keep = int(14 * self.sr)
        self.chunks = collections.deque()
        self.samples = 0
        self.lock = threading.Lock()
        self.recent = collections.deque(maxlen=int(8 / self.BLOCK))   # loudness over the last 8 s
        self.short = collections.deque(maxlen=int(2 / self.BLOCK))    # loudness over the last 2 s
        self.reset()

    def reset(self):
        with self.lock:
            self.chunks.clear()
            self.samples = 0
        self.recent.clear()
        self.short.clear()
        self.last_music = 0.0     # last moment the sound was sustained like music (typing isn't)
        self.quiet_for = 0.0
        self.sound_for = 0.0
        self.gap_seen = False
        self.in_gap = False
        self.gap_count = 0        # every gap between songs, for record mode
        self.last_sound = 0.0
        self.level = 0.0

    def feed(self, data):
        """data: float32 mono samples in -1..1"""
        np = self.np
        if data is None or len(data) == 0:
            return
        with self.lock:
            self.chunks.append(data)
            self.samples += len(data)
            while self.chunks and self.samples - len(self.chunks[0]) >= self.keep:
                self.samples -= len(self.chunks.popleft())
        step = max(1, int(self.sr * self.BLOCK))
        for i in range(0, len(data), step):
            b = data[i:i + step]
            self._block(float(np.sqrt(np.mean(b * b))), len(b) / self.sr)

    def _block(self, rms, dur):
        self.level = rms
        typical = float(self.np.median(self.recent)) if len(self.recent) >= 100 else None
        self.recent.append(rms)
        self.short.append(rms)
        thr = music_cutoff()
        # Music keeps the room loud nearly all the time; typing, knocks and voices come in bursts.
        if len(self.short) >= 25 and sorted(self.short)[len(self.short) // 5] >= thr:
            self.last_music = time.time()
        # A gap between songs = clearly quieter than the music around it (room noise never reads zero)
        quiet = rms < thr or (typical is not None and rms < typical * CFG["gap_ratio"])
        if quiet:
            self.quiet_for += dur
            if self.quiet_for >= CFG["gap_seconds"]:
                self.gap_seen = True
                self.sound_for = 0.0
                if not self.in_gap:
                    self.in_gap = True
                    self.gap_count += 1
        else:
            self.quiet_for = 0.0
            self.in_gap = False
            self.sound_for += dur
        if rms >= thr:
            self.last_sound = time.time()

    def last_samples(self, seconds):
        np = self.np
        with self.lock:
            if not self.chunks:
                return None
            return np.concatenate(list(self.chunks))[-int(seconds * self.sr):]

    def clip_wav(self, seconds):
        np = self.np
        with self.lock:
            if not self.chunks:
                return None
            data = np.concatenate(list(self.chunks))[-int(seconds * self.sr):]
        return to_wav(data, self.sr)

    def stats(self):
        r = sorted(self.recent)
        if not r:
            return None
        return r[len(r) // 10], r[len(r) // 2], r[-1]


def to_wav(data, sr):
    np = __import__("numpy")
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    data = data * min(0.9 / (peak or 1.0), 20.0)   # boost quiet mic recordings
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


def music_level(data, sr):
    """How loud a sample stays: the level 80% of it is above. Music stays loud; clicks, taps and
    typing are short spikes with quiet between them, so they score low."""
    np = __import__("numpy")
    step = max(1, int(sr * 0.02))
    blocks = [data[i:i + step] for i in range(0, len(data) - step + 1, step)]
    if not blocks:
        return 0.0
    return float(np.percentile([np.sqrt(np.mean(b * b)) for b in blocks], 20))


class PcMic:
    kind = "pc"

    def __init__(self, device):
        import sounddevice as sd
        self.sd = sd
        self.device = device
        info = sd.query_devices(device, "input")
        self.sr = int(info["default_samplerate"])
        self.name = info["name"]
        self.buf = AudioBuffer(self.sr)
        self.stream = None

    def probe(self, seconds):
        """Blocking: record a short sample with the mic opened just for that."""
        data = self.sd.rec(int(seconds * self.sr), samplerate=self.sr, channels=1, dtype="float32",
                           device=self.device, blocking=True)
        return data[:, 0].copy()

    def start(self):
        if self.stream is None:
            self.stream = self.sd.InputStream(
                device=self.device, channels=1, samplerate=self.sr, blocksize=1024, dtype="float32",
                callback=lambda indata, frames, t, status: self.buf.feed(indata[:, 0].copy()))
            self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


class PhoneMic:
    """The phone's display page records and uploads audio; it follows the mode the server publishes."""
    kind = "phone"
    sr = 16000
    name = "phone microphone (via the display page)"

    def __init__(self):
        self.buf = AudioBuffer(self.sr)
        self.probes = collections.deque(maxlen=4)
        self.last_upload = 0.0

    def start(self):
        pass      # the phone starts streaming when it sees mode "active"

    def stop(self):
        pass


def pick_device(dev):
    if dev is None or isinstance(dev, int):
        return dev
    if isinstance(dev, str) and dev.isdigit():
        return int(dev)
    import sounddevice as sd
    devs = list(enumerate(sd.query_devices()))
    for i, d in devs:                      # exact name first (what the tray menu saves)
        if d["max_input_channels"] > 0 and d["name"].lower() == dev.lower():
            return i
    for i, d in devs:
        if d["max_input_channels"] > 0 and dev.lower() in d["name"].lower():
            return i
    log(f"No input device matching '{dev}', using default")
    return None


SHAZAM_REPLY = {}


async def shazam_identify(shz, wav_bytes):
    path = os.path.join(tempfile.gettempdir(), "linernotes_clip.wav")
    with open(path, "wb") as f:
        f.write(wav_bytes)
    fn = getattr(shz, "recognize", None) or shz.recognize_song
    out = await fn(path)
    out = out if isinstance(out, dict) else {}
    tr = out.get("track")
    # Remember what Shazam actually said, so "no match" can be told apart from "slow down"
    SHAZAM_REPLY["keys"] = sorted(out.keys())
    SHAZAM_REPLY["matches"] = len(out.get("matches") or [])
    SHAZAM_REPLY["retryms"] = out.get("retryms")
    if not tr:
        return None
    meta = {}
    for sec in tr.get("sections", []) or []:
        for m in sec.get("metadata", []) or []:
            if m.get("title") and m.get("text"):
                meta[m["title"]] = m["text"]
    imgs = tr.get("images") or {}
    return {
        "title": tr.get("title"), "artist": tr.get("subtitle"), "album": meta.get("Album"),
        "label": meta.get("Label"), "year": (meta.get("Released") or "")[:4] or None,
        "genre": (tr.get("genres") or {}).get("primary"),
        "art": imgs.get("coverarthq") or imgs.get("coverart"), "shazam_url": tr.get("url"),
        "offset": ((out.get("matches") or [{}])[0] or {}).get("offset"),   # where in the song the clip began
    }


UNKNOWN = {"id": "unknown", "title": "Couldn't identify this one", "artist": "", "album": None, "art": None,
           "source": "turntable", "app": "Turntable", "status": "unknown", "links": []}


async def turntable_loop(app):
    app["listen_mode"] = "off"
    try:
        if str(CFG["audio_device"]).lower() == "phone":
            mic = PhoneMic()
        else:
            mic = PcMic(pick_device(CFG["audio_device"]))
    except Exception as e:
        log("Microphone not available - turntable detection off:", e)
        return
    app["mic"] = mic
    log(f"Microphone: {mic.name}. Standby: {CFG['probe_seconds']}s sample every {CFG['probe_every_seconds']}s.")
    try:
        from shazamio import Shazam
        shz = Shazam()
    except Exception as e:
        log("Song recognition unavailable (shazamio failed to load):", repr(e))
        return
    try:
        await _turntable_inner(app, mic, shz)
    except Exception:
        import traceback
        log("turntable loop crashed:\n" + traceback.format_exc())
    finally:
        mic.stop()


def set_mode(app, mic, mode):
    if app.get("listen_mode") == mode:
        return
    app["listen_mode"] = mode
    if mode == "active":
        mic.start()
    else:
        mic.stop()
    log({"active": "music detected, listening", "standby": "mic on standby",
         "off": "PC is playing, mic off"}[mode])


async def _turntable_inner(app, mic, shz):
    st: State = app["state"]
    loop = asyncio.get_running_loop()
    L = mic.buf
    last_try = 0.0
    last_probe = 0.0
    last_level_log = 0.0
    misses = 0
    not_before = 0.0          # Shazam asked us to wait, or we're backing off after misses
    rec_gaps = 0              # gaps already used to move along the tracklist
    active_since = 0.0
    heard = []                # recent songs Shazam recognized on the turntable (no record loaded)
    needle_drop = False       # music just started after a silence: the next song recognized opens the side

    async def check_album_start(song):
        """First song after the needle drop opens a side of its album: load that album."""
        try:
            album = song.get("album") or ((await itunes_lookup(app["http"], song["artist"], song["title"])) or {}).get("album")
            found, idx = await discogs_find_album_start(app["http"], song["title"], song["artist"], album)
        except Exception as e:
            log("album lookup failed:", repr(e))
            return
        if not found or app.get("record"):
            return
        found.auto = True
        app["record"] = found
        log(f"needle drop on {found.tracks[idx]['pos']}: playing {found.artist} - {found.title}")
        cur = st.track
        if cur and cur.get("source") == "turntable" and norm(cur.get("title")) == norm(song["title"]):
            show_record(app, found, found.set_index(idx))
        else:
            found.set_index(idx)

    async def work_out_record(first, second):
        """Runs in the background so listening isn't held up by Discogs."""
        try:
            found, idx = await discogs_find_record(app["http"], first, second)
        except Exception as e:
            log("record lookup failed:", repr(e))
            return
        if not found or app.get("record"):
            if not found:
                log(f"no single record has both '{first['title']}' and '{second['title']}'")
            return
        found.auto = True
        app["record"] = found
        log(f"worked out the record: {found.artist} - {found.title} (now {found.tracks[idx]['pos']})")
        cur = st.track
        if cur and cur.get("source") == "turntable" and norm(cur.get("title")) == norm(second["title"]):
            show_record(app, found, found.set_index(idx))
        else:
            found.set_index(idx)
    set_mode(app, mic, "standby")

    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        t = st.track
        rec = app.get("record")            # a record you told Liner Notes about (record mode), or None
        # "mine" = what the mic side put on screen: an identified song, a picked one, or one from the tracklist
        on_tt = bool(t and t.get("source") in ("turntable", "manual", "record"))
        picked = bool(t and t.get("source") == "manual")

        # Spotify etc. on the PC wins; no need for the mic at all
        if app.get("pc_playing"):
            set_mode(app, mic, "off")
            continue

        if rec and app["listen_mode"] != "active" and now - rec.last_active > 3 * 3600:
            app["record"] = rec = None       # forget a record that hasn't played in hours

        mode = app["listen_mode"]
        if mode == "off":
            set_mode(app, mic, "standby")
            last_probe = 0.0          # check right away once the PC stops
            mode = "standby"

        # ---------------- standby: short sample every N seconds
        if mode == "standby":
            if on_tt:
                st.idle()
            samples = []
            if mic.kind == "pc":
                if now - last_probe >= CFG["probe_every_seconds"]:
                    last_probe = now
                    try:
                        samples.append(await loop.run_in_executor(None, mic.probe, CFG["probe_seconds"]))
                    except Exception as e:
                        log("mic sample failed:", e)
            else:
                while mic.probes:
                    samples.append(mic.probes.popleft())
            for data in samples:
                level = music_level(data, mic.sr)
                if level < music_cutoff():
                    learn_room_noise(level)
                    continue
                if level >= music_cutoff():
                    L.reset()
                    L.feed(data)
                    last_try = 0.0
                    misses = 0
                    last_level_log = now
                    log(f"sample level {level:.4f} (music cutoff {music_cutoff():.4f})")
                    set_mode(app, mic, "active")
                    active_since = now
                    needle_drop = True
                    if rec and rec.tracks:
                        rec_gaps = L.gap_count
                        show_record(app, rec, rec.on_music_start())
                    break
            continue

        # ---------------- active: continuous listening while there's music
        if mic.kind == "phone":
            while mic.probes:                 # a sample that crossed with the mode change
                L.feed(mic.probes.popleft())

        if now - last_level_log >= 15:
            last_level_log = now
            s = L.stats()
            if s:
                log(f"mic level: quiet {s[0]:.4f}, typical {s[1]:.4f}, loud {s[2]:.4f} "
                    f"(music cutoff {music_cutoff():.4f}, room {NOISE['floor']:.4f})")

        if now - max(L.last_music, active_since) > CFG["idle_after_seconds"]:
            if rec:
                rec.on_stop()
                if rec.side_done:
                    log(f"side {rec.tracks[rec.idx]['side']} finished")
            if on_tt:
                st.idle()
            set_mode(app, mic, "standby")
            last_probe = now
            continue

        # Record mode: follow the tracklist between Shazam checks
        if rec and rec.tracks and rec.idx is not None:
            if L.gap_count != rec_gaps and L.sound_for >= 1.0:
                rec_gaps = L.gap_count
                if rec.gap_means_next(now):
                    show_record(app, rec, rec.advance())
                    log(f"gap: next track {rec.tracks[rec.idx]['pos']}")
            elif rec.overdue(now):
                show_record(app, rec, rec.advance())
                log(f"track length passed: next track {rec.tracks[rec.idx]['pos']}")

        if L.sound_for < CFG["clip_seconds"]:
            continue          # wait for a full clip of music since the start or the last gap

        identified = on_tt and t.get("status") == "playing"
        # After misses, wait longer each time (15 s, 30 s, 60 s, then every 90 s) so an unknown song
        # doesn't hammer Shazam; a gap (new song) resets this.
        retry_wait = min(CFG["retry_seconds"] * (2 ** max(0, misses - 1)), 90)
        due = (L.gap_seen or app.pop("force_check", False)
               or (not identified and now - last_try >= retry_wait)
               or (misses > 0 and now - last_try >= retry_wait)
               or (now - last_try >= CFG["recheck_seconds"]))
        app["next_try"] = max(last_try + (CFG["recheck_seconds"] if identified and not misses else retry_wait),
                              not_before)
        if not due or now < not_before:
            continue
        gap = L.gap_seen
        L.gap_seen = False
        if gap:
            misses = 0
        last_try = now
        wav = L.clip_wav(CFG["clip_seconds"])
        if not wav:
            continue
        log(f"identifying ({'new song' if gap else 'check'}, level {L.level:.4f})")
        try:
            res = await shazam_identify(shz, wav)
        except Exception as e:
            log("shazam error:", repr(e))
            res = None

        on_record = rec.find(res["title"], res["artist"]) if (rec and res and res.get("title")) else None
        if rec and rec.auto and res and res.get("title") and on_record is None:
            log(f"'{res['title']}' isn't on {rec.title}: dropping that guess")
            app["record"] = rec = None
        if on_record is not None:
            misses = 0
            if rec.idx != on_record:
                log(f"Shazam says track {rec.tracks[on_record]['pos']}: moving there")
                show_record(app, rec, rec.set_index(on_record))
            if res.get("offset") is not None:
                set_progress(app, rec.track()["id"], res["offset"] + CFG["clip_seconds"],
                             rec.tracks[rec.idx]["duration"], "exact", at=last_try)
            continue
        if res and res.get("title"):
            misses = 0
            tid = make_id(res["artist"], res["title"])
            # First song after the needle drop: if it opens a side of its album, that's the record
            if not rec and discogs_ready() and needle_drop and now - active_since < 240:
                asyncio.create_task(check_album_start(res))
            needle_drop = False
            # Two different songs in a row with no record chosen: work out which record this is
            if not rec and discogs_ready() and (not heard or norm(heard[-1]["title"]) != norm(res["title"])):
                if heard and now - heard[-1]["at"] < 20 * 60:
                    asyncio.create_task(work_out_record(heard[-1], {"title": res["title"], "artist": res["artist"]}))
                heard = [{"title": res["title"], "artist": res["artist"], "at": now}]
            if not (t and t.get("id") == tid):
                info = await itunes_lookup(app["http"], res["artist"], res["title"]) or {}
                album = res["album"] or info.get("album")
                show_and_enrich(app, {
                    "id": tid, "title": res["title"], "artist": res["artist"], "album": album,
                    "art": res["art"] or info.get("art"), "source": "turntable", "app": "Turntable",
                    "status": "playing", "year": res["year"] or info.get("year"),
                    "label": res["label"] or info.get("label"), "genre": res["genre"] or info.get("genre"),
                    "duration_ms": info.get("duration_ms"),
                    "links": search_links(res["artist"], res["title"], album, res["shazam_url"]),
                })
            if res.get("offset") is not None:
                cur = st.track or {}
                set_progress(app, tid, res["offset"] + CFG["clip_seconds"],
                             (cur.get("duration_ms") or 0) / 1000 or None, "exact", at=last_try)
        else:
            misses += 1
            log(f"no match (Shazam reply: {SHAZAM_REPLY})")
            if SHAZAM_REPLY.get("retryms"):
                not_before = now + min(float(SHAZAM_REPLY["retryms"]) / 1000.0, 600)
                log(f"Shazam asked to wait {SHAZAM_REPLY['retryms'] / 1000:.0f} s")
            # The song changed to one Shazam doesn't know (common on Vinyl Moon). Don't keep showing
            # the previous song: right after a gap, one miss is enough; otherwise two in a row.
            # A song the user picked by search stays until the next gap.
            # In record mode the tracklist already says what's playing, so keep showing that.
            if (gap or (misses >= 2 and not picked)) and not (rec and rec.idx is not None):
                st.show(UNKNOWN)


# ---------------------------------------------------------------- listening history
class History:
    """Every song shown, stored on this PC (DATA_DIR/history.db)."""

    def __init__(self, path):
        self.path = path
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, title TEXT, artist TEXT, album TEXT,
                year TEXT, label TEXT, genre TEXT, source TEXT, art TEXT, duration_ms INTEGER)""")
            db.execute("CREATE INDEX IF NOT EXISTS plays_ts ON plays(ts)")
            db.execute("""CREATE TABLE IF NOT EXISTS saved (
                tid TEXT PRIMARY KEY, ts REAL NOT NULL, title TEXT, artist TEXT, album TEXT, year TEXT,
                label TEXT, genre TEXT, art TEXT, source TEXT, spotify_uri TEXT)""")
        self.saved_ids = self._saved_ids()

    def _saved_ids(self):
        with self._db() as db:
            return {r[0] for r in db.execute("SELECT tid FROM saved")}

    def save(self, tid, t):
        art = t.get("art")
        if art and art.startswith("/api/art/"):
            art = None
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO saved (tid,ts,title,artist,album,year,label,genre,art,source) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (tid, time.time(), t.get("title"), t.get("artist"), t.get("album"), t.get("year"),
                        t.get("label"), t.get("genre"), art,
                        "vinyl" if t.get("source") in ("turntable", "manual", "record") else (t.get("app") or "PC")))
        self.saved_ids.add(tid)

    def set_spotify_uri(self, tid, uri):
        with self._db() as db:
            db.execute("UPDATE saved SET spotify_uri=? WHERE tid=?", (uri, tid))

    def unsave(self, tid):
        with self._db() as db:
            row = db.execute("SELECT spotify_uri FROM saved WHERE tid=?", (tid,)).fetchone()
            db.execute("DELETE FROM saved WHERE tid=?", (tid,))
        self.saved_ids.discard(tid)
        return row[0] if row else None

    def saved_list(self):
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM saved ORDER BY ts DESC")]

    def _db(self):
        return sqlite3.connect(self.path)

    def add(self, t):
        art = t.get("art")
        if art and art.startswith("/api/art/"):
            art = None                  # Windows thumbnails aren't kept, so don't store a dead link
        with self._db() as db:
            db.execute("INSERT INTO plays (ts,title,artist,album,year,label,genre,source,art,duration_ms) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), t.get("title"), t.get("artist"), t.get("album"), t.get("year"),
                        t.get("label"), t.get("genre"), "vinyl" if t.get("source") in ("turntable", "manual", "record")
                        else (t.get("app") or "PC"), art, t.get("duration_ms")))

    def stats(self, days):
        since = time.time() - days * 86400 if days else 0
        with self._db() as db:
            db.row_factory = sqlite3.Row
            q = lambda sql, *a: [dict(r) for r in db.execute(sql, (since, *a))]
            total = q("SELECT COUNT(*) plays, COUNT(DISTINCT lower(artist)) artists, "
                      "COUNT(DISTINCT lower(artist)||'|'||lower(title)) songs, "
                      "COALESCE(SUM(COALESCE(duration_ms, 210000)),0)/60000 minutes FROM plays WHERE ts>=?")[0]
            by_source = q("SELECT source, COUNT(*) n FROM plays WHERE ts>=? GROUP BY source ORDER BY n DESC")
            artists = q("SELECT artist, COUNT(*) n, MAX(art) art FROM plays WHERE ts>=? AND artist!='' "
                        "GROUP BY lower(artist) ORDER BY n DESC LIMIT 10")
            albums = q("SELECT album, artist, COUNT(*) n, MAX(art) art, MAX(year) year FROM plays "
                       "WHERE ts>=? AND album IS NOT NULL AND album!='' "
                       "GROUP BY lower(album), lower(artist) ORDER BY n DESC LIMIT 12")
            songs = q("SELECT title, artist, COUNT(*) n, MAX(art) art FROM plays WHERE ts>=? "
                      "GROUP BY lower(title), lower(artist) ORDER BY n DESC LIMIT 10")
            genres = q("SELECT genre, COUNT(*) n FROM plays WHERE ts>=? AND genre IS NOT NULL "
                       "GROUP BY genre ORDER BY n DESC LIMIT 6")
            span = days or 30
            per_day = q("SELECT date(ts,'unixepoch','localtime') day, COUNT(*) n FROM plays "
                        "WHERE ts>=? GROUP BY day ORDER BY day")
            recent = q("SELECT ts, title, artist, album, year, source, art FROM plays WHERE ts>=? "
                       "ORDER BY ts DESC LIMIT 40")
        return {"days": days, "total": total, "by_source": by_source, "artists": artists, "albums": albums,
                "songs": songs, "genres": genres, "per_day": per_day[-span:], "recent": recent}


# ---------------------------------------------------------------- demo mode
DEMO = [
    ("Khruangbin", "Maria También", "Con Todo El Mundo", "pc", "Spotify"),
    ("Big Thief", "Not", "Two Hands", "turntable", "Turntable"),
    ("Alvvays", "Archie, Marry Me", "Alvvays", "pc", "Spotify"),
]


async def demo_loop(app):
    st: State = app["state"]
    i = 0
    while True:
        artist, title, album, source, label = DEMO[i % len(DEMO)]
        art, _ = await itunes_art(app["http"], artist, title)
        st.show({"id": make_id(artist, title), "title": title, "artist": artist, "album": album,
                 "art": art, "source": source, "app": label, "status": "playing",
                 "links": search_links(artist, title, album)})
        i += 1
        await asyncio.sleep(12)


# ---------------------------------------------------------------- web server
async def index(request):
    # Never let a phone or browser keep an old copy of the page after an update
    return web.FileResponse(os.path.join(HERE, "display.html"),
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


MANIFEST = {"id": "/", "scope": "/", "name": APP_NAME, "short_name": APP_NAME, "start_url": "/", "display": "fullscreen",
            "orientation": "any", "background_color": "#0b0b0c", "theme_color": "#0b0b0c",
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#0b0b0c"/>'
        '<circle cx="50" cy="50" r="40" fill="#1b1b1b"/><circle cx="50" cy="50" r="28" fill="#121212"/>'
        '<circle cx="50" cy="50" r="12" fill="#c9a04a"/></svg>')


async def manifest(request):
    return web.json_response(MANIFEST, content_type="application/manifest+json")


async def icon(request):
    return web.Response(text=ICON, content_type="image/svg+xml")


async def api_search(request):
    q = (request.query.get("q") or "").strip()
    if not q:
        return web.json_response({"results": []})
    try:
        results = await itunes_search(request.app["http"], q)
    except Exception as e:
        log("search failed:", e)
        return web.json_response({"results": [], "error": "Search isn't reachable right now."})
    return web.json_response({"results": results})


async def api_pick(request):
    """The user picked a song by search: show it until the next song starts."""
    body = await request.json()
    info = await itunes_track(request.app["http"], body.get("track_id"))
    if not info:
        return web.json_response({"ok": False}, status=404)
    show_and_enrich(request.app, {
        "id": make_id(info["artist"], info["title"]), "title": info["title"], "artist": info["artist"],
        "album": info["album"], "art": info["art"], "source": "manual", "app": "Picked by you",
        "status": "playing", "year": info["year"], "label": info["label"], "genre": info["genre"],
        "duration_ms": info["duration_ms"], "links": search_links(info["artist"], info["title"], info["album"]),
    })
    return web.json_response({"ok": True})


async def api_stats(request):
    try:
        days = int(request.query.get("days", "30"))
    except ValueError:
        days = 30
    return web.json_response(request.app["state"].db.stats(days))


def icon_png(size):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (11, 11, 12))
    d = ImageDraw.Draw(img)
    c = size / 2
    for r, col in ((0.40, (20, 20, 22)), (0.34, (40, 40, 44)), (0.27, (24, 24, 26)),
                   (0.20, (40, 40, 44)), (0.14, (201, 160, 74)), (0.025, (20, 20, 22))):
        d.ellipse([c - r * size, c - r * size, c + r * size, c + r * size], fill=col)
    bio = io.BytesIO()
    img.save(bio, "PNG")
    return bio.getvalue()


_icons = {}


async def icon_file(request):
    size = 512 if "512" in request.path else 192
    if size not in _icons:
        _icons[size] = icon_png(size)
    return web.Response(body=_icons[size], content_type="image/png")


SW = """self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));
"""


async def service_worker(request):
    return web.Response(text=SW, content_type="application/javascript")


# ---------------------------------------------------------------- updates (installed app only)
try:
    from _version import VERSION
except Exception:
    VERSION = "dev"
RELEASES_API = "https://api.github.com/repos/PostModernPost/coverart/releases/latest"


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("v").split("-")[0].split("."))
    except ValueError:
        return (0,)


async def check_for_update(app):
    """Ask GitHub for the newest release. Sets app['update'] when it's newer than this copy."""
    if VERSION == "dev":
        return None
    try:
        async with app["http"].get(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": "LinerNotes"}) as r:
            if r.status != 200:
                return None
            rel = await r.json(content_type=None)
    except Exception as e:
        log("update check failed:", e)
        return None
    latest = rel.get("tag_name", "")
    asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
    app["update_checked"] = time.time()
    if asset and version_tuple(latest) > version_tuple(VERSION):
        app["update"] = {"version": latest.lstrip("v"), "url": asset["browser_download_url"],
                         "notes": (rel.get("body") or "").strip()[:2000], "page": rel.get("html_url")}
        log(f"update available: {latest}")
    else:
        app["update"] = None
    return app["update"]


async def update_loop(app):
    await asyncio.sleep(10)
    while True:
        await check_for_update(app)
        await asyncio.sleep(6 * 3600)


async def install_update(app):
    """Download the new installer and run it quietly; it closes this copy and reopens the new one."""
    upd = app.get("update")
    if not upd:
        return False
    app["updating"] = True
    path = os.path.join(tempfile.gettempdir(), f"LinerNotes-Setup-{upd['version']}.exe")
    log(f"downloading update {upd['version']}")
    async with app["http"].get(upd["url"], timeout=ClientTimeout(total=600)) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 16):
                f.write(chunk)
    log("starting installer")
    import subprocess
    subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/updated=1"],
                     creationflags=0x00000008 | 0x00000200)   # detached, own process group
    asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))
    return True


def update_info(app):
    return {"current": VERSION, "available": app.get("update"), "updating": bool(app.get("updating"))}


async def api_update(request):
    app = request.app
    if request.method == "POST":
        action = (await request.json()).get("action")
        if action == "check":
            await check_for_update(app)
        elif action == "install":
            try:
                ok = await install_update(app)
            except Exception as e:
                app["updating"] = False
                log("update failed:", repr(e))
                return web.json_response({**update_info(app), "error": "The download didn't work. Try again later."})
            return web.json_response({**update_info(app), "ok": ok})
    return web.json_response(update_info(app))


async def api_window_fullscreen(request):
    fn = request.app.get("on_toggle_fullscreen")
    full = await asyncio.get_running_loop().run_in_executor(None, fn) if fn else None
    return web.json_response({"ok": fn is not None, "full": full})


async def api_window(request):
    """A second launch of Liner Notes asks the running one to show its window."""
    fn = request.app.get("on_show_window")
    if fn:
        fn()
    return web.json_response({"ok": bool(fn)})


# ---------------------------------------------------------------- record mode endpoints
async def api_records_search(request):
    if not discogs_ready():
        return web.json_response({"results": [], "need_key": True})
    q = (request.query.get("q") or "").strip()
    barcode = re.sub(r"\D", "", request.query.get("barcode") or "")
    if not q and not barcode:
        return web.json_response({"results": []})
    try:
        return web.json_response({"results": await discogs_search(request.app["http"], q, barcode)})
    except PermissionError as e:
        return web.json_response({"results": [], "need_key": True, "error": str(e)})
    except Exception as e:
        log("discogs search failed:", repr(e))
        return web.json_response({"results": [], "error": "Discogs isn't reachable right now."})


async def api_records_load(request):
    app = request.app
    body = await request.json()
    try:
        rel = await discogs_get(app["http"], f"/releases/{int(body.get('id'))}")
    except Exception as e:
        log("discogs release failed:", repr(e))
        return web.json_response({"ok": False, "error": "Couldn't load that record from Discogs."})
    rec = RecordSession(rel)
    if not rec.tracks:
        return web.json_response({"ok": False, "error": "Discogs doesn't list the songs on this one."})
    app["record"] = rec
    log(f"record loaded: {rec.artist} - {rec.title} ({len(rec.tracks)} tracks)")
    if app.get("listen_mode") == "active":          # already playing: start at track 1, Shazam will correct it
        show_record(app, rec, rec.set_index(0))
        app["force_check"] = True
    return web.json_response({"ok": True, "record": rec.summary()})


async def api_records_position(request):
    """You tapped a song in the tracklist: that's what's playing now."""
    app = request.app
    rec = app.get("record")
    if not rec:
        return web.json_response({"ok": False})
    body = await request.json()
    show_record(app, rec, rec.set_index(body.get("index", 0)))
    return web.json_response({"ok": True, "record": rec.summary()})


async def api_records_clear(request):
    app = request.app
    rec = app.pop("record", None)
    st = app["state"]
    if rec and st.track and st.track.get("source") == "record":
        st.idle()
    return web.json_response({"ok": True})


async def api_config(request):
    """Settings changed from the app (currently: a personal Discogs token)."""
    body = await request.json()
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    if "discogs_token" in body:
        cfg["discogs_token"] = CFG["discogs_token"] = (body.get("discogs_token") or "").strip()
    if "phone_follows_pc" in body:
        cfg["phone_follows_pc"] = CFG["phone_follows_pc"] = bool(body.get("phone_follows_pc"))
    if "lastfm_include_pc" in body:
        cfg["lastfm_include_pc"] = CFG["lastfm_include_pc"] = bool(body.get("lastfm_include_pc"))
    if body.get("spotify_save_to") in ("playlist", "liked"):
        cfg["spotify_save_to"] = CFG["spotify_save_to"] = body["spotify_save_to"]
    if "spotify_client_id" in body:
        new = re.sub(r"[^0-9a-fA-F]", "", body.get("spotify_client_id") or "")
        if new != (CFG.get("spotify_client_id") or ""):
            spotify_store({})          # a different Spotify app needs a fresh login
        cfg["spotify_client_id"] = CFG["spotify_client_id"] = new
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return web.json_response({"ok": True, "discogs_ready": discogs_ready(), "spotify": spotify_status(),
                              "lastfm": lastfm_status()})


# ---------------------------------------------------------------- save the band (Spotify)
# The heart on the main screen keeps a song in Liner Notes' Saved list. With Spotify connected it
# also goes into a "Liner Notes" playlist (or Liked Songs) on the listener's account.
# Login uses Spotify's PKCE flow, so no secret ships with the app: just a client ID.
try:
    from _secrets import SPOTIFY_CLIENT_ID as _BUILTIN_SPOTIFY_ID
except Exception:
    _BUILTIN_SPOTIFY_ID = ""
SPOTIFY_FILE = os.path.join(DATA_DIR, "spotify.json")
SPOTIFY_SCOPES = "user-library-modify user-library-read playlist-modify-private playlist-modify-public playlist-read-private"
PLAYLIST_NAME = "Liner Notes"
_spotify_login = {}           # state -> code verifier, for logins in progress


def spotify_client_id():
    return (CFG.get("spotify_client_id") or "").strip() or _BUILTIN_SPOTIFY_ID


def spotify_redirect():
    return f"http://127.0.0.1:{CFG['port']}/api/spotify/callback"


def spotify_load():
    try:
        with open(SPOTIFY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def spotify_store(d):
    with open(SPOTIFY_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


async def spotify_token(http):
    """A working access token, refreshed when needed. None when Spotify isn't connected."""
    d = spotify_load()
    if not d.get("refresh_token"):
        return None
    if d.get("access_token") and d.get("expires", 0) > time.time() + 60:
        return d["access_token"]
    async with http.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "refresh_token", "refresh_token": d["refresh_token"],
            "client_id": d.get("client_id") or spotify_client_id()}) as r:
        j = await r.json(content_type=None)
    if "access_token" not in j:
        log("spotify: refresh failed:", j.get("error_description") or j.get("error") or r.status)
        if j.get("error") == "invalid_grant":
            spotify_store({})          # the listener removed access in Spotify; start over
        return None
    d.update(access_token=j["access_token"], expires=time.time() + int(j.get("expires_in", 3600)))
    if j.get("refresh_token"):
        d["refresh_token"] = j["refresh_token"]
    spotify_store(d)
    return d["access_token"]


async def spotify_call(http, method, path, **kw):
    token = await spotify_token(http)
    if not token:
        return None, None
    async with http.request(method, "https://api.spotify.com/v1" + path,
                            headers={"Authorization": f"Bearer {token}"}, **kw) as r:
        try:
            j = await r.json(content_type=None)
        except Exception:
            j = None
        if r.status >= 400:
            log("spotify:", method, path, r.status, (j or {}).get("error"))
        return r.status, j


async def spotify_find(http, t):
    """The Spotify track URI for a song, found by searching title and artist."""
    title, artist = t.get("title") or "", t.get("artist") or ""
    base = lambda x: norm(re.sub(r"\s*[\(\[].*?[\)\]]|\s+-\s+.*$", "", x))
    for q in (f'track:"{title}" artist:"{artist}"', f"{title} {artist}"):
        st, j = await spotify_call(http, "GET", "/search", params={"q": q, "type": "track", "limit": "10"})
        items = ((j or {}).get("tracks") or {}).get("items") or []
        for it in items:
            artists = [norm(a.get("name")) for a in it.get("artists") or []]
            if base(it.get("name") or "") == base(title) and (norm(artist) in artists or
                    any(a and (a in norm(artist) or norm(artist) in a) for a in artists)):
                return it.get("uri")
    return None


async def spotify_playlist(http):
    d = spotify_load()
    if d.get("playlist_id"):
        return d["playlist_id"]
    # reuse a "Liner Notes" playlist if one is already there (after reconnecting)
    st, j = await spotify_call(http, "GET", "/me/playlists", params={"limit": "50"})
    for p in (j or {}).get("items") or []:
        if p and p.get("name") == PLAYLIST_NAME:
            d["playlist_id"] = p["id"]
            break
    else:
        st, j = await spotify_call(http, "POST", "/me/playlists", json={
            "name": PLAYLIST_NAME, "public": False,
            "description": "Songs saved from Liner Notes, including the ones heard on records."})
        if not j or not j.get("id"):
            return None
        d["playlist_id"] = j["id"]
    spotify_store(d)
    return d["playlist_id"]


async def spotify_add(http, uri):
    if CFG.get("spotify_save_to", "playlist") == "liked":
        st, _ = await spotify_call(http, "PUT", "/me/library", params={"uris": uri})
        return st is not None and st < 300
    pid = await spotify_playlist(http)
    if not pid:
        return False
    st, _ = await spotify_call(http, "POST", f"/playlists/{pid}/items", json={"uris": [uri]})
    return st is not None and st < 300


async def spotify_remove(http, uri):
    if CFG.get("spotify_save_to", "playlist") == "liked":
        await spotify_call(http, "DELETE", "/me/library", params={"uris": uri})
        return
    pid = spotify_load().get("playlist_id")
    if pid:
        await spotify_call(http, "DELETE", f"/playlists/{pid}/items", json={"items": [{"uri": uri}]})


def spotify_status():
    d = spotify_load()
    return {"configured": bool(spotify_client_id()), "builtin": bool(_BUILTIN_SPOTIFY_ID),
            "connected": bool(d.get("refresh_token")), "user": d.get("user"),
            "save_to": CFG.get("spotify_save_to", "playlist"), "redirect": spotify_redirect(),
            "own_id": bool((CFG.get("spotify_client_id") or "").strip())}


async def api_save(request):
    """Heart or un-heart a song. Saved songs also go to Spotify when it's connected."""
    st: State = request.app["state"]
    body = await request.json()
    tid = body.get("id") or ""
    t = st.history.get(tid) or (st.track if st.track and st.track.get("id") == tid else None)
    db = st.db
    http = request.app["http"]
    if body.get("saved") is False or (body.get("saved") is None and tid in db.saved_ids):
        uri = db.unsave(tid)
        if uri:
            try:
                await spotify_remove(http, uri)
            except Exception as e:
                log("spotify: remove failed:", e)
        return web.json_response({"saved": False})
    if not t or not t.get("artist"):
        return web.json_response({"error": "That song isn't on screen any more."}, status=404)
    db.save(tid, t)
    result = "not_connected"
    if spotify_load().get("refresh_token"):
        try:
            uri = await spotify_find(http, t)
            if not uri:
                result = "not_found"
            elif await spotify_add(http, uri):
                db.set_spotify_uri(tid, uri)
                result = "saved"
            else:
                result = "error"
        except Exception as e:
            log("spotify: save failed:", e)
            result = "error"
    return web.json_response({"saved": True, "spotify": result,
                              "where": "Liked Songs" if CFG.get("spotify_save_to") == "liked" else PLAYLIST_NAME})


async def api_saved(request):
    rows = request.app["state"].db.saved_list()
    for r in rows:
        r["links"] = {l["name"].lower(): l["url"] for l in search_links(r["artist"], r["title"], r.get("album"))}
        if r.get("spotify_uri"):
            r["links"]["spotify"] = "https://open.spotify.com/track/" + r["spotify_uri"].split(":")[-1]
    return web.json_response({"saved": rows, "spotify": spotify_status()})


async def api_spotify_status(request):
    return web.json_response(spotify_status())


async def api_spotify_connect(request):
    """Open Spotify's login in the PC's browser. It comes back to /api/spotify/callback."""
    import base64, secrets, webbrowser
    cid = spotify_client_id()
    if not cid:
        return web.json_response({"error": "No Spotify app set up."}, status=400)
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    _spotify_login.clear()
    _spotify_login[state] = (verifier, cid)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": spotify_redirect(), "state": state,
        "scope": SPOTIFY_SCOPES, "code_challenge_method": "S256", "code_challenge": challenge})
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    return web.json_response({"ok": True, "url": url})


def _done_page(msg):
    return web.Response(content_type="text/html", text=f"""<!doctype html><meta charset="utf-8">
<title>Liner Notes</title><body style="background:#111;color:#eee;font:18px system-ui;display:grid;place-items:center;height:90vh;text-align:center">
<div><h2 style="font-weight:600">{msg}</h2><p style="color:#999">You can close this tab and go back to Liner Notes.</p></div>""")


async def api_spotify_callback(request):
    q = request.query
    if q.get("error"):
        return _done_page("Spotify wasn't connected" + (" (you said no)." if q["error"] == "access_denied" else f": {q['error']}."))
    pending = _spotify_login.pop(q.get("state", ""), None)
    if not pending or not q.get("code"):
        return _done_page("That login link expired. Try Connect Spotify again.")
    verifier, cid = pending
    http = request.app["http"]
    async with http.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "authorization_code", "code": q["code"], "redirect_uri": spotify_redirect(),
            "client_id": cid, "code_verifier": verifier}) as r:
        j = await r.json(content_type=None)
    if "refresh_token" not in j:
        log("spotify: login failed:", j)
        return _done_page("Spotify wasn't connected: " + (j.get("error_description") or j.get("error") or "unknown error"))
    spotify_store({"client_id": cid, "refresh_token": j["refresh_token"], "access_token": j["access_token"],
                   "expires": time.time() + int(j.get("expires_in", 3600))})
    st, me = await spotify_call(http, "GET", "/me")
    if st == 403:
        spotify_store({})
        return _done_page("Spotify says this account isn't allowed to use Liner Notes yet. "
                          "Ask the app's owner to add your Spotify email, or use your own Spotify app (Settings).")
    d = spotify_load()
    d["user"] = (me or {}).get("display_name") or (me or {}).get("id")
    spotify_store(d)
    # Anything hearted before connecting goes to Spotify now
    asyncio.create_task(spotify_backfill(request.app))
    return _done_page("Spotify connected" + (f" as {d['user']}" if d.get("user") else "") + ".")


async def spotify_backfill(app):
    db, http = app["state"].db, app["http"]
    for r in reversed(db.saved_list()):
        if r.get("spotify_uri"):
            continue
        try:
            uri = await spotify_find(http, r)
            if uri and await spotify_add(http, uri):
                db.set_spotify_uri(r["tid"], uri)
        except Exception as e:
            log("spotify: backfill:", e)
        await asyncio.sleep(0.3)


async def api_spotify_disconnect(request):
    spotify_store({})
    return web.json_response(spotify_status())


# ---------------------------------------------------------------- Last.fm scrobbling
# Records you play get logged ("scrobbled") to your Last.fm profile, next to what Spotify already
# sends there. Last.fm's rule: a song counts once it has played for half its length or 4 minutes.
try:
    from _secrets import LASTFM_KEY, LASTFM_SECRET
except Exception:
    LASTFM_KEY = LASTFM_SECRET = ""
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
LASTFM_FILE = os.path.join(DATA_DIR, "lastfm.json")
VINYL_SOURCES = ("turntable", "record", "manual")


def lastfm_load():
    try:
        with open(LASTFM_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def lastfm_store(d):
    with open(LASTFM_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def lastfm_sign(params):
    raw = "".join(k + str(params[k]) for k in sorted(params) if k not in ("format", "callback")) + LASTFM_SECRET
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def lastfm_call(http, method, params, post=True):
    p = {"method": method, "api_key": LASTFM_KEY, **{k: v for k, v in params.items() if v not in (None, "")}}
    p["api_sig"] = lastfm_sign(p)
    p["format"] = "json"
    req = http.post(LASTFM_API, data=p) if post else http.get(LASTFM_API, params=p)
    async with req as r:
        j = await r.json(content_type=None)
    if isinstance(j, dict) and j.get("error"):
        log("last.fm:", method, j.get("error"), j.get("message"))
    return j or {}


def lastfm_status():
    d = lastfm_load()
    return {"available": bool(LASTFM_KEY and LASTFM_SECRET), "connected": bool(d.get("sk")), "user": d.get("user"),
            "count": d.get("count", 0), "pending": len(d.get("pending") or []),
            "include_pc": bool(CFG.get("lastfm_include_pc", False))}


async def api_lastfm_status(request):
    return web.json_response(lastfm_status())


async def api_lastfm_connect(request):
    import webbrowser
    if not (LASTFM_KEY and LASTFM_SECRET):
        return web.json_response({"error": "This build doesn't include a Last.fm key."}, status=400)
    cb = f"http://127.0.0.1:{CFG['port']}/api/lastfm/callback"
    url = "https://www.last.fm/api/auth/?" + urllib.parse.urlencode({"api_key": LASTFM_KEY, "cb": cb})
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    return web.json_response({"ok": True})


async def api_lastfm_callback(request):
    token = request.query.get("token")
    if not token:
        return _done_page("Last.fm wasn't connected.")
    j = await lastfm_call(request.app["http"], "auth.getSession", {"token": token}, post=False)
    sess = j.get("session") or {}
    if not sess.get("key"):
        return _done_page("Last.fm wasn't connected: " + (j.get("message") or "unknown error"))
    d = lastfm_load()
    d.update(sk=sess["key"], user=sess.get("name"))
    lastfm_store(d)
    return _done_page("Last.fm connected" + (f" as {sess.get('name')}" if sess.get("name") else "") + ".")


async def api_lastfm_disconnect(request):
    d = lastfm_load()
    lastfm_store({"count": d.get("count", 0)})
    return web.json_response(lastfm_status())


async def lastfm_send(http, items):
    """Scrobble up to 50 plays. Returns True when Last.fm took them (or rejected them for good)."""
    d = lastfm_load()
    if not d.get("sk"):
        return False
    params = {"sk": d["sk"]}
    for i, it in enumerate(items[:50]):
        for k in ("artist", "track", "album", "timestamp", "duration"):
            if it.get(k):
                params[f"{k}[{i}]"] = it[k]
    try:
        j = await lastfm_call(http, "track.scrobble", params)
    except Exception as e:
        log("last.fm: scrobble failed (will retry):", e)
        return False
    if j.get("error") in (9, 4, 14):         # session no longer valid: needs connecting again
        d.pop("sk", None)
        lastfm_store(d)
        return False
    if j.get("error") in (11, 16, 29):       # Last.fm busy or rate limited: try later
        return False
    acc = int(((j.get("scrobbles") or {}).get("@attr") or {}).get("accepted", 0) or 0)
    d = lastfm_load()
    d["count"] = d.get("count", 0) + acc
    lastfm_store(d)
    log(f"last.fm: scrobbled {acc} of {len(items[:50])}")
    return True


async def lastfm_now_playing(http, t):
    d = lastfm_load()
    if d.get("sk"):
        try:
            await lastfm_call(http, "track.updateNowPlaying", {
                "sk": d["sk"], "artist": t.get("artist"), "track": t.get("title"), "album": t.get("album"),
                "duration": int((t.get("duration_ms") or 0) / 1000) or None})
        except Exception:
            pass


async def lastfm_loop(app):
    """Watch what's on screen. Once a song has played long enough, scrobble it (once)."""
    st: State = app["state"]
    http = app["http"]
    cur = None               # {"id", "start", "done"}
    last_retry = 0
    while True:
        await asyncio.sleep(5)
        try:
            if not (LASTFM_KEY and LASTFM_SECRET):
                continue
            d = lastfm_load()
            if not d.get("sk"):
                cur = None
                continue
            t = st.track
            ok_source = t and (t.get("source") in VINYL_SOURCES or CFG.get("lastfm_include_pc"))
            if not t or t.get("status") != "playing" or not ok_source or not t.get("artist"):
                cur = None if not t or t.get("status") != "playing" else cur
                continue
            now = time.time()
            if not cur or cur["id"] != t["id"]:
                pr = progress_now(app)
                pos = pr.get("pos") if pr else None
                cur = {"id": t["id"], "start": now - (pos or 0), "done": False}
                asyncio.create_task(lastfm_now_playing(http, t))
            if cur["done"]:
                continue
            dur = (t.get("duration_ms") or 0) / 1000 or None
            if dur is not None and dur < 30:
                cur["done"] = True
                continue
            pr = progress_now(app)
            if pr and pr.get("pos") is not None and pr.get("kind") == "exact":
                cur["start"] = now - pr["pos"]          # Shazam or Spotify knows exactly where in the song we are
            played = now - cur["start"]
            if played >= min((dur or 180) / 2, 240):
                cur["done"] = True
                item = {"artist": t["artist"], "track": t["title"], "album": t.get("album"),
                        "timestamp": int(cur["start"]), "duration": int(dur) if dur else None}
                d = lastfm_load()
                d.setdefault("pending", []).append(item)
                lastfm_store(d)
                last_retry = 0
            # send anything waiting (new plays, or ones that failed while offline)
            pending = lastfm_load().get("pending") or []
            pending = [p for p in pending if p["timestamp"] > now - 13 * 86400]     # Last.fm takes up to 14 days back
            if pending and now - last_retry > 60:
                last_retry = now
                if await lastfm_send(http, pending):
                    d = lastfm_load()
                    d["pending"] = [p for p in (d.get("pending") or []) if p not in pending[:50]]
                    lastfm_store(d)
        except Exception as e:
            log("last.fm loop:", repr(e))


# ---------------------------------------------------------------- phone as a second screen (USB)
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
TOOLS_DIR = os.path.join(DATA_DIR, "platform-tools")


def adb_path():
    """Android's USB connector (adb): ours, or one already on this PC."""
    import shutil
    ours = os.path.join(TOOLS_DIR, "adb.exe" if sys.platform == "win32" else "adb")
    if os.path.exists(ours):
        return ours
    p = shutil.which("adb")
    if p:
        return p
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet")
    for root in (os.path.join(base, "Links"), os.path.join(base, "Packages")):
        for dirpath, _, files in os.walk(root):
            if "adb.exe" in files:
                return os.path.join(dirpath, "adb.exe")
    return None


def adb_run(*args, timeout=10):
    import subprocess
    adb = adb_path()
    if not adb:
        return None
    try:
        return subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    except Exception as e:
        log("adb failed:", e)
        return None


def adb_devices():
    """Phones plugged in: [{serial, state ('device' = ready, 'unauthorized' = tap Allow), model}]"""
    r = adb_run("devices", "-l")
    if not r:
        return []
    out = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1].replace("_", " ") for p in parts[2:] if p.startswith("model:")), None)
            out.append({"serial": parts[0], "state": parts[1], "model": model})
    return out


def adb_phone_screens(on):
    """Turn the screens of plugged-in phones on or off (like pressing their power button)."""
    for d in adb_devices():
        if d["state"] == "device":
            adb_run("-s", d["serial"], "shell", "input", "keyevent", "KEYCODE_WAKEUP" if on else "KEYCODE_SLEEP", timeout=5)


def adb_connect_phones():
    """Point each ready phone's localhost at this PC, so the phone can open the screen."""
    for d in adb_devices():
        if d["state"] == "device":
            adb_run("-s", d["serial"], "reverse", f"tcp:{CFG['port']}", f"tcp:{CFG['port']}")


async def install_platform_tools(http):
    """Download Google's Android platform tools (about 7 MB) into Liner Notes' folder."""
    import zipfile
    path = os.path.join(tempfile.gettempdir(), "platform-tools.zip")
    async with http.get(PLATFORM_TOOLS_URL, timeout=ClientTimeout(total=300)) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 16):
                f.write(chunk)
    with zipfile.ZipFile(path) as z:
        z.extractall(DATA_DIR)            # the zip holds a platform-tools folder
    os.remove(path)
    log("Android connector installed")


async def api_phone_status(request):
    app = request.app
    loop = asyncio.get_running_loop()
    devices = await loop.run_in_executor(None, adb_devices) if adb_path() else []
    if any(d["state"] == "device" for d in devices):
        await loop.run_in_executor(None, adb_connect_phones)
    return web.json_response({
        "tools": bool(adb_path()), "installing": bool(app.get("tools_installing")),
        "install_error": app.get("tools_error"), "devices": devices,
        "page_open": time.time() - app.get("last_phone_poll", 0) < 6,
        "mic_is_phone": str(CFG.get("audio_device")).lower() == "phone",
        "port": CFG["port"],
        "wifi": bool(CFG.get("allow_other_devices")), "wifi_address": lan_address(),
        "wifi_open": time.time() - app.get("last_wifi_poll", 0) < 6,
        "wifi_iphone": "iPhone" in app.get("last_wifi_agent", "") or "iPad" in app.get("last_wifi_agent", "")})


async def api_phone_install(request):
    app = request.app
    if app.get("tools_installing"):
        return web.json_response({"ok": True})
    app["tools_installing"], app["tools_error"] = True, None

    async def go():
        try:
            await install_platform_tools(app["http"])
        except Exception as e:
            log("connector install failed:", repr(e))
            app["tools_error"] = "The download didn't work. Check the internet connection and try again."
        finally:
            app["tools_installing"] = False
    asyncio.create_task(go())
    return web.json_response({"ok": True})


async def api_phone_use_mic(request):
    """Switch listening to the phone's microphone (restarts the listening part)."""
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["audio_device"] = CFG["audio_device"] = "phone"
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    fn = request.app.get("on_restart")
    if fn:
        asyncio.get_running_loop().call_later(0.5, fn)
    return web.json_response({"ok": True, "restarting": bool(fn)})


# ---------------------------------------------------------------- sharing on Wi-Fi (iPhones, other devices)
# Requests from other devices may show the screen and use search; anything that changes this PC
# (updates, settings, phone tools, the window) only answers requests from this PC itself.
LOCAL_ONLY = ("/api/update", "/api/config", "/api/phone/", "/api/window", "/api/share-wifi", "/api/spotify/", "/api/lastfm/")


@web.middleware
async def local_only_guard(request, handler):
    if request.remote not in ("127.0.0.1", "::1") and request.path.startswith(LOCAL_ONLY):
        return web.json_response({"error": "Only Liner Notes on this PC can do that."}, status=403)
    if request.remote not in ("127.0.0.1", "::1") and request.path == "/api/now":
        request.app["last_wifi_poll"] = time.time()
        request.app["last_wifi_agent"] = request.headers.get("User-Agent", "")
    return await handler(request)


def save_config_value(key, value):
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg[key] = CFG[key] = value
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


async def api_share_wifi(request):
    """Turn Wi-Fi sharing on or off. The server has to restart to start or stop listening on the network."""
    body = await request.json()
    save_config_value("allow_other_devices", bool(body.get("on")))
    fn = request.app.get("on_restart")
    if fn:
        asyncio.get_running_loop().call_later(0.5, fn)
    return web.json_response({"ok": True, "restarting": bool(fn)})


async def api_qr(request):
    """QR code (SVG) for the Wi-Fi address, so a phone camera can open it."""
    import qrcode
    import qrcode.image.svg
    ip = lan_address()
    if not ip:
        raise web.HTTPNotFound()
    img = qrcode.make(f"http://{ip}:{CFG['port']}", image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return web.Response(text=img.to_string(encoding="unicode"), content_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})


def mic_info(app):
    mic = app.get("mic")
    if not mic or mic.kind != "phone":
        return {"mode": "off"}
    return {"mode": app.get("listen_mode", "off"), "every": CFG["probe_every_seconds"],
            "seconds": CFG["probe_seconds"]}


async def api_now(request):
    st: State = request.app["state"]
    if "Android" not in request.headers.get("User-Agent", ""):
        request.app["last_window_poll"] = time.time()      # a Liner Notes window on the PC is open
    else:
        request.app["last_phone_poll"] = time.time()       # the phone screen is open
    return web.json_response({"version": st.version, "track": st.track, "mic": mic_info(request.app),
                              "donate": CFG.get("donate_url") or "", "update": update_info(request.app),
                              "record": request.app["record"].summary() if request.app.get("record") else None,
                              "progress": progress_now(request.app),
                              "phone_follows_pc": CFG.get("phone_follows_pc", True),
                              "saved": bool(st.track and st.track.get("id") in st.db.saved_ids),
                              "discogs_ready": discogs_ready(),
                              "listen": {"mode": request.app.get("listen_mode", "off"),
                                         "next_try": request.app.get("next_try"), "now": time.time()}})


async def api_audio(request):
    """The phone page uploads 16 kHz mono 16-bit audio here."""
    import numpy as np
    app = request.app
    mic = app.get("mic")
    body = await request.read()
    if mic is None or mic.kind != "phone":
        return web.json_response({"mic": mic_info(app)})
    if len(body) >= 2:
        data = np.frombuffer(body[: len(body) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        mic.last_upload = time.time()
        if request.query.get("probe") == "1" or app.get("listen_mode") != "active":
            mic.probes.append(data)
        else:
            mic.buf.feed(data)
    return web.json_response({"mic": mic_info(app)})


async def api_art(request):
    st: State = request.app["state"]
    item = st.art.get(request.match_info["tid"])
    if not item:
        raise web.HTTPNotFound()
    data, mime = item
    return web.Response(body=data, content_type=mime.split(";")[0])


async def api_info(request):
    st: State = request.app["state"]
    tid = request.query.get("id", "")
    if tid in st.info_cache:
        return web.json_response(st.info_cache[tid])
    t = st.history.get(tid)
    if not t or not t.get("artist"):
        return web.json_response({"lyrics": None, "bio": None})
    http = request.app["http"]
    (lyrics, synced), bio = await asyncio.gather(
        fetch_lyrics(http, t["artist"], t["title"], t.get("album"), (t.get("duration_ms") or 0) / 1000 or None),
        fetch_bio(http, t["artist"]),
    )
    info = {"lyrics": lyrics, "synced": synced, "bio": bio,
            "genius": "https://genius.com/search?q=" + urllib.parse.quote(f"{main_artist(t['artist'])} {clean_title(t['title'])}")}
    st.info_cache[tid] = info
    return web.json_response(info)


async def on_startup(app):
    app["http"] = ClientSession(timeout=ClientTimeout(total=10))
    if app["demo"]:
        app["tasks"] = [asyncio.create_task(demo_loop(app))]
    else:
        app["tasks"] = [asyncio.create_task(pc_media_loop(app)),
                        asyncio.create_task(turntable_loop(app)),
                        asyncio.create_task(update_loop(app)),
                        asyncio.create_task(lastfm_loop(app))]


async def on_cleanup(app):
    for t in app["tasks"]:
        t.cancel()
    await app["http"].close()


def build_app(demo=False):
    app = web.Application(middlewares=[local_only_guard])
    app["state"] = State()
    app["state"].db = History(os.path.join(DATA_DIR, "history.db"))
    app["demo"] = demo
    app.router.add_get("/", index)
    app.router.add_get("/api/now", api_now)
    app.router.add_get("/api/info", api_info)
    app.router.add_get("/api/art/{tid}", api_art)
    app.router.add_post("/api/audio", api_audio)
    app.router.add_get("/manifest.json", manifest)
    app.router.add_get("/api/search", api_search)
    app.router.add_post("/api/pick", api_pick)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/icon-192.png", icon_file)
    app.router.add_get("/icon-512.png", icon_file)
    app.router.add_get("/sw.js", service_worker)
    app.router.add_get("/api/update", api_update)
    app.router.add_post("/api/window", api_window)
    app.router.add_post("/api/window/fullscreen", api_window_fullscreen)
    app.router.add_get("/api/records/search", api_records_search)
    app.router.add_post("/api/records/load", api_records_load)
    app.router.add_post("/api/records/position", api_records_position)
    app.router.add_post("/api/records/clear", api_records_clear)
    app.router.add_post("/api/config", api_config)
    app.router.add_post("/api/save", api_save)
    app.router.add_get("/api/saved", api_saved)
    app.router.add_get("/api/spotify/status", api_spotify_status)
    app.router.add_get("/api/lastfm/status", api_lastfm_status)
    app.router.add_post("/api/lastfm/connect", api_lastfm_connect)
    app.router.add_get("/api/lastfm/callback", api_lastfm_callback)
    app.router.add_post("/api/lastfm/disconnect", api_lastfm_disconnect)
    app.router.add_post("/api/spotify/connect", api_spotify_connect)
    app.router.add_get("/api/spotify/callback", api_spotify_callback)
    app.router.add_post("/api/spotify/disconnect", api_spotify_disconnect)
    app.router.add_get("/api/phone/status", api_phone_status)
    app.router.add_post("/api/phone/install", api_phone_install)
    app.router.add_post("/api/phone/use-mic", api_phone_use_mic)
    app.router.add_post("/api/share-wifi", api_share_wifi)
    app.router.add_get("/api/qr.svg", api_qr)
    app.router.add_post("/api/update", api_update)
    app.router.add_get("/icon.svg", icon)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def bind_host():
    return "0.0.0.0" if CFG["allow_other_devices"] else "127.0.0.1"


def lan_address():
    if not CFG["allow_other_devices"]:
        return None
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


async def serve(app):
    """Start the server inside an already-running event loop (used by the tray app)."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, bind_host(), CFG["port"]).start()
    return runner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"{i:3d}  {d['name']}")
        return

    app = build_app(args.demo)
    log(f"{APP_NAME} running at http://localhost:{CFG['port']}")
    lan = lan_address()
    if lan:
        log(f"On your phone, open http://{lan}:{CFG['port']}")
    web.run_app(app, host=bind_host(), port=CFG["port"], print=None)

if __name__ == "__main__":
    main()
