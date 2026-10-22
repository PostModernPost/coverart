"""
Cover Art: shows the album art of whatever is playing.

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
import sys
import tempfile
import threading
import time
import urllib.parse
import wave

from aiohttp import web, ClientSession, ClientTimeout

APP_NAME = "Cover Art"
# Bundled files (display.html) live next to this file, or in PyInstaller's unpack folder when installed.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# Settings and the log live in the user's AppData, since Program Files is read-only.
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
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
        self.art = {}              # id -> (bytes, mime) for thumbnails from Windows
        self.history = {}          # id -> track, so info lookups work after a change
        self.info_cache = {}

    def _bump(self, track):
        self.track = track
        self.version += 1
        if track and track.get("id"):
            self.history[track["id"]] = track
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


# ---------------------------------------------------------------- lookups
async def itunes_art(http, artist, title):
    """Big album art from the iTunes catalog, only if the artist matches."""
    try:
        q = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 8})
        async with http.get("https://itunes.apple.com/search?" + q) as r:
            data = await r.json(content_type=None)
        for res in data.get("results", []):
            a, b = norm(artist), norm(res.get("artistName"))
            if a and b and (a in b or b in a) and res.get("artworkUrl100"):
                return res["artworkUrl100"].replace("100x100bb", "1000x1000bb"), res.get("collectionName")
    except Exception as e:
        log("itunes lookup failed:", e)
    return None, None


async def fetch_lyrics(http, artist, title, album):
    try:
        q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        async with http.get("https://lrclib.net/api/search?" + q,
                            headers={"User-Agent": "CoverArt/1.0"}) as r:
            data = await r.json(content_type=None)
        for res in data or []:
            if res.get("plainLyrics"):
                return res["plainLyrics"]
            if res.get("instrumental"):
                return "(Instrumental)"
    except Exception as e:
        log("lyrics lookup failed:", e)
    return None


MUSIC_WORDS = ("band", "musician", "singer", "rapper", "group", "duo", "songwriter", "music",
               "dj", "producer", "composer", "guitarist", "drummer", "pianist", "artist", "project")


async def fetch_bio(http, artist):
    if not artist:
        return None
    for title in (f"{artist} (band)", f"{artist} (musician)", f"{artist} (singer)", f"{artist} (rapper)", artist):
        try:
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"))
            async with http.get(url, headers={"User-Agent": "CoverArt/1.0"}) as r:
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
    return props, app_id


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
            props, app_id = res
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
                big, it_album = await itunes_art(http, artist, title)
                if big:
                    art = big
                st.show({
                    "id": tid, "title": title, "artist": artist, "album": album or it_album,
                    "art": art, "source": "pc", "app": app_label(app_id), "status": "playing",
                    "links": search_links(artist, title, album or it_album),
                })
        else:
            app["pc_playing"] = False
            if st.track and st.track.get("source") == "pc":
                stopped_since = stopped_since or time.time()
                if time.time() - stopped_since > 4:   # brief gaps happen when skipping tracks
                    st.idle()
        await asyncio.sleep(CFG["poll_seconds"])


# ---------------------------------------------------------------- turntable (mic + Shazam)
#
# The mic is NOT on all the time. In standby it takes a short sample every
# `probe_every_seconds`. Only when that sample sounds like music does it listen
# continuously (and identify songs), until the room has been quiet for
# `idle_after_seconds`, then it goes back to standby.

class AudioBuffer:
    """The last ~14 seconds of mic audio, plus loudness bookkeeping."""

    BLOCK = 0.05          # seconds per loudness measurement

    def __init__(self, sr):
        import numpy as np
        self.np = np
        self.sr = int(sr)
        self.keep = int(14 * self.sr)
        self.chunks = collections.deque()
        self.samples = 0
        self.lock = threading.Lock()
        self.recent = collections.deque(maxlen=int(8 / self.BLOCK))   # loudness over the last 8 s
        self.reset()

    def reset(self):
        with self.lock:
            self.chunks.clear()
            self.samples = 0
        self.recent.clear()
        self.quiet_for = 0.0
        self.sound_for = 0.0
        self.gap_seen = False
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
        typical = float(self.np.median(self.recent)) if len(self.recent) >= 40 else None
        self.recent.append(rms)
        thr = CFG["silence_threshold"]
        # A gap between songs = clearly quieter than the music around it (room noise never reads zero)
        quiet = rms < thr or (typical is not None and rms < typical * CFG["gap_ratio"])
        if quiet:
            self.quiet_for += dur
            if self.quiet_for >= CFG["gap_seconds"]:
                self.gap_seen = True
                self.sound_for = 0.0
        else:
            self.quiet_for = 0.0
            self.sound_for += dur
        if rms >= thr:
            self.last_sound = time.time()

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
    """Typical loudness of a sample (median of short blocks, so one click or cough doesn't count)."""
    np = __import__("numpy")
    step = max(1, int(sr * 0.05))
    blocks = [data[i:i + step] for i in range(0, len(data) - step + 1, step)]
    if not blocks:
        return 0.0
    return float(np.median([np.sqrt(np.mean(b * b)) for b in blocks]))


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


async def shazam_identify(shz, wav_bytes):
    path = os.path.join(tempfile.gettempdir(), "coverart_clip.wav")
    with open(path, "wb") as f:
        f.write(wav_bytes)
    fn = getattr(shz, "recognize", None) or shz.recognize_song
    out = await fn(path)
    tr = (out or {}).get("track")
    if not tr:
        return None
    album = None
    for sec in tr.get("sections", []) or []:
        for m in sec.get("metadata", []) or []:
            if m.get("title") == "Album":
                album = m.get("text")
    imgs = tr.get("images") or {}
    return {
        "title": tr.get("title"), "artist": tr.get("subtitle"), "album": album,
        "art": imgs.get("coverarthq") or imgs.get("coverart"), "shazam_url": tr.get("url"),
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
    set_mode(app, mic, "standby")

    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        t = st.track
        on_tt = bool(t and t.get("source") == "turntable")

        # Spotify etc. on the PC wins; no need for the mic at all
        if app.get("pc_playing"):
            set_mode(app, mic, "off")
            continue

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
                if level >= CFG["silence_threshold"]:
                    L.reset()
                    L.feed(data)
                    last_try = 0.0
                    misses = 0
                    last_level_log = now
                    log(f"sample level {level:.4f}")
                    set_mode(app, mic, "active")
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
                    f"(music cutoff {CFG['silence_threshold']})")

        if now - L.last_sound > CFG["idle_after_seconds"]:
            if on_tt:
                st.idle()
            set_mode(app, mic, "standby")
            last_probe = now
            continue

        if L.sound_for < CFG["clip_seconds"]:
            continue          # wait for a full clip of music since the start or the last gap

        identified = on_tt and t.get("status") == "playing"
        due = (L.gap_seen
               or (not identified and now - last_try >= CFG["retry_seconds"])
               or (misses > 0 and now - last_try >= CFG["retry_seconds"])
               or (now - last_try >= CFG["recheck_seconds"]))
        if not due:
            continue
        gap = L.gap_seen
        L.gap_seen = False
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

        if res and res.get("title"):
            misses = 0
            tid = make_id(res["artist"], res["title"])
            st.show({
                "id": tid, "title": res["title"], "artist": res["artist"], "album": res["album"],
                "art": res["art"], "source": "turntable", "app": "Turntable", "status": "playing",
                "links": search_links(res["artist"], res["title"], res["album"], res["shazam_url"]),
            })
        else:
            misses += 1
            log("no match")
            # The song changed to one Shazam doesn't know (common on Vinyl Moon). Don't keep showing
            # the previous song: right after a gap, one miss is enough; otherwise two in a row.
            if gap or misses >= 2:
                st.show(UNKNOWN)


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
    return web.FileResponse(os.path.join(HERE, "display.html"))


MANIFEST = {"name": APP_NAME, "short_name": APP_NAME, "start_url": "/", "display": "fullscreen",
            "orientation": "any", "background_color": "#0b0b0c", "theme_color": "#0b0b0c",
            "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#0b0b0c"/>'
        '<circle cx="50" cy="50" r="40" fill="#1b1b1b"/><circle cx="50" cy="50" r="28" fill="#121212"/>'
        '<circle cx="50" cy="50" r="12" fill="#c9a04a"/></svg>')


async def manifest(request):
    return web.json_response(MANIFEST, content_type="application/manifest+json")


async def icon(request):
    return web.Response(text=ICON, content_type="image/svg+xml")


def mic_info(app):
    mic = app.get("mic")
    if not mic or mic.kind != "phone":
        return {"mode": "off"}
    return {"mode": app.get("listen_mode", "off"), "every": CFG["probe_every_seconds"],
            "seconds": CFG["probe_seconds"]}


async def api_now(request):
    st: State = request.app["state"]
    return web.json_response({"version": st.version, "track": st.track, "mic": mic_info(request.app),
                              "donate": CFG.get("donate_url") or ""})


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
    lyrics, bio = await asyncio.gather(
        fetch_lyrics(http, t["artist"], t["title"], t.get("album")),
        fetch_bio(http, t["artist"]),
    )
    info = {"lyrics": lyrics, "bio": bio}
    st.info_cache[tid] = info
    return web.json_response(info)


async def on_startup(app):
    app["http"] = ClientSession(timeout=ClientTimeout(total=10))
    if app["demo"]:
        app["tasks"] = [asyncio.create_task(demo_loop(app))]
    else:
        app["tasks"] = [asyncio.create_task(pc_media_loop(app)),
                        asyncio.create_task(turntable_loop(app))]


async def on_cleanup(app):
    for t in app["tasks"]:
        t.cancel()
    await app["http"].close()


def build_app(demo=False):
    app = web.Application()
    app["state"] = State()
    app["demo"] = demo
    app.router.add_get("/", index)
    app.router.add_get("/api/now", api_now)
    app.router.add_get("/api/info", api_info)
    app.router.add_get("/api/art/{tid}", api_art)
    app.router.add_post("/api/audio", api_audio)
    app.router.add_get("/manifest.json", manifest)
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
