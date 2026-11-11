# audio-drop

Paste a YouTube link, get a tagged audio file. Title, artist and cover are written into the file.

## Run

```sh
docker compose up -d --build
```

Listens on `127.0.0.1:8090`. Caddy:

```
audio.twh0.de {
    reverse_proxy 127.0.0.1:8090
}
```

## Notes

- Formats: original (source codec, no re-encode), MP3, M4A, Opus.
- No auth. Anyone with the URL can convert; put Cloudflare Access or Caddy basic auth in front if that matters.
- yt-dlp goes stale when YouTube changes. `docker compose build --pull` now and then.
- If YouTube asks for a sign-in check, mount a `cookies.txt` read-only and set `COOKIES_FILE` to its path.
