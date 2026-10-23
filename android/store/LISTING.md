# Liner Notes on Google Play

Everything to paste into Play Console. Images are in this folder.

## Main store listing

**App name:** Liner Notes

**Short description (80 max):**
Album art, lyrics and credits for the record on your turntable, on your phone.

**Full description:**
Liner Notes turns your phone into the screen by your turntable.

Put a record on. Liner Notes on your PC hears it, names the song, and your phone shows the cover, the lyrics and who played on it. It follows the record song by song, side by side, and tells you when to flip.

The phone app connects to Liner Notes on your Windows PC (free at liner-notes.app), over your home Wi-Fi or a USB cable.

- The cover, song, side and track, big enough to read from the couch
- Timed lyrics that scroll along with the song
- The band's story, full credits from Discogs, and similar bands
- Your shelf: every record you play, synced with your Discogs collection
- Use the phone's microphone to listen right by the speakers
- The screen wakes for the next record and goes dark when nothing is playing
- Share the song you're hearing with a link that opens in any music app

Free, no ads, no account. Your listening stays on your own PC.

Requires Liner Notes for Windows, running on a PC on the same network.

**App icon:** icon-512.png
**Feature graphic:** feature-graphic.png
**Phone screenshots:** play-1-now.png, play-2-lyrics.png, play-3-song.png, play-4-last.png, play-5-shelf.png

**Category:** Music & Audio
**Contact email:** your email
**Website:** https://liner-notes.app
**Privacy policy:** https://liner-notes.app/privacy

## App content answers

- **Ads:** No ads.
- **App access:** All features need Liner Notes running on a Windows PC on the same network. Reviewers can't reach one, so say: "This app is a display for the Liner Notes desktop app (free at https://liner-notes.app). Without it the app shows a setup screen explaining how to connect." Choose "All or some functionality is restricted" and paste that.
- **Content rating:** Questionnaire: category "Utility, Productivity, Communication, or Other"; answer No to everything (no violence, user content, etc.). Lyrics come from public lyric sites, so answer the "crude humor / profanity" question No unless they object.
- **Target audience:** 13 and over (or 18+; nothing is aimed at children).
- **News app:** No.
- **Government app:** No.
- **Financial features:** None.
- **Health:** None.

## Data safety

- **Does your app collect or share any of the required user data types?** Yes.
- **Is all of the user data collected by your app encrypted in transit?** No (it goes to your own PC over your home network without encryption). Answer honestly: No.
- **Do you provide a way for users to request that their data is deleted?** Yes: the data lives on the user's own PC and they can delete it there; disconnecting the phone in Settings removes its key.
- **Data types:**
  - **Audio > Voice or sound recordings:** Collected (sent off the phone to the user's own PC), not shared. Processed ephemerally: yes. Required or optional: optional (only when the PC is set to listen through the phone). Purpose: App functionality.
  - Nothing else (no location, contacts, identifiers, analytics or crash data).

## Permissions declarations

- **Foreground service, microphone:** "When the user sets Liner Notes on their PC to listen through this phone, the app records with the phone's microphone and sends the audio to the user's own PC to recognize the record playing. It runs while the screen is off so recognition continues through a whole record side. A notification is shown the whole time."
- **Foreground service, special use:** "Keeps this phone's now-playing display in sync with the Liner Notes app on the user's PC while the screen is off, and turns the screen on when the next song starts. It polls the user's own PC on the local network about once a second. A notification is shown the whole time." Google may ask for a short video: record the phone screen going dark, then waking when a song starts.

## Releasing

1. Internal testing: upload `LinerNotes-Android-<version>.aab` from the GitHub release. Add yourself as a tester.
2. Closed testing (required for new personal accounts): at least 12 testers opted in for 14 days in a row. Ask friends with Android phones to join via the opt-in link.
3. Then apply for production access in Play Console.
