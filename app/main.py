"""
Liner Notes launcher: tray icon, background server, display window.

Installed: runs as LinerNotes.exe (built with PyInstaller).
From source: `pythonw app/main.py` (installs its Python packages on first run).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

APP_NAME = "Liner Notes"
FROZEN = getattr(sys, "frozen", False)
SELF = os.path.abspath(sys.executable if FROZEN else __file__)
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
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
LOG = os.path.join(DATA_DIR, "linernotes.log")
CONFIG = os.path.join(DATA_DIR, "config.json")
NO_WINDOW = 0x08000000
SELFTEST = "--selftest" in sys.argv

if not FROZEN:
    sys.path.insert(0, SRC_DIR)

# No console in the installed app or under pythonw: everything goes to the log (trimmed when big)
if not SELFTEST and (sys.stdout is None or FROZEN or "pythonw" in os.path.basename(sys.executable).lower()):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
            os.remove(LOG)
    except OSError:
        pass
    _f = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = _f
    sys.stderr = _f


def message(text, title=APP_NAME):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        print(text)


def relaunch_cmd(*args):
    if FROZEN:
        return [sys.executable, *args]
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return [pyw if os.path.exists(pyw) else sys.executable, SELF, *args]


# ------------------------------------------------------------- running from source: install packages
def ensure_packages():
    if FROZEN:
        return
    missing = []
    for mod in ("aiohttp", "numpy", "sounddevice", "shazamio", "pystray", "PIL", "winrt.windows.media.control",
                "webview"):
        try:
            __import__(mod)
        except Exception as e:
            missing.append(f"{mod} ({e!r})")
    if not missing:
        return
    print("Installing packages, missing:", missing, flush=True)
    py = os.path.join(os.path.dirname(sys.executable), "python.exe")
    req = os.path.join(SRC_DIR, "..", "requirements.txt")
    r = subprocess.run([py if os.path.exists(py) else sys.executable, "-m", "pip", "install", "-q", "-r", req],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    print(r.stdout, r.stderr, flush=True)
    if r.returncode != 0:
        message(f"Couldn't install the parts {APP_NAME} needs.\n\nDetails are in {LOG}")
        sys.exit(1)


def selftest():
    """Used by the build to prove every bundled part loads. Exit code 0 = good."""
    failures = []
    for mod in ("aiohttp", "numpy", "sounddevice", "shazamio", "pystray", "PIL",
                "winrt.windows.media.control", "winrt.windows.storage.streams", "core",
                "webview", "clr", "webview.platforms.edgechromium", "qrcode", "qrcode.image.svg"):
        try:
            __import__(mod)
        except Exception as e:
            failures.append(f"{mod}: {e!r}")
    try:
        import core
        core.build_app()
        assert os.path.exists(os.path.join(core.HERE, "display.html")), "display.html not bundled"
    except Exception as e:
        failures.append(f"app: {e!r}")
    with open(os.path.join(DATA_DIR, "selftest.txt"), "w") as f:
        f.write("\n".join(failures) or "ok")
    sys.exit(1 if failures else 0)


if SELFTEST:
    selftest()

ensure_packages()

import core  # noqa: E402
import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

PORT = core.CFG["port"]
URL = f"http://localhost:{PORT}"


# ------------------------------------------------------------- helpers
def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def make_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    for r, col in ((0.48, (20, 20, 22, 255)), (0.40, (40, 40, 44, 255)), (0.32, (24, 24, 26, 255)),
                   (0.24, (40, 40, 44, 255)), (0.17, (201, 160, 74, 255)), (0.03, (20, 20, 22, 255))):
        d.ellipse([c - r * size, c - r * size, c + r * size, c + r * size], fill=col)
    return img


WINDOW = {}
ICON = {}
QUITTING = {"now": False}


def show_window():
    """Show Liner Notes's own window (or bring it to the front)."""
    w = WINDOW.get("w")
    if w is None:
        webbrowser.open(URL)            # no built-in window available: use the default browser
        return
    try:
        WINDOW["visible"] = True
        w.show()
        w.restore()
        w.on_top = True                 # brief always-on-top nudge brings it to the front
        w.on_top = False
    except Exception as e:
        print("show window failed:", e, flush=True)


open_window = show_window


def open_panel(name):
    """Show the window with one of its panels open (e.g. the phone setup guide)."""
    show_window()
    w = WINDOW.get("w")
    if w is not None:
        try:
            w.evaluate_js(f"openPanel({json.dumps(name)}); if (window.onPanelOpen) onPanelOpen({json.dumps(name)});")
        except Exception as e:
            print("open panel failed:", e, flush=True)
    else:
        webbrowser.open(f"{URL}/#{name}")


