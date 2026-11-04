import datetime as dt
import types
import fb_reels_rss as fbr
import postolab_relay as relay


def cfg(**kw):
    c = relay.Cfg()
    c.profile, c.sources, c.backfill, c.backfill_per_run, c.backfill_rotate_days = "deli", {"g1"}, True, 2, 30
    for k, v in kw.items():
        setattr(c, k, v)
    return c


ARCH = {"g1": [{"id": str(i), "url": f"https://www.facebook.com/watch/?v={i}"} for i in range(5, 0, -1)]}  # 5 = terbaru, 1 = paling lama


def iso(days_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def test_merge_archive_keeps_order_and_old_items():
    prev = [{"id": "3", "url": "u3"}, {"id": "2", "url": "u2"}, {"id": "1", "url": "u1"}]
    top = ["https://www.facebook.com/watch/?v=5", "https://www.facebook.com/x/videos/4444444444/",
           "https://www.facebook.com/watch/?v=3"]
    out = fbr.merge_archive(top, prev)
    assert [i["id"] for i in out] == ["5", "4444444444", "3", "2", "1"]


def test_oldest_first_and_skips_taken():
    rows = {("deli", "1"): {"status": "posted", "updated_at": iso(1)}}
    out = relay.plan_backfill(cfg(), ARCH, rows, set(), "20261104")
    assert [o["id"] for o in out] == ["2", "3"] and not out[0]["rotate"] and out[0]["sid"] == "2" and out[0]["mp4"] == ""


def test_skips_feed_items_and_rejected():
    rows = {("deli", "2"): {"status": "red"}}
    out = relay.plan_backfill(cfg(backfill_per_run=3), ARCH, rows, {"1"}, "20261104")
    assert [o["id"] for o in out] == ["3", "4", "5"]


def test_rotation_restarts_from_oldest_after_cooldown():
    rows = {("deli", str(i)): {"status": "posted", "updated_at": iso(40 if i <= 2 else 5)} for i in range(1, 6)}
    out = relay.plan_backfill(cfg(), ARCH, rows, set(), "20261104")
    assert [o["id"] for o in out] == ["1", "2"] and all(o["rotate"] for o in out)
    assert out[0]["sid"] == "1:r20261104"
    rows = {("deli", str(i)): {"status": "posted", "updated_at": iso(5)} for i in range(1, 6)}
    assert relay.plan_backfill(cfg(), ARCH, rows, set(), "20261104") == []      # belum cukup tempoh


def test_profile_cfg_reads_backfill():
    c = relay.profile_cfg(relay.Cfg(), "deli", {}, {"backfill": True, "backfill_per_run": 4, "backfill_rotate_days": 3}, {})
    assert (c.backfill, c.backfill_per_run, c.backfill_rotate_days) == (True, 4, 7)
    assert relay.profile_cfg(relay.Cfg(), "deli", {}, {}, {}).backfill is False


def test_download_reel_without_mp4_fetches_fresh(monkeypatch, tmp_path):
    got = []
    monkeypatch.setattr(relay, "download", lambda u, d, session=None: got.append(u))
    item = {"mp4": "", "url": "https://www.facebook.com/watch/?v=1", "caption": ""}
    relay.download_reel(item, str(tmp_path / "v"), log=lambda *_: None,
                        extract=lambda u: types.SimpleNamespace(mp4="https://new/v.mp4", caption="Kapsyen asal", thumb="t", error=""))
    assert got == ["https://new/v.mp4"] and item["caption"] == "Kapsyen asal"


def test_run_remote_adds_backfill_when_no_new(monkeypatch):
    monkeypatch.setattr(relay, "load_archives", lambda slugs, fetch=None: ARCH)
    seen = []

    def process(item, pages, c, settings, state, api, cache=None):
        seen.append((item["id"], item.get("backfill")))
        return {"status": "posted", "summary": "", "scheduled": []}
    api = types.SimpleNamespace(save_items=lambda items: None)
    remote = {"settings": {"dry_run": False, "profiles": {"deli": {"enabled": True, "sources": ["https://www.facebook.com/groups/1/"],
                                                                    "backfill": True, "backfill_per_run": 2}}},
              "items": [], "captions": []}
    ARCH["g1x"] = ARCH["g1"]
    monkeypatch.setattr(relay, "load_archives", lambda slugs, fetch=None: {s: ARCH["g1"] for s in slugs})
    relay.run_remote(relay.Cfg(), None, api, remote, [], [{"page_id": "p", "page_name": "P"}], process=process)
    assert seen == [("1", True), ("2", True)]
