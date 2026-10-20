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

    def schedule(self, url, sid, posts, profile, stagger, slots=None):
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

    def test_allow_warn_turns_accepted_yellow_green(self):
        self.cfg.allow_warn = {"low_resolution", "visual_violence"}
        rv = GeminiReview(visual=[{"category": "violence", "severity": "warn", "t": 1.0, "confidence": 80,
                                   "description": "tampar"}])
        rec = relay.process_reel(self.item(SD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(rv),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(rec["status"], "posted", rec)
        self.assertEqual(rec["accepted"], ["low_resolution", "visual_violence"])

    def test_allow_warn_never_lifts_red(self):
        self.cfg.allow_warn = {"visual_violence", "visual_graphic_violence"}
        rv = GeminiReview(visual=[{"category": "graphic_violence", "severity": "block", "t": 1.0, "confidence": 95,
                                   "description": "darah"}])
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(rv),
                                 ask_factory=fake_ask_factory(["x"]))
        self.assertEqual(rec["status"], "red")

    def test_gemini_warn_on_blood_is_forced_red(self):
        self.cfg.allow_warn = {"low_resolution", "text_profanity"}
        rv = GeminiReview(visual=[{"category": "graphic_violence", "severity": "warn", "t": 1.0, "confidence": 60,
                                   "description": "darah"}])
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(rv),
                                 ask_factory=fake_ask_factory(["x"]))
        self.assertEqual(rec["status"], "red")

    def test_profanity_and_low_res_allowed_light_fight_still_yellow(self):
        self.cfg.allow_warn = {"low_resolution", "text_profanity"}
        swear = GeminiReview(transcript=[{"start": 1, "end": 2, "text": "eh bodoh"}],
                             text_issues=[{"category": "profanity", "severity": "warn", "source": "speech",
                                           "quote": "bodoh", "reason": "kesat"}])
        ok = relay.process_reel(self.item(SD), self.api.pages(), self.cfg, self.settings,
                                {"reels": {}, "captions": []}, self.api, checker=fake_checker(swear),
                                ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(ok["status"], "posted", ok)
        fight = GeminiReview(visual=[{"category": "violence", "severity": "warn", "t": 1.0, "confidence": 80,
                                      "description": "tampar"}])
        y = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                               {"reels": {}, "captions": []}, self.api, checker=fake_checker(fight),
                               ask_factory=fake_ask_factory(["x"]))
        self.assertEqual(y["status"], "yellow")

    def test_manual_approve_lifts_incitement_but_not_gore_or_nudity(self):
        inc = GeminiReview(text_issues=[{"category": "violence_threat", "severity": "block", "source": "speech",
                                         "timestamp": 3, "quote": "bagi pecah", "reason": "hasutan"}])
        it = self.item(HD)
        self.cfg.approve_ids = {it["id"]}
        rec = relay.process_reel(it, self.api.pages(), self.cfg, self.settings, {"reels": {}, "captions": []},
                                 self.api, checker=fake_checker(inc),
                                 ask_factory=fake_ask_factory(["Dpt info laki ni dah", "Katanya adik ni ada"]))
        self.assertEqual(rec["status"], "posted")
        self.assertTrue(rec["approved_manually"])
        for cat in ("graphic_violence", "nudity_sexual", "drugs"):
            rv = GeminiReview(visual=[{"category": cat, "severity": "block", "t": 1.0, "confidence": 95,
                                       "description": "x"}])
            rec = relay.process_reel(it, self.api.pages(), self.cfg, self.settings, {"reels": {}, "captions": []},
                                     self.api, checker=fake_checker(rv), ask_factory=fake_ask_factory(["x"]))
            self.assertEqual(rec["status"], "red", cat)
            self.assertEqual(rec["approve_refused"], [f"visual_{cat}"])

    def test_incitement_without_approval_stays_red(self):
        inc = GeminiReview(text_issues=[{"category": "violence_threat", "severity": "block", "source": "speech",
                                         "timestamp": 3, "quote": "bagi pecah", "reason": "hasutan"}])
        rec = relay.process_reel(self.item(HD), self.api.pages(), self.cfg, self.settings,
                                 {"reels": {}, "captions": []}, self.api, checker=fake_checker(inc),
                                 ask_factory=fake_ask_factory(["x"]))
        self.assertEqual(rec["status"], "red")

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


