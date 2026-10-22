"""Write app/_secrets.py from the build's environment. Values are trimmed (a pasted key often
carries a trailing space or newline) and written with repr(), so nothing in them can break the file."""
import os

NAMES = ["DISCOGS_KEY", "DISCOGS_SECRET", "SPOTIFY_CLIENT_ID", "LASTFM_KEY", "LASTFM_SECRET"]
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "..", "app", "_secrets.py"), "w", encoding="utf-8") as f:
    for n in NAMES:
        f.write(f"{n} = {os.environ.get(n, '').strip()!r}\n")
for n in NAMES:
    print(f"{n}: {'set' if os.environ.get(n, '').strip() else 'MISSING'}")
