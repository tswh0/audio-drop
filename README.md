# audio-drop

Paste a YouTube link, get a tagged audio file. Title, artist, date, source link and cover are written into the file. UI is [Oat](https://oat.ink).

## Run

Published image:

```sh
docker compose up -d
```

Build locally instead (dev, port 8091):

```sh
docker compose -f docker-compose.dev.yml up -d --build
```

The published image listens on `127.0.0.1:8090`; the dev container on `127.0.0.1:8091`. If GHCR asks for auth, run `docker login ghcr.io` first.

## Formats

Default is MP3 at 320 kbps. Also available: M4A, Opus and Original (source codec, no re-encode).

Opus stores tags as Vorbis comments on the audio stream, and its length comes from the last Ogg granule. Players that only read ID3/MP4 can show no tags and may estimate a longer duration from file size.

## Options

```sh
LANGUAGE=en EXPERT_MODE=true docker compose up -d
```

- `LANGUAGE` — `de` (default) or `en`, switches the UI and server messages.
- `EXPERT_MODE` — `true` shows the quality selector and lets you edit title, artist and album before download.

## Cookies

YouTube sometimes answers datacenter IPs with "Sign in to confirm you're not a bot".

1. Install the "Get cookies.txt LOCALLY" extension in Chrome or Firefox.
2. Sign in to YouTube, export the cookies for `youtube.com` in Netscape format, save the file as `cookies.txt` beside `docker-compose.yml`.
3. Mount it into the service and point `COOKIES_FILE` at it:

```yaml
services:
  audio-drop:
    volumes:
      - ./cookies.txt:/cookies.txt:ro
    environment:
      COOKIES_FILE: /cookies.txt
```

Cookies are login credentials: keep the file private and out of git. They expire, so export again when downloads start failing.

## Notes

- No auth. Anyone with the URL can convert.
- Every served download logs one line to the container log: id, IP, format, quality, bytes, title, artist, URL.
- yt-dlp is pinned; Renovate opens a PR for new releases. Rebuild after merging.
- `MAX_DURATION` (seconds, default 10800), `MAX_CONCURRENT` and `MAX_JOBS` are environment knobs.
