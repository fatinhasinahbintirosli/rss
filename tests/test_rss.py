"""Offline tests (no Facebook, no R2)."""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fb_reels_rss as m  # noqa: E402


class T(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(m.normalize_reels_url("facebook.com/penmerahdotcoms"),
                         ("https://facebook.com/penmerahdotcoms/reels/", "penmerahdotcoms"))
        self.assertEqual(m.normalize_reels_url("https://www.facebook.com/penmerahdotcoms/reels")[0],
                         "https://www.facebook.com/penmerahdotcoms/reels/")

    def test_caption_cleanup(self):
        self.assertEqual(m.clean_caption("Aduh..dah kenapa buat... iseemo.re"), ("Aduh..dah kenapa buat", True))
        self.assertEqual(m.clean_caption("Patutlah.. rupanya.. See-mo.ren"), ("Patutlah.. rupanya", True))
        self.assertEqual(m.clean_caption("Biasa"), ("Biasa", False))

    def test_pick_best_mp4_prefers_hd_with_audio(self):
        info = {"formats": [
            {"url": "hd-video-only", "ext": "mp4", "vcodec": "h264", "acodec": "none", "height": 1280},
            {"url": "sd", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 634, "width": 356},
            {"url": "hd", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 1280, "width": 720},
            {"url": "audio", "ext": "m4a", "vcodec": "none", "acodec": "aac"},
        ]}
        self.assertEqual(m.pick_best_mp4(info), ("hd", 720, 1280))
        self.assertEqual(m.pick_best_mp4({"url": "x", "width": 1, "height": 2}), ("x", 1, 2))
        self.assertEqual(m.pick_best_mp4({}), ("", None, None))

    def test_rss_is_valid_and_has_enclosure(self):
        r = m.Reel(id="123456789", url="https://www.facebook.com/reel/123456789/",
                   caption="Baris satu\nBaris & dua <b>", mp4="https://v/x.mp4?a=1&b=2", width=720, height=1280,
                   thumb="https://t/x.jpg?a=1&b=2", first_seen="Mon, 19 Oct 2026 12:00:00 GMT")
        xml = m.build_rss("penmerahdotcoms", "https://www.facebook.com/penmerahdotcoms/reels/", [r])
        root = ET.fromstring(xml)
        item = root.find("channel/item")
        self.assertEqual(item.find("enclosure").get("url"), "https://v/x.mp4?a=1&b=2")
        desc = item.findtext("description")
        self.assertIn("Kapsyen Penuh:", desc)
        self.assertIn("Baris &amp; dua &lt;b&gt;", desc)        # caption escaped inside the HTML
        self.assertIn("<br/>", desc)

    def test_rss_parses_with_relay_parser(self):
        """The RSS must be readable by the relay's parser (reels-relay/relay/rss.py)."""
        relay = os.path.join(os.path.dirname(__file__), "..", "..", "reels-relay")
        if not os.path.isdir(relay):
            self.skipTest("reels-relay not next to this project")
        sys.path.insert(0, relay)
        from relay import rss as relay_rss
        r = m.Reel(id="123456789", url="https://www.facebook.com/reel/123456789/", caption="Baris satu\nDua",
                   mp4="https://v/x.mp4?a=1&b=2", first_seen="x")
        items = relay_rss.parse(m.build_rss("p", "u", [r]))
        self.assertEqual(items[0].video_url, "https://v/x.mp4?a=1&b=2")
        self.assertEqual(items[0].caption, "Baris satu\nDua")

    def test_merge_keeps_first_seen_and_last_good_data(self):
        old = [m.Reel(id="1", url="u1", mp4="old.mp4", caption="cap", first_seen="T0"),
               m.Reel(id="2", url="u2", mp4="b.mp4", first_seen="T0")]
        new = [m.Reel(id="3", url="u3", mp4="c.mp4"), m.Reel(id="1", url="u1", mp4="", caption="")]
        out = m.merge_with_previous(new, old, keep=10)
        self.assertEqual([r.id for r in out], ["3", "1", "2"])
        r1 = out[1]
        self.assertEqual((r1.first_seen, r1.mp4, r1.caption), ("T0", "old.mp4", "cap"))
        self.assertTrue(out[0].first_seen.endswith("GMT"))
        self.assertEqual(len(m.merge_with_previous(new, old, keep=2)), 2)

    def test_login_wall_detection(self):
        self.assertTrue(m.LOGIN_WALL.search("Log in to Facebook to continue"))
        self.assertTrue(m.LOGIN_WALL.search("Log masuk ke Facebook untuk teruskan"))


if __name__ == "__main__":
    unittest.main()