def wait_for_server(seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if port_in_use() and APP.get("app"):
            return True
        time.sleep(0.2)
    return False


def run_window(show):
    """Runs the built-in window on the main thread until Quit. Returns False if it can't start."""
    try:
        import webview
    except Exception as e:
        print("built-in window unavailable:", repr(e), flush=True)
        return False
    wait_for_server()
    try:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # links open in your normal browser
    except Exception:
        pass
    win = webview.create_window(APP_NAME, URL, width=960, height=600, min_size=(480, 320),
                                background_color="#0b0b0c", hidden=not show, text_select=True)
    WINDOW["w"] = win
    WINDOW["visible"] = show

    def on_closing():
        if QUITTING["now"]:
            return True
        WINDOW["visible"] = False
        win.hide()                      # closing the window keeps Liner Notes running by the clock
        return False

    win.events.closing += on_closing
    try:
        webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, "webview"))
    except Exception as e:
        WINDOW.pop("w", None)
        print("built-in window failed:", repr(e), flush=True)
        return False
    return True


def quit_app(icon=None, item=None):
    QUITTING["now"] = True
    try:
        if icon:
            icon.stop()
    finally:
        os._exit(0)


def make_source_shortcut():
    """Running from source only (the installer makes its own shortcuts): one desktop icon."""
    if FROZEN:
        return
    flag = os.path.join(DATA_DIR, ".shortcut_made")
    if os.path.exists(flag):
        return
    ico = os.path.join(DATA_DIR, "linernotes.ico")
    make_image(256).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    target, script = relaunch_cmd()
    ps = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "foreach ($o in 'Now Playing.lnk','Who''s This.lnk','Cover Art.lnk') { $p=Join-Path $d $o; if (Test-Path $p) { Remove-Item $p } };"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Liner Notes.lnk'));"
        f"$s.TargetPath='{target}';$s.Arguments='\"{script}\"';$s.IconLocation='{ico}';"
        "$s.Description='Shows the album art of whatever is playing';$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode == 0:
        open(flag, "w").close()
    else:
        print("shortcut failed:", r.stderr, flush=True)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "LinerNotes")
            return True
    except OSError:
        return False


