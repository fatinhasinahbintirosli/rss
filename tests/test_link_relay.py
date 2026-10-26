import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import link_relay as lr  # noqa: E402
from video_checker.config import Settings  # noqa: E402

TMP = tempfile.mkdtemp()


def make(name, vcodec, ext):
    p = os.path.join(TMP, f"{name}.{ext}")
    acodec = "aac" if ext == "mp4" else "libopus"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=480x854:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", vcodec, "-pix_fmt", "yuv420p",
                    "-c:a", acodec, "-shortest", p], check=True)
    return p


MP4 = make("ok", "libx264", "mp4")
WEBM = make("yt", "libvpx-vp9", "webm")


class FakeApi:
    def __init__(self, skipped=None):
        self.saved, self.calls, self.uploads, self.skipped = [], [], 0, skipped or []

    def save_links(self, items):
        self.saved.extend(items)

    def upload(self, path, content_type="video/mp4"):
        assert os.path.getsize(path) > 0
        self.uploads += 1
        self.types = getattr(self, "types", []) + [content_type]
        return "https://pub-x.r2.dev/external/" + ("s.jpg" if content_type == "image/jpeg" else "v.mp4")

    def schedule(self, url, sid, posts, profile, stagger, slots=None, first_comment=None, comment_image_url=None):
        self.calls.append(dict(sid=sid, posts=posts, profile=profile, stagger=stagger, slots=slots,
                               comment=first_comment, comment_img=comment_image_url))
        if self.skipped:
            return {"created": [], "skipped": self.skipped}
        return {"created": [{"pageId": p["pageId"], "scheduled_at": f"2026-10-26T0{6 + i}:00:00+08:00"}
                            for i, p in enumerate(posts)]}


PAGES = [{"page_id": "1", "page_name": "A"}, {"page_id": "2", "page_name": "B"}, {"page_id": "3", "page_name": "C"}]


def fetcher(path=MP4):
    def f(url, outdir, max_minutes, cookiefile, proxy):
        dst = os.path.join(outdir, "video.mp4")
        shutil.copy(path, dst)
        return {"path": dst, "title": "Tajuk asal", "description": "", "thumb": "https://t/1.jpg", "duration": 2}
    return f


def asker(caps):
    return lambda settings, path: ((lambda prompt: {"captions": caps}), (lambda: None))