class TestSelectTodo(unittest.TestCase):
    def test_selection(self):
        feed = [{"id": i} for i in ["new", "yel", "red", "posted", "appr"]]
        done = {"yel": {"status": "yellow"}, "red": {"status": "red"}, "posted": {"status": "posted"},
                "appr": {"status": "yellow"}}
        ids = lambda xs: [x["id"] for x in xs]
        self.assertEqual(ids(relay.select_todo(feed, done, set(), False, 10)), ["new"])
        self.assertEqual(ids(relay.select_todo(feed, done, {"appr", "posted"}, False, 10)), ["appr", "new"])
        self.assertEqual(ids(relay.select_todo(feed, done, set(), True, 10)), ["new", "yel", "red", "appr"])
        self.assertEqual(ids(relay.select_todo(feed, done, {"appr"}, True, 2)), ["appr", "new"])


class FakeRemoteApi(FakePostolab):
    def __init__(self):
        super().__init__()
        self.saved = []

    def save_items(self, items):
        self.saved.extend(items)


def remote_settings(dry=False, **deli):
    pc = {"enabled": True, "sources": ["https://www.facebook.com/penmerahdotcoms/reels/"],
          "target_mode": "all", "target_page_ids": [], "slots": {"start": "06:00", "end": "00:00", "step": 30},
          "stagger": 1, "allow_warn": ["low_resolution"]}
    pc.update(deli)
    return {"paused": False, "dry_run": dry, "max_videos_per_run": 5, "profiles": {
        "deli": pc,
        "kedua": {"enabled": True, "sources": ["https://www.facebook.com/buletinmalaysiakuu/reels/",
                                               "https://www.facebook.com/penmerahdotcoms/reels/"],
                  "target_mode": "selected", "target_page_ids": ["333"], "stagger": 0, "allow_warn": []},
        "mati": {"enabled": False, "sources": ["https://www.facebook.com/penmerahdotcoms/reels/"]},
    }}


def fresh_captions():
    sets = iter([["Dpt info laki ni dah", "Katanya adik ni ada", "Zaman kita dulu, budak ni akan"],
                 ["Rupanya pakcik ni sebenarnya", "Staff kedai ni terus"],
                 ["Mak cik ni tak sangka", "Dorg ni rupanya pernah"]])

    def factory(settings, path):
        caps = next(sets)
        return (lambda prompt: {"captions": caps}), (lambda: None)
    return factory


