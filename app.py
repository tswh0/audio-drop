import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, abort, jsonify, request, send_file
from mutagen.flac import Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TDRC, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

app = Flask(__name__, static_folder="static", static_url_path="")
app.logger.setLevel(logging.INFO)
app.logger.propagate = False

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
MAX_DURATION = int(os.environ.get("MAX_DURATION", 3 * 3600))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", 2))
MAX_JOBS = int(os.environ.get("MAX_JOBS", 20))
JOB_TTL = 30 * 60
COOKIES = os.environ.get("COOKIES_FILE", "")

LANGUAGE = os.environ.get("LANGUAGE", "de").strip().lower()
if LANGUAGE not in {"de", "en"}:
    LANGUAGE = "de"
EXPERT = os.environ.get("EXPERT_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

MESSAGES = {
    "de": {
        "bad_url": "Bitte einen YouTube-Link einfügen.",
        "playlist": "Playlists werden nicht unterstützt, bitte einen einzelnen Videolink verwenden.",
        "live": "Livestreams werden nicht unterstützt.",
        "too_long": "Das Video ist länger als {hours} Stunden.",
        "bad_format": "Bitte ein unterstütztes Format wählen.",
        "bad_quality": "Bitte eine unterstützte Qualität wählen.",
        "busy": "Gerade ausgelastet, bitte in einer Minute erneut versuchen.",
        "failed": "Konvertierung fehlgeschlagen.",
        "meta_required": "Titel und Künstler dürfen nicht leer sein.",
        "meta_long": "Titel, Künstler und Album dürfen höchstens 200 Zeichen lang sein.",
        "queued": "In der Warteschlange",
        "reading": "Video wird gelesen",
        "downloading": "Audio wird geladen",
        "converting": "Wird konvertiert",
        "tagging": "Tags werden geschrieben",
        "ready": "Fertig",
    },
    "en": {
        "bad_url": "Paste a YouTube video link.",
        "playlist": "Playlists are not supported, use a single video link.",
        "live": "Live streams are not supported.",
        "too_long": "Video is longer than {hours} hours.",
        "bad_format": "Pick a supported format.",
        "bad_quality": "Pick a supported quality.",
        "busy": "Busy right now, try again in a minute.",
        "failed": "Conversion failed.",
        "meta_required": "Title and artist must not be empty.",
        "meta_long": "Title, artist and album must be 200 characters or less.",
        "queued": "Queued",
        "reading": "Reading video",
        "downloading": "Downloading audio",
        "converting": "Converting",
        "tagging": "Writing tags",
        "ready": "Ready",
    },
}


def t(key, **kwargs):
    return MESSAGES[LANGUAGE][key].format(**kwargs)


ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
QUALITIES = {
    "mp3": (320, 256, 192, 128),
    "m4a": (256, 192, 128),
    "opus": (192, 160, 128, 96),
}
MIME = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "opus": "audio/ogg"}

TITLE_NOISE = re.compile(
    r"(?:\s*[\(\[](?=[^)\]]*(?:official|lyrics?|audio|video|visuali[sz]er|hd|4k|mv|remaster))[^)\]]*[\)\]])+\s*$",
    re.I,
)
TITLE_SPLIT = re.compile(r"\s+[-\u2013\u2014]\s+")
INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
TAG_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

jobs = {}
jobs_lock = threading.Lock()
slots = threading.BoundedSemaphore(MAX_CONCURRENT)


def update(jid, **fields):
    with jobs_lock:
        jobs[jid].update(fields)


def purge():
    now = time.time()
    with jobs_lock:
        stale = [
            jid
            for jid, job in jobs.items()
            if (job["status"] in {"ready", "error"} and now - job["created"] > JOB_TTL)
            or now - job["created"] > MAX_DURATION + JOB_TTL
        ]
        for jid in stale:
            job = jobs.pop(jid)
            if job.get("tmp"):
                shutil.rmtree(job["tmp"], ignore_errors=True)


