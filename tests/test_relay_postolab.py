"""Offline: captions rules + full relay flow with fake Gemini, fake Postolab, real ffmpeg video."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import captions as cap  # noqa: E402
import fb_reels_rss as gen  # noqa: E402
import postolab_relay as relay  # noqa: E402
from video_checker.config import Settings  # noqa: E402
from video_checker.gemini import GeminiReview  # noqa: E402
from video_checker.pipeline import check_video  # noqa: E402

TMP = tempfile.mkdtemp()


def video(name, w, h, secs=5):
    p = os.path.join(TMP, name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}:sample_rate=48000",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", p], check=True)
    return p


HD = video("hd.mp4", 720, 1280)
SD = video("sd.mp4", 356, 634)


class TestCaptions(unittest.TestCase):
    def test_clean_and_finalize(self):
        self.assertEqual(cap.clean_body('"Katanya adik ni ada...see-moo.re"'), "Katanya adik ni ada")
        self.assertEqual(cap.clean_body("Dpt info laki ni dah..."), "Dpt info laki ni dah")
        self.assertEqual(cap.finalize("Katanya adik ni ada"), "Katanya adik ni ada...see-moo.re")

    def test_rules(self):
        self.assertEqual(cap.problems("Zaman kita dulu, budak ni akan"), [])
        self.assertTrue(cap.problems("satu dua tiga empat lima enam tujuh lapan"))
        self.assertTrue(cap.problems("Dpt info laki ni curi"))
        self.assertTrue(cap.problems("Budak melayu ni rupanya"))
        self.assertTrue(cap.problems("Padan muka dia"))
        self.assertTrue(cap.problems("Tengok ni 😂"))
        self.assertTrue(cap.problems("Share kalau setuju"))
        self.assertTrue(cap.problems("#viral budak ni"))

    def test_unique_and_retry(self):
        rounds = iter([
            {"captions": ["Katanya adik ni ada", "Katanya adik ni ada", "Laki ni curi motor"]},
            {"captions": ["Dpt info dorg ni rupanya", "Staff ni mrh sbb"]},
        ])
        out, rej = cap.generate(None, 3, "asal", ["Zaman kita dulu, budak ni akan"], lambda p: next(rounds))
        self.assertEqual(out, ["Katanya adik ni ada...see-moo.re", "Dpt info dorg ni rupanya...see-moo.re",
                               "Staff ni mrh sbb...see-moo.re"])
        self.assertTrue(any("serupa" in r for r in rej) and any("curi" in r for r in rej))

    def test_history_blocks_repeat(self):
        out, _ = cap.generate(None, 1, "", ["Katanya adik ni ada"],
                              lambda p: {"captions": ["Katanya adik ni ada"]}, rounds=1)
        self.assertEqual(out, [])


class FakePostolab:
    def __init__(self):
        self.uploads, self.calls = 0, []

    def pages(self):
        return [{"page_id": "111", "page_name": "Penmerah"}, {"page_id": "222", "page_name": "Page B"},
                {"page_id": "333", "page_name": "Page C"}]

    def upload(self, path):
        assert os.path.getsize(path) > 0
        self.uploads += 1
        return "https://pub-x.r2.dev/external/1.mp4"

    def schedule(self, url, sid, posts, profile, stagger):
        self.calls.append((url, sid, posts, profile, stagger))
        return {"created": [{"pageId": p["pageId"], "id": i, "scheduled_at": f"2026-10-20T{6+i}:00:00+08:00"}
                            for i, p in enumerate(posts)]}


def fake_checker(review):
    def fn(path, caption, target, settings, layers=(1, 2, 3, 4)):
        assert 2 not in layers                       # no AudD token in tests
        return check_video(path, caption, target, settings, layers=layers,
                           overrides={"gemini_review": lambda p, c, s: review})
    return fn


def fake_ask_factory(captions_list):
    def factory(settings, path):
        return (lambda prompt: {"captions": captions_list}), (lambda: None)
    return factory


class TestRelay(unittest.TestCase):
    def setUp(self):
        self.cfg = relay.Cfg()
        self.cfg.source_map = {"penmerahdotcoms": {"111"}}
        self.cfg.profile = "deli"
        self.settings = Settings()
        self.settings.visual_provider = "gemini"
        self.settings.gemini_api_key = "x"
        self.settings.audd_api_token = ""
        self.api = FakePostolab()

    def item(self, src_path):
        dl = relay.download
        relay.download = lambda url, dest, session=None: shutil.copy(src_path, dest)
        self.addCleanup(lambda: setattr(relay, "download", dl))
        return {"id": "2324759198267426", "url": "u", "mp4": "https://video.fbcdn/x.mp4",
                "source": "penmerahdotcoms", "caption": "Gile 14K siaw nak kena bayar malam ni"}

    def test_green_posts_to_other_pages_with_different_captions(self):
        state = {"reels": {}, "captions": []}
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings, state, self.api,
                                 checker=fake_checker(GeminiReview()),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya staff ni mrh sbb",
                                                               "Zaman kita dulu, budak ni akan"]),
                                 log=lambda m: None)
        self.assertEqual(rec["status"], "posted", rec)
        url, sid, posts, profile, stagger = self.api.calls[0]
        self.assertEqual(profile, "deli")
        self.assertEqual([p["pageId"] for p in posts], ["222", "333"])          # source page 111 excluded
        self.assertEqual(posts[0]["message"], "Dpt info laki ni dah...see-moo.re")
        self.assertNotEqual(posts[0]["message"], posts[1]["message"])
        self.assertEqual(len(state["captions"]), 2)

    def test_red_not_posted(self):
        rv = GeminiReview(visual=[{"category": "nudity_sexual", "severity": "block", "t": 2.0,
                                   "confidence": 95, "description": "bogel"}])
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(rv),
                                 ask_factory=fake_ask_factory(["x"]))
        self.assertEqual(rec["status"], "red")
        self.assertEqual(self.api.calls, [])
        self.assertTrue(any("Bogel" in r for r in rec["reasons"]))

    def test_sd_video_is_yellow_and_skipped(self):
        rec = relay.process_reel(self.item(SD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(GeminiReview()),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(rec["status"], "yellow")
        self.assertEqual(self.api.calls, [])

    def test_yellow_post_mode(self):
        self.cfg.yellow_mode = "post"
        rec = relay.process_reel(self.item(SD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(GeminiReview()),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(rec["status"], "posted")

    def test_dry_run_does_not_upload(self):
        self.cfg.dry_run = True
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(GeminiReview()),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(self.api.uploads, 0)
        self.assertEqual(len(rec["scheduled"]), 2)

    def test_feed_parse_roundtrip(self):
        r = gen.Reel(id="2324759198267426", url="https://www.facebook.com/reel/2324759198267426/",
                     caption="Gile 14K\nbaris dua", mp4="https://v/x.mp4?a=1&b=2", first_seen="x",
                     source_page="https://www.facebook.com/penmerahdotcoms/reels/")
        items = relay.parse_feed(gen.build_rss("semua", "", [r]))
        self.assertEqual(items[0]["source"], "penmerahdotcoms")
        self.assertEqual(items[0]["mp4"], "https://v/x.mp4?a=1&b=2")
        self.assertEqual(items[0]["caption"], "Gile 14K\nbaris dua")

    def test_report_renders(self):
        state = {"reels": {"1": {"status": "posted", "summary": "ok", "at": "2026-10-20T01:00",
                                 "scheduled": [{"page": "B", "message": "<x>", "when": "t"}]}}}
        self.assertIn("&lt;x&gt;", relay.render_report(state))


if __name__ == "__main__":
    unittest.main()