class TestRemote(unittest.TestCase):
    FEED = [{"id": "1", "url": "https://www.facebook.com/reel/1/", "mp4": "m", "source": "penmerahdotcoms",
             "caption": "", "thumb": "https://t/1.jpg"},
            {"id": "2", "url": "u2", "mp4": "m", "source": "buletinmalaysiakuu", "caption": "", "thumb": ""},
            {"id": "3", "url": "u3", "mp4": "m", "source": "lain", "caption": "", "thumb": ""}]

    def setUp(self):
        self.base = relay.Cfg()
        self.settings = Settings()
        self.settings.visual_provider, self.settings.gemini_api_key, self.settings.audd_api_token = "gemini", "x", ""
        dl = relay.download
        relay.download = lambda url, dest, session=None: shutil.copy(HD, dest)
        self.addCleanup(lambda: setattr(relay, "download", dl))

    def test_eligibility(self):
        e = relay.remote_eligible
        self.assertTrue(e(None, True))
        self.assertTrue(e({"status": "approved"}, False))
        self.assertFalse(e({"status": "approved"}, True))
        self.assertFalse(e({"status": "rejected", "dry_run": True}, False))
        self.assertFalse(e({"status": "posted"}, False))
        self.assertTrue(e({"status": "yellow", "dry_run": True}, False))
        self.assertFalse(e({"status": "yellow", "dry_run": True}, True))
        self.assertFalse(e({"status": "yellow", "dry_run": False}, False))
        self.assertTrue(e({"status": "error", "tries": 2}, False))
        self.assertFalse(e({"status": "error", "tries": 3}, False))

    def test_profiles_sources_targets_and_shared_check(self):
        api, calls, asker = FakeRemoteApi(), [], fresh_captions()

        def checker(path, caption, target, settings, layers=(1, 3, 4)):
            calls.append(path)
            return check_video(path, caption, target, settings, layers=layers,
                               overrides={"gemini_review": lambda p, c, s: GeminiReview()})

        def process(item, pages, c, settings, state, api_, cache=None):
            return relay.process_reel(item, pages, c, settings, state, api_, checker=checker, cache=cache,
                                      ask_factory=asker,
                                      log=lambda m: None)

        remote = {"settings": remote_settings(), "items": [], "captions": ["Lama...see-moo.re"]}
        done = relay.run_remote(self.base, self.settings, api, remote, self.FEED, api.pages(), process=process)
        got = sorted((c.profile, it["id"]) for c, it, _ in done)
        self.assertEqual(got, [("deli", "1"), ("kedua", "1"), ("kedua", "2")])   # 'lain' & profile 'mati' tiada
        self.assertEqual(len(calls), 2)                                           # reel 1 disemak sekali sahaja
        by = {(c[3], c[1]): c for c in api.calls}
        self.assertEqual([p["pageId"] for p in by[("deli", "1")][2]], ["111", "222", "333"])
        self.assertEqual([p["pageId"] for p in by[("kedua", "2")][2]], ["333"])
        row = next(r for r in api.saved if r["profile"] == "deli")
        self.assertEqual((row["status"], row["thumb_url"], row["dry_run"]), ("posted", "https://t/1.jpg", False))
        msgs = [p["message"] for c in api.calls for p in c[2]]
        self.assertEqual(len(msgs), len(set(msgs)))                               # tiada caption berulang
        self.assertTrue(all(r["status"] == "posted" for r in api.saved))

    def test_approved_and_limits_and_paused(self):
        rows = [{"profile": "deli", "reel_id": "9", "status": "approved", "dry_run": True, "tries": 0},
                {"profile": "deli", "reel_id": "1", "status": "rejected", "dry_run": True, "tries": 0}]
        feed = self.FEED + [{"id": "9", "url": "", "mp4": "m", "source": "penmerahdotcoms", "caption": "", "thumb": ""}]
        st = remote_settings()
        rowmap = {(r["profile"], r["reel_id"]): r for r in rows}
        cfgs = [relay.profile_cfg(self.base, n, st, pc, rowmap) for n, pc in st["profiles"].items() if pc["enabled"]]
        self.assertEqual(cfgs[0].approve_ids, {"9"})
        self.assertEqual(cfgs[0].allow_warn, {"low_resolution"})
        jobs = relay.plan_jobs(feed, cfgs, rowmap, 1)
        self.assertEqual([(c.profile, it["id"]) for c, it in jobs], [("deli", "9"), ("kedua", "9")][:len(jobs)])
        self.assertTrue(all(it["id"] == "9" for _, it in jobs))
        api = FakeRemoteApi()
        out = relay.run_remote(self.base, self.settings, api, {"settings": {**st, "paused": True}, "items": []},
                               feed, api.pages(), process=lambda *a, **k: 1 / 0)
        self.assertEqual(out, [])

    def test_error_saved_with_tries(self):
        api = FakeRemoteApi()
        st = remote_settings()
        st["profiles"] = {"deli": st["profiles"]["deli"]}
        rows = [{"profile": "deli", "reel_id": "1", "status": "error", "dry_run": False, "tries": 1}]

        def boom(*a, **k):
            raise RuntimeError("rangkaian")
        relay.run_remote(self.base, self.settings, api, {"settings": st, "items": rows}, self.FEED[:1],
                         api.pages(), process=boom)
        self.assertEqual((api.saved[0]["status"], api.saved[0]["tries"]), ("error", 2))


if __name__ == "__main__":
    unittest.main()