def janitor():
    while True:
        time.sleep(60)
        purge()


threading.Thread(target=janitor, daemon=True).start()


def clean_error(exc):
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    text = text.splitlines()[-1] if text else ""
    text = re.sub(r"^ERROR:\s*", "", text)
    return (text or t("failed"))[:300]


def clean_tag(value):
    text = TAG_CONTROL.sub(" ", value or "")
    return re.sub(r"\s+", " ", text).strip()


def client_ip():
    for header in ("X-Real-IP", "CF-Connecting-IP", "X-Forwarded-For"):
        for part in request.headers.get(header, "").split(","):
            ip = re.sub(r"[^0-9A-Fa-f:.]", "", part.strip())[:64]
            if ip:
                return ip
    return re.sub(r"[^0-9A-Fa-f:.]", "", request.remote_addr or "")[:64] or "-"


def parse_url(raw):
    parsed = urlparse((raw or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ValueError(t("bad_url"))

    query = parse_qs(parsed.query)
    video_path = parsed.path.startswith(("/shorts/", "/live/", "/embed/"))
    short_link = host in {"youtu.be", "www.youtu.be"}
    if "list" in query and "v" not in query and not video_path and not short_link:
        raise ValueError(t("playlist"))

    return parsed.geturl()


def release_date(info):
    value = str(info.get("release_date") or "")
    if value:
        value = value[:10]
    else:
        uploaded = str(info.get("upload_date") or "")
        if re.fullmatch(r"\d{8}", uploaded):
            value = f"{uploaded[:4]}-{uploaded[4:6]}-{uploaded[6:]}"
        else:
            value = str(info.get("release_year") or "")[:4]
    return value if re.fullmatch(r"\d{4}(-\d{2}){0,2}", value) else ""


def metadata(info):
    title = (info.get("track") or info.get("title") or "Audio").strip()
    artist = (info.get("artist") or "").strip()

    if not artist:
        parts = TITLE_SPLIT.split(title, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            artist, title = parts[0].strip(), parts[1].strip()

    if not artist:
        artist = (info.get("uploader") or info.get("channel") or "YouTube").strip()

    title = TITLE_NOISE.sub("", title).strip() or title
    album = (info.get("album") or "").strip()
    return title, artist, album, release_date(info)


def artwork(info, tmp):
    import requests

    url = info.get("thumbnail")
    if not url:
        thumbs = info.get("thumbnails") or []
        if not thumbs:
            return None
        url = thumbs[-1].get("url")
    if not url:
        return None

    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()

        source = os.path.join(tmp, "cover.bin")
        target = os.path.join(tmp, "cover.jpg")
        Path(source).write_bytes(response.content)
        subprocess.run(
            [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-i",
                source,
                "-vf",
                "scale='min(640,iw)':-2",
                "-frames:v",
                "1",
                "-q:v",
                "4",
                target,
            ],
            check=True,
            timeout=60,
        )
        return Path(target).read_bytes(), "image/jpeg"
    except Exception as exc:
        app.logger.warning("cover failed: %s", exc)
        return None


def probe_codec(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def encode(source, fmt, quality, tmp):
    codec = probe_codec(source)

    if fmt == "original":
        if codec == "opus":
            ext, args = "opus", ["-c:a", "copy"]
        elif codec == "aac":
            ext, args = "m4a", ["-c:a", "copy", "-movflags", "+faststart"]
        else:
            ext, args = "mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]
    elif fmt == "mp3":
        ext, args = "mp3", ["-c:a", "libmp3lame", "-b:a", f"{quality}k"]
    elif fmt == "m4a":
        ext, args = "m4a", ["-c:a", "aac", "-b:a", f"{quality}k", "-movflags", "+faststart"]
    else:
        ext, args = "opus", ["-c:a", "libopus", "-b:a", f"{quality}k", "-vbr", "on"]

    target = os.path.join(tmp, f"audio.{ext}")
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", source, "-vn", "-map", "0:a:0", "-map_metadata", "-1", *args, target],
        check=True,
        timeout=900,
    )
    return target, ext


def write_tags(path, ext, title, artist, album, date, url, cover):
    if ext == "mp3":
        tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))
        if date:
            tags.add(TDRC(encoding=3, text=date))
        if url:
            tags.add(COMM(encoding=3, lang="eng", desc="Source", text=url))
        if cover:
            tags.add(APIC(encoding=3, mime=cover[1], type=3, desc="Cover", data=cover[0]))
        tags.save(path, v2_version=3)
    elif ext == "m4a":
        audio = MP4(path)
        audio["\xa9nam"] = [title]
        audio["\xa9ART"] = [artist]
        if album:
            audio["\xa9alb"] = [album]
        else:
            audio.pop("\xa9alb", None)
        if date:
            audio["\xa9day"] = [date]
        else:
            audio.pop("\xa9day", None)
        if url:
            audio["\xa9cmt"] = [url]
        else:
            audio.pop("\xa9cmt", None)
        if cover:
            image_format = MP4Cover.FORMAT_PNG if cover[1] == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(cover[0], imageformat=image_format)]
        audio.save()
    else:
        audio = OggOpus(path)
        audio["title"] = [title]
        audio["artist"] = [artist]
        if album:
            audio["album"] = [album]
        else:
            audio.pop("album", None)
        if date:
            audio["date"] = [date]
        else:
            audio.pop("date", None)
        if url:
            audio["comment"] = [url]
        else:
            audio.pop("comment", None)
        if cover:
            picture = Picture()
            picture.type = 3
            picture.mime = cover[1]
            picture.desc = "Cover"
            picture.data = cover[0]
            audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode()]
        audio.save()