class TestLinkRelay(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.settings.gemini_api_key = "x"

    def test_posts_to_selected_pages_with_profile_caption_settings(self):
        api = FakeApi()
        pc = {"target_mode": "selected", "target_page_ids": ["1", "3"], "caption_suffix": "Baca di : tiktok.com",
              "caption_max_words": 10, "caption_short": True}
        item = {"id": "abc", "profile": "deli", "url": "https://youtu.be/x"}
        hist = []
        rec = lr.process_link(item, pc, PAGES, self.settings, hist, api, fetcher=fetcher(),
                              ask_factory=asker(["Budak yang tiba-tiba lari", "Ramai orang terkejut bila"]),
                              log=lambda m: None)
        self.assertEqual(rec["status"], "posted", rec)
        c = api.calls[0]
        self.assertEqual((c["sid"], c["profile"], c["slots"], c["stagger"]), ("link:abc", "deli", {"source": "profile"}, 0))
        self.assertEqual([p["pageId"] for p in c["posts"]], ["1", "3"])
        self.assertEqual(sorted(p["message"] for p in c["posts"]),
                         ["Budak yg tiba² lari Baca di : tiktok.com", "Ramai orang terkejut bila Baca di : tiktok.com"])
        self.assertEqual(rec["thumb_url"], "https://t/1.jpg")
        self.assertEqual(len(hist), 2)

    def test_no_targets_and_already_scheduled(self):
        item = {"id": "1", "profile": "deli", "url": "u"}
        rec = lr.process_link(item, {"target_mode": "selected", "target_page_ids": []}, PAGES, self.settings, [],
                              FakeApi(), fetcher=fetcher(), ask_factory=asker(["x"]))
        self.assertEqual(rec["status"], "error")
        api = FakeApi(skipped=[{"pageId": "1", "reason": "sudah_dijadualkan"}])
        rec = lr.process_link(item, {}, PAGES, self.settings, [], api, fetcher=fetcher(),
                              ask_factory=asker(["Satu dua tiga", "Empat lima enam", "Tujuh lapan sembilan"]),
                              log=lambda m: None)
        self.assertEqual(rec["status"], "posted")          # larian terputus sebelum ini, sudah dalam queue

    def test_run_marks_processing_then_result_and_counts_tries(self):
        api = FakeApi()
        remote = {"settings": {"profiles": {"deli": {}}}, "captions": ["Lama...see-moo.re"],
                  "items": [{"id": "a", "profile": "deli", "url": "u1", "tries": 0},
                            {"id": "b", "profile": "deli", "url": "u2", "tries": 1}]}

        def proc(item, pc, pages, settings, history, api_):
            if item["id"] == "b":
                raise lr.DownloadError("Video ini peribadi atau perlu log masuk.")
            return {"status": "posted", "scheduled": [{"page": "A", "message": "m", "when": "t"}], "detail": {}}
        orig = lr.time.sleep
        lr.time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(lr.time, 'sleep', orig))
        done = lr.run_links(api, remote, PAGES, self.settings, 10, process=proc, log=lambda m: None)
        self.assertEqual([r["status"] for _, r in done], ["posted", "error"])
        self.assertEqual([x["status"] for x in api.saved], ["processing", "posted", "processing", "error"])
        self.assertEqual(api.saved[-1]["tries"], 2)
        self.assertIn("peribadi", api.saved[-1]["error"])

    def test_webm_converted_to_h264_mp4(self):
        d = tempfile.mkdtemp()
        src = shutil.copy(WEBM, os.path.join(d, "video.webm"))
        out = lr.ensure_facebook_mp4(src)
        self.assertTrue(out.endswith(".mp4"))
        codecs = {s["codec_type"]: s["codec_name"] for s in lr._probe(out)["streams"]}
        self.assertEqual(codecs, {"video": "h264", "audio": "aac"})
        self.assertEqual(lr.ensure_facebook_mp4(MP4), MP4)            # sudah sesuai → tidak ditukar

    def test_page_fallback_finds_video(self):
        class R:
            def __init__(self, t): self.text = t
            def raise_for_status(self): pass

        class S:
            def __init__(self, t): self.t = t
            def get(self, *a, **k): return R(self.t)
        og = '<meta property="og:video" content="https://cdn.x/v.mp4?a=1&amp;b=2"><meta property="og:title" content="Hai">'
        self.assertEqual(lr._page_video_url("u", S(og))[:2], ("https://cdn.x/v.mp4?a=1&b=2", "Hai"))
        thr = '{"video_versions":[{"type":101,"url":"https:\\/\\/scontent.cdninstagram.com\\/v\\/t.mp4?x=1\\u0026y=2"}]}'
        self.assertEqual(lr._page_video_url("u", S(thr))[0], "https://scontent.cdninstagram.com/v/t.mp4?x=1&y=2")
        with self.assertRaises(lr.DownloadError):
            lr._page_video_url("u", S("<html>tiada</html>"))

    def test_first_comment_with_screenshot_and_music_removal(self):
        api = FakeApi()
        removed = []

        def fake_remove(path):
            removed.append(path)
            return path
        item = {"id": "c1", "profile": "deli", "url": "https://www.tiktok.com/@a/video/1",
                "comment_text": "Tonton part 2 sini : https://vt.tiktok.com/ZS9k/", "comment_image": True, "remove_music": True}
        rec = lr.process_link(item, {}, PAGES[:1], self.settings, [], api, fetcher=fetcher(),
                              ask_factory=asker(["Budak ni terus lari bila"]), log=lambda m: None,
                              music_remover=fake_remove)
        self.assertEqual(rec["status"], "posted", rec)
        self.assertEqual(len(removed), 1)
        self.assertTrue(rec["detail"]["music_removed"])
        self.assertEqual(api.types, ["image/jpeg", "video/mp4"])
        c = api.calls[0]
        self.assertEqual((c["comment"], c["comment_img"]), (item["comment_text"], "https://pub-x.r2.dev/external/s.jpg"))
        # tanpa screenshot / tanpa komen
        api2 = FakeApi()
        lr.process_link({**item, "comment_image": False, "remove_music": False}, {}, PAGES[:1], self.settings, [], api2,
                        fetcher=fetcher(), ask_factory=asker(["Ramai orang terkejut bila"]), log=lambda m: None,
                        music_remover=lambda p: 1 / 0)
        self.assertEqual((api2.calls[0]["comment"] is not None, api2.calls[0]["comment_img"], api2.types),
                         (True, None, ["video/mp4"]))
        api3 = FakeApi()
        lr.process_link({"id": "c3", "profile": "deli", "url": "u"}, {}, PAGES[:1], self.settings, [], api3,
                        fetcher=fetcher(), ask_factory=asker(["Pakcik ni tenang je bila"]), log=lambda m: None)
        self.assertEqual((api3.calls[0]["comment"], api3.calls[0]["comment_img"]), (None, None))

    def test_tiktok_watermark_free_format(self):
        fmts = [
            {"format_id": "download", "format_note": "watermarked", "vcodec": "h264", "acodec": "aac", "height": 1024},
            {"format_id": "h264_540p_1", "format_note": "Direct video", "vcodec": "h264", "acodec": "aac", "height": 1024, "tbr": 900},
            {"format_id": "bytevc1_720p", "format_note": "Direct video", "vcodec": "h265", "acodec": "aac", "height": 1280},
        ]
        f, free = lr.pick_tiktok_format(fmts)
        self.assertEqual((f["format_id"], free), ("h264_540p_1", True))
        f, free = lr.pick_tiktok_format(fmts[:1])
        self.assertEqual((f["format_id"], free), ("download", False))
        self.assertTrue(lr.is_tiktok("https://vt.tiktok.com/ZS9kJohaBKkj1-DIuS7/"))
        self.assertTrue(lr.is_tiktok("https://www.tiktok.com/@a/video/1"))
        self.assertFalse(lr.is_tiktok("https://www.youtube.com/shorts/x"))

    def test_remove_music_replaces_audio_track(self):
        d = tempfile.mkdtemp()
        src = shutil.copy(MP4, os.path.join(d, "video.mp4"))

        def sep(wav_in, out_dir):
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, "vocals.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=2",
                            "-ac", "2", out], check=True)
            return out
        out = lr.remove_music(src, separator=sep)
        self.assertTrue(out.endswith("_vocal.mp4"))
        codecs = sorted(s["codec_name"] for s in lr._probe(out)["streams"])
        self.assertEqual(codecs, ["aac", "h264"])

    def test_screenshot(self):
        shot = lr.screenshot(shutil.copy(MP4, os.path.join(tempfile.mkdtemp(), "v.mp4")))
        self.assertTrue(shot.endswith(".jpg") and os.path.getsize(shot) > 1000)

    def test_friendly_errors(self):
        self.assertIn("LINK_COOKIES", lr._friendly("ERROR: Sign in to confirm you're not a bot"))
        self.assertEqual(lr._friendly("ERROR: Unsupported URL: https://x"), "")


if __name__ == "__main__":
    unittest.main()