def set_startup(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            cmd = " ".join(f'"{p}"' for p in relaunch_cmd()) + " --quiet"
            winreg.SetValueEx(k, "LinerNotes", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "LinerNotes")
            except OSError:
                pass


def migrate_startup_entry():
    """Cover Art's Start-with-Windows entry becomes Liner Notes'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, "CoverArt")
            except OSError:
                return
            winreg.DeleteValue(k, "CoverArt")
        set_startup(True)
    except Exception as e:
        print("startup entry migration failed:", e, flush=True)


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def mic_choices():
    """Full-length input names (WASAPI lists them untruncated), deduplicated."""
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        apis = sd.query_hostapis()
        wasapi = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]), None)
        names = []
        for d in devs:
            if d["max_input_channels"] > 0 and (wasapi is None or d["hostapi"] == wasapi):
                n = d["name"]
                if n not in names and "Stereo Mix" not in n and "Hands-Free" not in n:
                    names.append(n)
        return names
    except Exception as e:
        print("device list failed:", e, flush=True)
        return []


# ------------------------------------------------------------- phone over USB
def adb_loop():
    """Keep plugged-in phones pointed at this PC, even after replugging or installing the connector."""
    while True:
        try:
            if core.adb_path():
                core.adb_connect_phones()
        except Exception as e:
            print("phone connect failed:", e, flush=True)
        time.sleep(15)


# ------------------------------------------------------------- server
APP = {}


def server_thread():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = core.build_app()
    loop.run_until_complete(core.serve(app))
    app["on_show_window"] = show_window
    app["on_restart"] = lambda: restart(ICON["i"]) if ICON.get("i") else None
    APP["app"] = app
    APP["loop"] = loop
    loop.run_forever()


def status_text(item=None):
    app = APP.get("app")
    t = app["state"].track if app else None
    if not t:
        mode = app.get("listen_mode") if app else None
        return {"standby": "Nothing playing (mic on standby)", "active": "Listening..."}.get(mode, "Nothing playing")
    if t.get("status") != "playing":
        return "Listening (couldn't identify)"
    s = f"{t.get('artist')} - {t.get('title')}"
    return s if len(s) <= 60 else s[:57] + "..."


# ------------------------------------------------------------- tray menu
def restart(icon):
    args = ["--quiet", "--restart"] + (["--show"] if WINDOW.get("visible") else [])
    subprocess.Popen(relaunch_cmd(*args), creationflags=NO_WINDOW)
    icon.stop()
    os._exit(0)


def build_menu():
    def pick(name):
        def handler(icon, item):
            cfg = load_cfg()
            cfg["audio_device"] = name
            save_cfg(cfg)
            restart(icon)
        return handler

    def is_current(name):
        def check(item):
            cur = load_cfg().get("audio_device")
            if name is None:
                return cur in (None, "")
            if name == "phone" or str(cur).lower() == "phone":
                return str(cur).lower() == name
            return bool(cur) and str(cur).lower() in name.lower()
        return check

    mics = [pystray.MenuItem("Phone (the display phone's mic)", pick("phone"), checked=is_current("phone"), radio=True),
            pystray.MenuItem("Windows default", pick(None), checked=is_current(None), radio=True)]
    for n in mic_choices():
        mics.append(pystray.MenuItem(n, pick(n), checked=is_current(n), radio=True))

    def update_label(item):
        u = APP.get("app", {}).get("update") if APP.get("app") else None
        return f"Update to {u['version']}" if u else "Check for updates"

    def update_action(icon, item):
        app, loop = APP.get("app"), APP.get("loop")
        if not app:
            return
        import asyncio
        if app.get("update"):
            asyncio.run_coroutine_threadsafe(core.install_update(app), loop)
            try:
                icon.notify("Downloading the update. Liner Notes will reopen in a minute.", APP_NAME)
            except Exception:
                pass
        else:
            fut = asyncio.run_coroutine_threadsafe(core.check_for_update(app), loop)
            try:
                found = fut.result(timeout=20)
                icon.notify(f"Version {found['version']} is available. Click the icon menu to install it."
                            if found else "You have the latest version.", APP_NAME)
            except Exception:
                pass

    return pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Liner Notes", lambda i, it: show_window(), default=True),
        pystray.MenuItem("Open in browser", lambda i, it: webbrowser.open(URL)),
        pystray.MenuItem("Use a phone as a screen…", lambda i, it: open_panel("phone")),
        pystray.MenuItem("Microphone", pystray.Menu(*mics)),
        pystray.MenuItem("Start with Windows", lambda i, it: set_startup(not startup_enabled()),
                         checked=lambda it: startup_enabled()),
        pystray.MenuItem("Settings folder", lambda i, it: os.startfile(DATA_DIR)),
        pystray.MenuItem(update_label, update_action, visible=lambda it: core.VERSION != "dev"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )


def refresher(icon):
    last = None
    while True:
        s = status_text()
        if s != last:
            last = s
            icon.title = f"{APP_NAME}: {s}"
            try:
                icon.update_menu()
            except Exception:
                pass
        time.sleep(2)


def main():
    quiet = "--quiet" in sys.argv
    restarting = "--restart" in sys.argv
    updated = "--updated" in sys.argv

    if restarting:                       # wait for the old copy to let go of the port
        for _ in range(40):
            if not port_in_use():
                break
            time.sleep(0.25)
    elif port_in_use():                  # already running: ask it to show its window
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(f"{URL}/api/window", data=b"{}", method="POST"), timeout=3)
        except Exception:
            webbrowser.open(URL)
        return

    migrate_startup_entry()
    first_flag = os.path.join(DATA_DIR, ".first_run_done")
    first_run = not os.path.exists(first_flag)
    make_source_shortcut()
    threading.Thread(target=server_thread, daemon=True).start()
    threading.Thread(target=adb_loop, daemon=True).start()

    icon = pystray.Icon("LinerNotes", make_image(), APP_NAME, build_menu())
    ICON["i"] = icon
    icon.run_detached()
    icon.visible = True
    threading.Thread(target=refresher, args=(icon,), daemon=True).start()
    if first_run:
        open(first_flag, "w").close()
        def hello():
            time.sleep(3)
            try:
                icon.notify("Closing the window keeps Liner Notes running by the clock. Right-click the record icon for options.",
                            APP_NAME)
            except Exception:
                pass
        threading.Thread(target=hello, daemon=True).start()

    show = updated or "--show" in sys.argv or not (quiet or restarting)
    if not run_window(show):
        # No built-in window on this PC: fall back to the default browser and keep running in the tray
        if show and wait_for_server():
            webbrowser.open(URL)
        while True:
            time.sleep(3600)
    quit_app(icon)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        message(f"{APP_NAME} hit an error and closed.\n\nDetails are in {LOG}")