def filename(artist, title, ext):
    name = f"{artist} - {title}" if artist else title
    name = INVALID.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return f"{name[:140].rstrip(' .-') or 'audio'}.{ext}"


def fetch(url, jid, tmp):
    import yt_dlp

    def progress(event):
        if event.get("status") != "downloading":
            return
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        if total:
            update(jid, progress=min(70, int(70 * event.get("downloaded_bytes", 0) / total)))

    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "playlist_items": "1",
        "outtmpl": os.path.join(tmp, "source.%(ext)s"),
        "cachedir": "/tmp/yt-cache",
        "progress_hooks": [progress],
    }
    if COOKIES and os.path.isfile(COOKIES):
        options["cookiefile"] = COOKIES

    with yt_dlp.YoutubeDL(options) as ydl:
        update(jid, phase=t("reading"))
        info = ydl.extract_info(url, download=False)

        if info.get("_type") == "playlist":
            raise ValueError(t("playlist"))
        if info.get("is_live"):
            raise ValueError(t("live"))
        if (info.get("duration") or 0) > MAX_DURATION:
            raise ValueError(t("too_long", hours=MAX_DURATION // 3600))

        update(jid, phase=t("downloading"), progress=0)
        return ydl.process_ie_result(info, download=True)


def run_job(jid, url, fmt, quality):
    tmp = None

    try:
        with slots:
            tmp = tempfile.mkdtemp(prefix="audio-drop-")
            update(jid, tmp=tmp, status="running")
            info = fetch(url, jid, tmp)
            source = info["requested_downloads"][0]["filepath"]

            update(jid, phase=t("converting"), progress=75)
            target, ext = encode(source, fmt, quality, tmp)

            update(jid, phase=t("tagging"), progress=90)
            title, artist, album, date = metadata(info)
            cover = artwork(info, tmp)
            write_tags(target, ext, title, artist, album, date, url, cover)

            update(
                jid,
                status="ready",
                phase=t("ready"),
                progress=100,
                path=target,
                ext=ext,
                filename=filename(artist, title, ext),
                mime=MIME[ext],
                title=title,
                artist=artist,
                album=album,
                date=date,
                cover=cover,
            )
    except Exception as exc:
        app.logger.warning("job %s failed url=%s: %s", jid, url, clean_error(exc))
        update(jid, status="error", phase="", error=clean_error(exc))
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def send_job(job):
    try:
        size = os.path.getsize(job["path"])
    except OSError:
        size = 0
    app.logger.info(
        'download id=%s ip=%s format=%s quality=%s bytes=%d title=%r artist=%r url=%s',
        job.get("jid"),
        client_ip(),
        job.get("ext"),
        job.get("quality") or "source",
        size,
        job.get("title") or "",
        job.get("artist") or "",
        job.get("url") or "",
    )
    return send_file(
        job["path"],
        as_attachment=True,
        download_name=job["filename"],
        mimetype=job["mime"],
        max_age=0,
    )


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/healthz")
def health():
    return "ok"


@app.get("/api/client")
def api_client():
    return jsonify(
        ip=client_ip(),
        remote=request.remote_addr,
        x_real_ip=request.headers.get("X-Real-IP"),
        cf_connecting_ip=request.headers.get("CF-Connecting-IP"),
        x_forwarded_for=request.headers.get("X-Forwarded-For"),
    )


@app.get("/api/config")
def client_config():
    return jsonify({"language": LANGUAGE, "expert": EXPERT})


@app.post("/api/convert")
def convert():
    url = parse_url(request.form.get("url", ""))
    fmt = request.form.get("format", "mp3")

    if fmt not in {"original", *QUALITIES}:
        raise ValueError(t("bad_format"))

    quality = None
    if fmt != "original":
        if EXPERT and request.form.get("quality"):
            try:
                quality = int(request.form.get("quality"))
            except (TypeError, ValueError):
                raise ValueError(t("bad_quality"))
            if quality not in QUALITIES[fmt]:
                raise ValueError(t("bad_quality"))
        else:
            quality = max(QUALITIES[fmt])

    purge()
    with jobs_lock:
        active = sum(1 for job in jobs.values() if job["status"] in {"queued", "running"})
        if active >= MAX_JOBS:
            return jsonify({"error": t("busy")}), 429

        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {
            "jid": jid,
            "url": url,
            "quality": quality,
            "status": "queued",
            "phase": t("queued"),
            "progress": 0,
            "created": time.time(),
        }

    threading.Thread(target=run_job, args=(jid, url, fmt, quality), daemon=True).start()
    return jsonify({"id": jid}), 202


@app.get("/api/job/<jid>")
def job_status(jid):
    purge()
    with jobs_lock:
        job = jobs.get(jid)
        if not job:
            abort(404)
        return jsonify(
            {
                key: job.get(key)
                for key in ("status", "phase", "progress", "error", "filename", "title", "artist", "album")
            }
        )


@app.get("/api/job/<jid>/file")
def job_file(jid):
    purge()
    with jobs_lock:
        job = jobs.get(jid)
        if not job or job.get("status") != "ready" or not os.path.isfile(job.get("path", "")):
            abort(404)
        return send_job(job)


@app.post("/api/job/<jid>/download")
def job_download(jid):
    purge()
    with jobs_lock:
        job = jobs.get(jid)
        if not job or job.get("status") != "ready" or not os.path.isfile(job.get("path", "")):
            abort(404)

        if EXPERT:
            title = clean_tag(request.form.get("title"))
            artist = clean_tag(request.form.get("artist"))
            album = clean_tag(request.form.get("album"))
            if not title or not artist:
                raise ValueError(t("meta_required"))
            if any(len(value) > 200 for value in (title, artist, album)):
                raise ValueError(t("meta_long"))

            write_tags(
                job["path"],
                job["ext"],
                title,
                artist,
                album,
                job.get("date") or "",
                job.get("url") or "",
                job.get("cover"),
            )
            job["filename"] = filename(artist, title, job["ext"])

        return send_job(job)


@app.errorhandler(ValueError)
def bad_request(exc):
    return jsonify({"error": str(exc)}), 400


@app.after_request
def secure_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response
