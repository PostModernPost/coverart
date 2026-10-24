# Liner Notes: notes for Claude

Liner Notes is a free app by Adam Waitt (GitHub: PostModernPost). It listens to records, CDs and tapes playing
in the room, and to Spotify and other apps on the PC. It shows the cover, lyrics, credits, the band's story and
similar bands on the PC, or on a phone as a second screen. Website: https://liner-notes.app (deployed from `site/` by Vercel).

Adam isn't a programmer. Explain things in plain words, give him exact clicks for anything he has to do, and
just do the work (build, commit, push, release) without asking at each step. He writes without em dashes and
wants replies short and direct.

## How it fits together

- `app/core.py`: the engine (Python, aiohttp server on port 8765). It handles listening (`turntable_loop` /
  `_turntable_inner`), Shazam, following a record's tracklist (`RecordSession`), metadata (iTunes, Discogs,
  LRCLIB lyrics, Wikipedia, Last.fm), history and shelf (`History`, SQLite `history.db`), Spotify, Last.fm,
  Discogs collection sync, phone pairing, updates, and every `/api/...` route (`build_app`).
- `app/display.html`: the whole UI in one page (PC window, phone browser, and the Android app's WebView).
  Settings are per screen (localStorage) except the `/api/config` ones.
- `app/main.py`: the Windows wrapper (pywebview window, tray icon, sleep/wake).
- `app/shazam_lite.py`: Shazam recognition in plain Python (numpy). Used on Android, where shazamio's
  compiled core doesn't install. `core.shazam_client()` picks shazamio or this.
- `app/android_main.py`: starts the engine inside the Android app (`LINERNOTES_ENGINE=android`, port 8766).
- Data lives in `%APPDATA%\Liner Notes` (config.json, history.db, linernotes.log, record.json).
  When Adam reports a problem, read `linernotes.log` there first.

### Android app (`android/`)

Kotlin. It's a WebView that shows either the PC's page or the engine running on the phone itself (Chaquopy, Python 3.11).
- `MainActivity.kt`: startup and switching. It works on its own by default. It switches to a PC it trusts over USB
  (`127.0.0.1:8765`, via `adb reverse`, checked every 5 s) or Wi-Fi (NSD `_linernotes._tcp`, checked every
  2 min; a new PC gets a one-time pairing request it has to approve). It goes back to working on its own when the PC is gone for about a minute.
  The JS bridge `window.LinerNotesApp` covers the screen, sharing, updates, mode, the log and switching.
- `LinkService.kt`: foreground service. It polls `/api/now`, records the phone mic and uploads it to `/api/audio`,
  and wakes the screen for the next song.
- `Engine.kt`: starts the Python engine; `Updater.kt`: self-update for the GitHub ("direct") build.
- `Link.kt`: HTTP, pairing tokens, NSD `Finder`, `Pairing`.
- Flavors: `direct` (GitHub APK, updates itself, REQUEST_INSTALL_PACKAGES) and `play` (Play Store AAB).
- Liner Notes Plus: the `play` copy sells an unlock through Google Play Billing (`src/play/.../Pro.kt`: subscription
  `liner_notes_plus` with base plans `monthly` $1 and `yearly` $10, one-time `liner_notes_plus_lifetime` $25). `direct`
  (`src/direct/.../Pro.kt`) is always unlocked, as are the PC and browsers. The page asks `LinerNotesApp.pro()`;
  `isPro()` in display.html gates the song page, shelf, saved, stats, recap, lyrics beside the cover, the side-end
  pulse and the accounts, and `LinkService.wakeScreen` checks `Pro.unlocked`. The Play copy shows no donation links.
- Store listing text and images: `android/store/`.
- aiohttp and its helpers are installed as plain-Python wheels (the workflow builds them with `*_NO_EXTENSIONS=1`
  into `android/wheels`). certifi supplies SSL certificates (`extractPackages("certifi")`).

## Releasing

- `VERSION` holds the version. Bumping it and pushing to `main` releases the Windows app:
  `.github/workflows/build.yml` runs PyInstaller and Inno Setup, Azure Artifact Signing signs it, and it's published as GitHub release `v<VERSION>`
  with `LinerNotes-Setup-<v>.exe` and `LinerNotes-Setup.exe`. The installed app offers the update.
- The Android build runs only when started: `.github/workflows/android.yml` (Actions > Run workflow, or
  `gh api -X POST repos/PostModernPost/linernotes/actions/workflows/android.yml/dispatches -f ref=main`).
  It takes about 15 minutes, so batch Android changes. It publishes to the rolling prerelease `android`
  (`LinerNotes-Android.apk`, which the app checks for updates).
- The Android app has its own version in `ANDROID_VERSION` (1.0.x), separate from the Windows `VERSION` (0.7.x).
  Bump `ANDROID_VERSION` before each Android build, or phones won't see the update. The engine inside the app keeps
  the Windows number (Settings > About on the phone shows both). An Android-only change doesn't need a `VERSION` bump.
  Permanent link: https://github.com/PostModernPost/linernotes/releases/download/android/LinerNotes-Android.apk
- Version numbers: the last number goes up with each release. Suggest to Adam (don't just do it) when a
  bigger step is due: the middle number for a noticeable new feature or a batch of them (0.8 -> 0.9), on
  either app. The PC app stays below 1.0.0 until Adam says it's ready to advertise; Android is already 1.x.
- Commit message style: what changed for the user, with `(0.7.xx)` at the end, or `(Android 1.0.x)` for Android-only changes.

## Secrets (never put them in code, commits or replies)

The GitHub repo secrets are DISCOGS_KEY/SECRET, SPOTIFY_CLIENT_ID, LASTFM_KEY/SECRET, six AZURE_* for signing,
and ANDROID_KEYSTORE_B64/PASSWORD. The builds write them into `app/_secrets.py`, which is gitignored.
config.json on Adam's PC holds his Discogs token, so don't print it.

## Adam's setup

- Windows desktop (ASUS ROG). The turntable runs through a preamp into the rear Mic In ("Rear Mic (Realtek USB
  Audio)", input volume about 25, enhancements off). It's on wired ethernet with Surfshark VPN ("Invisible on LAN" off).
- An old Android phone used as the second screen, with USB debugging on.
- Google Play Console account: not set up yet ($25, ID check). Closed testing needs 12 testers for 14 days.

## Open items

- The Android app working on its own hasn't been confirmed to recognize music on a real phone yet. Next step:
  watch `adb logcat` (tag `python.stdout`) or use the in-app Settings > About > Send the log.
- Spotify login on the phone working on its own needs `http://127.0.0.1:8766/api/spotify/callback` added as a Redirect URI
  on the Spotify developer app.
- The phone and the PC keep separate history and shelf (syncing them is a future project).
- See ROADMAP.md for features.
