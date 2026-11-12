import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["LANGUAGE"] = "en"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as audio


class UrlTests(unittest.TestCase):
    def test_accepts_youtube_hosts(self):
        self.assertEqual(audio.parse_url("https://youtu.be/abc"), "https://youtu.be/abc")
        self.assertTrue(audio.parse_url("https://www.youtube.com/watch?v=abc").startswith("https://www.youtube.com/"))

    def test_rejects_other_hosts(self):
        for url in ("https://example.com/watch?v=abc", "ftp://youtube.com/x", ""):
            with self.assertRaises(ValueError):
                audio.parse_url(url)

    def test_rejects_playlist_only_links(self):
        with self.assertRaises(ValueError):
            audio.parse_url("https://www.youtube.com/playlist?list=PL123")


class MetadataTests(unittest.TestCase):
    def test_date_falls_back(self):
        self.assertEqual(audio.release_date({"release_date": "2024-05-01"}), "2024-05-01")
        self.assertEqual(audio.release_date({"upload_date": "20240501"}), "2024-05-01")
        self.assertEqual(audio.release_date({"release_year": "2024"}), "2024")
        self.assertEqual(audio.release_date({}), "")

    def test_artist_from_title(self):
        title, artist, _, _ = audio.metadata({"title": "Nina Simone - Feeling Good"})
        self.assertEqual((title, artist), ("Feeling Good", "Nina Simone"))

    def test_uploader_fallback_and_noise(self):
        title, artist, _, _ = audio.metadata({"title": "Song (Official Video)", "uploader": "Chan"})
        self.assertEqual((title, artist), ("Song", "Chan"))

    def test_filename_is_safe(self):
        name = audio.filename("AC/DC", "Live: 1979?", "mp3")
        self.assertTrue(name.endswith(".mp3"))
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertEqual(audio.filename("", "", "mp3"), "audio.mp3")


class PurgeTests(unittest.TestCase):
    def setUp(self):
        with audio.jobs_lock:
            audio.jobs.clear()

    def tearDown(self):
        with audio.jobs_lock:
            audio.jobs.clear()

    def test_expired_ready_job_is_removed(self):
        tmp = tempfile.mkdtemp(prefix="audio-drop-test-")
        Path(tmp, "file.mp3").write_bytes(b"x")
        with audio.jobs_lock:
            audio.jobs["old"] = {"status": "ready", "created": time.time() - audio.JOB_TTL - 1, "tmp": tmp}
        audio.purge()
        self.assertNotIn("old", audio.jobs)
        self.assertFalse(Path(tmp).exists())

    def test_fresh_job_is_kept(self):
        with audio.jobs_lock:
            audio.jobs["new"] = {"status": "ready", "created": time.time(), "tmp": None}
        audio.purge()
        self.assertIn("new", audio.jobs)

    def test_stuck_running_job_is_removed(self):
        with audio.jobs_lock:
            audio.jobs["stuck"] = {
                "status": "running",
                "created": time.time() - audio.MAX_DURATION - audio.JOB_TTL - 1,
                "tmp": None,
            }
        audio.purge()
        self.assertNotIn("stuck", audio.jobs)


class ConvertTests(unittest.TestCase):
    def setUp(self):
        with audio.jobs_lock:
            audio.jobs.clear()
        self.client = audio.app.test_client()

    def tearDown(self):
        with audio.jobs_lock:
            audio.jobs.clear()

    def test_bad_url_is_rejected(self):
        response = self.client.post("/api/convert", data={"url": "https://example.com/x"})
        self.assertEqual(response.status_code, 400)

    def test_bad_format_is_rejected(self):
        response = self.client.post("/api/convert", data={"url": "https://youtu.be/abc", "format": "wav"})
        self.assertEqual(response.status_code, 400)

    def test_valid_request_queues_a_job(self):
        with mock.patch.object(audio, "run_job"):
            response = self.client.post("/api/convert", data={"url": "https://youtu.be/abc", "format": "mp3"})
        self.assertEqual(response.status_code, 202)
        self.assertIn(response.get_json()["id"], audio.jobs)

    def test_full_queue_returns_429(self):
        with audio.jobs_lock:
            for index in range(audio.MAX_JOBS):
                audio.jobs[f"j{index}"] = {"status": "queued", "created": time.time()}
        response = self.client.post("/api/convert", data={"url": "https://youtu.be/abc", "format": "mp3"})
        self.assertEqual(response.status_code, 429)


class RequestTests(unittest.TestCase):
    def test_real_ip_wins_over_forwarded_for(self):
        with audio.app.test_request_context(
            "/", headers={"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9"}
        ):
            self.assertEqual(audio.client_ip(), "1.2.3.4")

    def test_health_and_config(self):
        client = audio.app.test_client()
        self.assertEqual(client.get("/healthz").data, b"ok")
        self.assertEqual(client.get("/api/config").get_json(), {"language": "en", "expert": False})


if __name__ == "__main__":
    unittest.main()
