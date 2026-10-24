"""Save a web page as a PNG with Microsoft Edge (no window opens).
python shot.py <url> <out.png> <width> <height> [scale] [wait ms]"""
import os
import subprocess
import sys
import tempfile

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def shot(url, out, w, h, scale=1.0, wait=3000):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    profile = tempfile.mkdtemp(prefix="ln-edge-")      # its own profile, so it doesn't touch the user's Edge
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
                    f"--user-data-dir={profile}", f"--window-size={w},{h}", f"--force-device-scale-factor={scale}",
                    "--run-all-compositor-stages-before-draw",
                    f"--screenshot={os.path.abspath(out)}", url],
                   check=True, timeout=120, capture_output=True)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    shot(a[0], a[1], int(a[2]), int(a[3]), float(a[4]) if len(a) > 4 else 1.0, int(a[5]) if len(a) > 5 else 3000)
