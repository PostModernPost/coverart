# Liner Notes

*Formerly Cover Art.*

Shows the album art of whatever is playing, on your screen or an old phone next to your monitor. Tap it for the artist's bio, lyrics, and links to Spotify, YouTube, Bandcamp, Discogs, Genius and Last.fm.

It works with:

- **Anything playing on the PC** (Spotify app, Spotify in a browser, and so on). Windows reports the track, so it's instant and exact.
- **Records, or any music in the room.** The mic sits on standby, taking a 2-second sample every 20 seconds. When it hears music it listens continuously and identifies the song, until the room has been quiet for 20 seconds.

Also:

- **Details**: year, record label and genre under the album name.
- **Lyrics beside the art** (a setting, per screen).
- **Record mode**: tell Liner Notes which record is on the turntable (search Discogs, or scan the barcode with the phone), or let it work the record out after it recognizes two songs in a row. It follows the tracklist song by song, shows "Side A · 2 of 5", fills in songs Shazam doesn't know, and reminds you to flip the record.
- **Search**: when a record can't be identified (small indie releases often aren't in Shazam), tap "Search for it", type what's on the sleeve, and pick the right song.
- **Listening history and stats**: top artists, albums and songs, records vs Spotify, songs per day, recently played. Stored on your PC in `%APPDATA%\Liner Notes\history.db`.

Free. If you enjoy it, you can [buy me a coffee](https://buymeacoffee.com/goodisanoun).

## Install (Windows 10/11)

Download `LinerNotes-Setup-x.y.z.exe` from [Releases](https://github.com/PostModernPost/coverart/releases) and run it. No admin password needed. Liner Notes opens in its own window (no browser needed) and keeps a record icon by the clock. Closing the window keeps it running there:

- **Left-click** the icon to open the window.
- **Right-click** for the microphone choice, Start with Windows, the settings folder, and Quit.

Windows may warn that the installer is from an unknown publisher (it isn't code-signed yet). Click "More info" then "Run anyway".

## Microphone tips

- Webcam and headset mics often have noise cancelling that filters music out as "background noise". Turn it off for the mic you use, or use the phone's mic (below).
- Settings and the log live in `%APPDATA%\Liner Notes`.

## Using a phone as the screen (and mic)

Open Settings (gear icon) > **Use a phone as a second screen**, or right-click the tray icon > **Use a phone as a screen…**. The guide walks through it and checks each step off as it happens: getting the phone ready (USB debugging), installing Google's Android connector with one click, plugging in, opening Liner Notes on the phone, and optionally listening with the phone's microphone.

iPhones connect over Wi-Fi instead (the guide's iPhone tab): turn on sharing, scan the QR code, Add to Home Screen. The iPhone is the screen only; listening stays on the PC's mic. While sharing is on, other devices on your network can view the screen and search, but can't change settings, install updates or use the phone tools.

## Discogs key (record mode)

Record search uses the Discogs API. Builds include the app's own Discogs key when the repo has `DISCOGS_KEY` and `DISCOGS_SECRET` secrets (create an application at discogs.com/settings/developers). Without them, the Records panel asks for a personal Discogs token instead.

## Building from source

```
pip install -r requirements.txt
pythonw app/main.py
```

Every push to `main` builds the installer on GitHub Actions. To publish a release, raise the number in `VERSION` and update `RELEASE_NOTES.md`: that build becomes the release, and installed copies offer the update.

Song recognition uses the unofficial `shazamio` library, which is fine for personal use but could stop working if Shazam changes its service.

## Saving songs to Spotify

The heart saves a song in Liner Notes. To also send it to Spotify, the build needs a `SPOTIFY_CLIENT_ID` repo secret: create an app at developer.spotify.com/dashboard (Web API), add the Redirect URI `http://127.0.0.1:8765/api/spotify/callback`, and copy its Client ID. Spotify limits apps in development mode to a handful of people you add under User Management, so anyone else can paste their own app's Client ID in Settings > Spotify > Use your own Spotify app.

## Last.fm

Scrobbling uses the app's Last.fm API account. Create one at last.fm/api/account/create and add its API key and shared secret as the `LASTFM_KEY` and `LASTFM_SECRET` repo secrets. Builds without them hide the Connect button.
