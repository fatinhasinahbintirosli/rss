"""RSS (semua.xml) → semak video → caption Gemini setiap Page → jadual dalam Postolab.

Larian (GitHub Actions, selepas RSS dijana):
  1. baca RSS gabungan, cari reel yang belum pernah diproses (state dalam R2)
  2. muat turun mp4, semak: teknikal, muzik (AudD jika ada token), Gemini (visual + suara + teks)
  3. HIJAU → Gemini tulis caption berbeza bagi setiap Page → upload mp4 ke R2 Postolab
            → POST /api/external/videos (slot kosong seterusnya bagi setiap Page, profile "deli")
     KUNING → ikut YELLOW_MODE (skip | post)      MERAH → skip
  4. simpan state + laporan HTML ke R2
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import html
import json
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import requests

import captions as cap
from video_checker import layer1_technical
from video_checker.config import Settings
from video_checker.gemini import GeminiClient
from video_checker.models import BLOCK, INFO, WARN, Finding
from video_checker.pipeline import check_video, score

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ------------------------------------------------------------------ config
class Cfg:
    def __init__(self):
        self.rss_url = env("RELAY_RSS_URL") or (
            f"{env('R2_PUBLIC_BASE_URL').rstrip('/')}/{env('R2_PREFIX', 'rss/')}semua.xml")
        self.postolab_base = env("POSTOLAB_URL", "https://postolab.com").rstrip("/")
        self.postolab_key = env("POSTOLAB_API_KEY")
        self.profile = env("POSTOLAB_PROFILE", "deli")
        self.yellow_mode = env("YELLOW_MODE", "skip").lower()           # skip | post
        self.max_per_run = int(env("MAX_VIDEOS_PER_RUN", "5"))
        self.stagger = int(env("STAGGER", "0"))
        self.dry_run = env("DRY_RUN", "0") in ("1", "true", "ya")
        # "penmerahdotcoms=1234567890,buletinmalaysiakuu=987654321" → video tidak dihantar ke page asalnya
        self.source_map = {}
        for part in env("SOURCE_PAGE_MAP").split(","):
            if "=" in part:
                slug, pid = part.split("=", 1)
                self.source_map[slug.strip().lower()] = {x.strip() for x in pid.split("|") if x.strip()}
        self.exclude = {x.strip() for x in env("EXCLUDE_PAGE_IDS").split(",") if x.strip()}
        # Jenis amaran KUNING yang diterima (dianggap hijau). Amaran MERAH tidak boleh dilepaskan.
        #   low_resolution, visual_violence, visual_weapons, text_profanity, visual_third_party_watermark, ...
        self.allow_warn = {x.strip() for x in env("ALLOW_WARN").split(",") if x.strip()}
        # Reel yang awak tonton sendiri dan luluskan walaupun ditanda kuning/merah
        self.approve_ids = {x.strip() for x in env("APPROVE_IDS").replace("\n", ",").split(",") if x.strip()}
        # Sentiasa MERAH walaupun Gemini beri tahap "warn": darah, kematian, cedera parah, bunuh diri.
        self.force_block = {x.strip() for x in env(
            "FORCE_BLOCK", "visual_graphic_violence,visual_self_harm,text_self_harm,visual_disturbing").split(",")
            if x.strip()}
        self.allow_warn -= self.force_block
        self.caption_suffix = cap.SUFFIX        # ayat penutup caption (tetapan profile)
        self.caption_max_words = cap.MAX_WORDS  # maks perkataan sebelum ayat penutup
        self.caption_min_words = 1              # min perkataan (julat "dari X hingga Y")
        self.caption_short = False              # yang → yg, kata ganda → ²
        self.comment_text = None                # komen pertama (teks template), None = tiada
        self.comment_image = True               # sertakan screenshot video dalam komen pertama
        self.slots = None             # {"start","end","step"} dari tetapan profile (None = lalai Postolab)
        self.target_ids = None        # set page_id dipilih (None = semua page)
        self.sources = None           # set slug sumber RSS profile (None = semua sumber)
        self.state_key = f"{env('R2_PREFIX', 'rss/')}relay_state.json"
        self.report_key = f"{env('R2_PREFIX', 'rss/')}laporan.html"


# ------------------------------------------------------------------ RSS
def parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        link = it.findtext("link") or ""
        m = re.search(r"/reel/(\d+)", link)
        enc = it.find("enclosure")
        if not m or enc is None or not enc.get("url"):
            continue
        desc = html.unescape(it.findtext("description") or "")
        cm = re.search(r"Kapsyen Penuh:</b><br/>(.*?)</p>", desc, re.S)
        caption = html.unescape(re.sub(r"<[^>]+>", "", (cm.group(1) if cm else "").replace("<br/>", "\n"))).strip()
        tm = re.search(r'<img[^>]+src="([^"]+)"', desc)
        out.append({"id": m.group(1), "url": link, "mp4": html.unescape(enc.get("url")),
                    "source": (it.findtext("category") or "").strip().lower(), "caption": caption,
                    "thumb": html.unescape(tm.group(1)) if tm else ""})
    return out


# ------------------------------------------------------------------ Postolab API
def _site(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


class _KeepAuthSession(requests.Session):
    """requests buang header Authorization bila dialih ke host lain (cth. postolab.com → www.postolab.com).
    Kekalkan header jika masih domain yang sama."""

    def rebuild_auth(self, prepared_request, response):
        from urllib.parse import urlparse
        old, new = urlparse(response.request.url).hostname, urlparse(prepared_request.url).hostname
        if _site(old) == _site(new) and "Authorization" in response.request.headers:
            prepared_request.headers["Authorization"] = response.request.headers["Authorization"]
            return
        super().rebuild_auth(prepared_request, response)


class Postolab:
    def __init__(self, base: str, key: str, session=None):
        self.base, self.s = base, session or _KeepAuthSession()
        self.h = {"Authorization": f"Bearer {key}"}

    def _check(self, r):
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"Postolab {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise RuntimeError(f"Postolab {r.status_code}: {j.get('error')} {j.get('reason') or ''}".strip())
        return j

    def pages(self) -> list[dict]:
        return self._check(self.s.get(f"{self.base}/api/external/videos", headers=self.h, timeout=30))["pages"]

    def upload(self, path: str, content_type: str = "video/mp4") -> str:
        name = "reel.mp4" if content_type.startswith("video/") else "image.jpg"
        j = self._check(self.s.post(f"{self.base}/api/external/upload-url", headers=self.h,
                                    json={"fileName": name, "contentType": content_type}, timeout=30))
        with open(path, "rb") as f:
            r = self.s.put(j["uploadUrl"], data=f, headers={"Content-Type": content_type}, timeout=600)
        if r.status_code >= 300:
            raise RuntimeError(f"Upload R2 gagal: {r.status_code} {r.text[:200]}")
        return j["publicUrl"]

    def schedule(self, video_url: str, source_id: str, posts: list[dict], profile: str, stagger: int,
                 slots: dict | None = None, first_comment: str | None = None,
                 comment_image_url: str | None = None, when: str | None = None) -> dict:
        body = {"videoUrl": video_url, "sourceId": source_id, "posts": posts, "profile": profile, "stagger": stagger}
        if slots:
            body["slots"] = slots
        if when:                      # masa tetap (ISO) untuk semua Page; jika tiada → slot kosong seterusnya
            body["schedule"] = when
        if first_comment:
            body["firstComment"] = first_comment
        if comment_image_url:
            body["commentImageUrl"] = comment_image_url
        return self._check(self.s.post(f"{self.base}/api/external/videos", headers=self.h, timeout=60, json=body))

    # Halaman Auto Reels di Postolab: tetapan setiap profile + keputusan semakan.
    def remote(self) -> dict | None:
        """None jika Postolab belum ada /api/external/auto-reels (belum deploy)."""
        r = self.s.get(f"{self.base}/api/external/auto-reels", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None
        return self._check(r)

    def save_items(self, items: list[dict]) -> None:
        self._check(self.s.post(f"{self.base}/api/external/auto-reels", headers=self.h, timeout=30,
                                json={"items": items}))


# ------------------------------------------------------------------ state + report (R2)
def r2():
    import boto3
    return boto3.client("s3", endpoint_url=f"https://{env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
                        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"), region_name="auto")


class Store:
    def __init__(self, client=None, bucket="", key=""):
        self.c, self.bucket, self.key = client, bucket, key

    def load(self) -> dict:
        if not self.c:
            return {"reels": {}, "captions": []}
        try:
            return json.loads(self.c.get_object(Bucket=self.bucket, Key=self.key)["Body"].read())
        except Exception:
            return {"reels": {}, "captions": []}

    def save(self, state: dict, report_key: str, report_html: str) -> None:
        if not self.c:
            return
        self.c.put_object(Bucket=self.bucket, Key=self.key, Body=json.dumps(state, ensure_ascii=False).encode(),
                          ContentType="application/json")
        self.c.put_object(Bucket=self.bucket, Key=report_key, Body=report_html.encode(),
                          ContentType="text/html; charset=utf-8", CacheControl="max-age=60")


def render_report(state: dict) -> str:
    dry = {k: {**v, "status": f"percubaan: {v.get('status', '')}"} for k, v in state.get("dry", {}).items()}
    rows = sorted({**dry, **state["reels"]}.items(), key=lambda kv: kv[1].get("at", ""), reverse=True)[:150]
    color = {"percubaan: green": "#0891b2", "posted": "#16a34a", "green": "#16a34a", "yellow": "#ca8a04", "red": "#dc2626",
             "error": "#6b7280", "skipped": "#6b7280"}
    out = []
    for rid, r in rows:
        st = r.get("status", "")
        caps = "".join(f"<li>{html.escape(c['page'])}: {html.escape(c['message'])} "
                       f"<small>({html.escape(c.get('when', ''))})</small></li>" for c in r.get("scheduled", []))
        out.append(
            f"<tr><td><span style='background:{color.get(st, '#6b7280')};color:#fff;padding:2px 8px;"
            f"border-radius:99px;font-size:12px'>{html.escape(st)}</span></td>"
            f"<td><a href='https://www.facebook.com/reel/{rid}/'>{rid}</a><br><small>{html.escape(r.get('source', ''))}"
            f" · {html.escape(r.get('at', '')[:16].replace('T', ' '))}</small></td>"
            f"<td>{html.escape(r.get('summary', ''))}"
            + "".join(f"<div style='color:#555;font-size:13px'>• {html.escape(x)}</div>" for x in r.get("reasons", []))
            + (f"<ul style='margin:6px 0;padding-left:18px;font-size:13px'>{caps}</ul>" if caps else "")
            + "</td></tr>")
    return ("<!doctype html><html lang='ms'><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Laporan Relay Reels</title><style>"
            "body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#111}"
            "table{width:100%;border-collapse:collapse;background:#fff}td{border-top:1px solid #e5e7eb;"
            "padding:8px;vertical-align:top}a{color:#2563eb}</style></head><body><h1 style='font-size:20px'>"
            "Laporan Relay Reels</h1><p style='color:#555'>Dikemas kini "
            f"{html.escape(dt.datetime.now(dt.timezone.utc).isoformat()[:16])} UTC</p><table>"
            + "".join(out) + "</table></body></html>")


# ------------------------------------------------------------------ one reel
def download(url: str, dest: str, session=None) -> None:
    s = session or requests
    with s.get(url, stream=True, timeout=300, headers={"User-Agent": UA}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


# Tidak boleh dilepaskan walaupun melalui APPROVE_IDS
NEVER_POST = {"visual_nudity_sexual", "visual_minor_safety", "visual_graphic_violence", "visual_self_harm",
              "visual_drugs", "text_adult_sexual", "text_self_harm", "text_drugs"}


def checker_layers(settings: Settings) -> tuple[int, ...]:
    # Tanpa token AudD, lapisan muzik tidak dijalankan (jika tidak, semua video jadi kuning dalam STRICT_MODE).
    return (1, 2, 3, 4) if settings.audd_api_token or settings.acrcloud_host else (1, 3, 4)


def extra_findings(path: str) -> list[Finding]:
    """Postolab terbitkan melalui /videos; video SD masih boleh, tapi tandakan kuning."""
    d = layer1_technical.run(path, "video").data
    w, h = d.get("width") or 0, d.get("height") or 0
    if w and h and min(w, h) < 540:
        return [Finding(1, WARN, "low_resolution", f"Resolusi rendah ({w}x{h}). Kualiti buruk di Facebook.")]
    return []


def gemini_ask_factory(settings: Settings, path: str):
    """Upload video sekali, kemudian guna fail yang sama untuk semua permintaan caption."""
    client = GeminiClient(settings.gemini_api_key, settings.gemini_model)
    f = client.wait_active(client.upload(path))

    def ask(prompt: str) -> dict:
        return client.generate_json(f, prompt)

    return ask, (lambda: client.delete(f))


def process_reel(item: dict, pages: list[dict], cfg: Cfg, settings: Settings, state: dict, api: Postolab,
                 checker=check_video, ask_factory=gemini_ask_factory, log=print, cache: dict | None = None) -> dict:
    rec = {"source": item["source"], "at": dt.datetime.now(dt.timezone.utc).isoformat(), "reasons": []}
    targets = [p for p in pages if str(p["page_id"]) not in cfg.exclude
               and str(p["page_id"]) not in cfg.source_map.get(item["source"], set())
               and (cfg.target_ids is None or str(p["page_id"]) in cfg.target_ids)]
    if not targets:
        rec.update(status="skipped", summary="Tiada Page sasaran (semua dikecualikan).")
        return rec

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, f"{item['id']}.mp4")
        download(item["mp4"], path)
        # Caption asal TIDAK dipost (diganti caption baru yang disemak berasingan), jadi semak video sahaja.
        # Reel yang sama untuk beberapa profile: semak sekali sahaja dalam satu larian.
        if cache is not None and item["id"] in cache:
            report = copy.deepcopy(cache[item["id"]])
        else:
            report = checker(path, "", "video", settings, layers=checker_layers(settings))
            extra = extra_findings(path)
            if extra:
                report.layers[0].findings.extend(extra)
            if cache is not None:
                cache[item["id"]] = copy.deepcopy(report)
        accepted = []
        for f in report.findings:
            if f.code.split(":")[0] in cfg.force_block:
                f.severity = BLOCK
            elif f.severity == WARN and f.code.split(":")[0] in cfg.allow_warn:
                f.severity = INFO
                accepted.append(f.code.split(":")[0])
        report.verdict, report.summary = score(report.layers, settings.strict)
        if accepted:
            rec["accepted"] = sorted(set(accepted))
        hard_stop = sorted({f.code.split(":")[0] for f in report.findings
                            if f.severity == "block" and f.code.split(":")[0] in NEVER_POST})
        if item["id"] in cfg.approve_ids and report.verdict != "green":
            if hard_stop:
                rec["approve_refused"] = hard_stop
            else:
                rec["approved_manually"] = True
                report.verdict, report.summary = "green", "Diluluskan secara manual."
        rec["verdict"], rec["summary"] = report.verdict, report.summary
        rec["reasons"] = [f"[{f.severity}] ({f.code.split(':')[0]}) {f.message} {f.evidence}".strip()
                          for f in report.findings if f.severity in ("block", "warn")][:8]
        go = report.verdict == "green" or (report.verdict == "yellow" and cfg.yellow_mode == "post")
        if not go:
            rec["status"] = report.verdict
            return rec

        ask, cleanup = ask_factory(settings, path)
        try:
            history = state.get("captions", [])[-300:]
            caps, rejected = cap.generate(path, len(targets), item["caption"], history, ask,
                                          max_words=cfg.caption_max_words, min_words=cfg.caption_min_words, suffix=cfg.caption_suffix,
                                          short=cfg.caption_short)
        finally:
            cleanup()
        rec["caption_rejected"] = rejected[:10]
        if not caps:
            rec.update(status="error", summary="Gemini gagal hasilkan caption yang menepati syarat.")
            return rec
        if len(caps) < len(targets):
            log(f"   nota: hanya {len(caps)} caption unik untuk {len(targets)} Page; sebahagian Page guna caption berulang.")
            caps = cap.fill_to(caps, len(targets))
        posts = [{"pageId": str(p["page_id"]), "message": c} for p, c in zip(targets, caps)]
        comment_img = None
        if cfg.comment_text:
            rec["comment"] = cfg.comment_text
            rec["comment_image"] = bool(cfg.comment_image)
            if cfg.comment_image and not cfg.dry_run:
                from link_relay import screenshot          # import lewat: elak import bulat
                comment_img = api.upload(screenshot(path), "image/jpeg")

        if cfg.dry_run:
            rec.update(status="green", scheduled=[{"page": next(x["page_name"] for x in targets
                                                               if str(x["page_id"]) == p["pageId"]),
                                                   "message": p["message"], "when": "(dry run)"} for p in posts])
            return rec
        video_url = api.upload(path)

    res = api.schedule(video_url, item["id"], posts, cfg.profile, cfg.stagger, cfg.slots,
                       first_comment=cfg.comment_text, comment_image_url=comment_img)
    names = {str(p["page_id"]): p["page_name"] for p in pages}
    msg = {p["pageId"]: p["message"] for p in posts}
    rec["scheduled"] = [{"page": names.get(c["pageId"], c["pageId"]), "message": msg.get(c["pageId"], ""),
                         "when": c["scheduled_at"]} for c in res.get("created", [])]
    rec["status"] = "posted" if rec["scheduled"] else "skipped"
    state.setdefault("captions", []).extend(cap.clean_body(p["message"], [cfg.caption_suffix]) for p in posts)
    state["captions"] = state["captions"][-500:]
    return rec


# ------------------------------------------------------------------ main
NOT_POSTED = ("yellow", "red", "error", "skipped")


def select_todo(feed: list[dict], done: dict, approve_ids: set, recheck: bool, limit: int) -> list[dict]:
    """Reel baru, ditambah reel yang belum pernah dipost tetapi kini diluluskan (APPROVE_IDS)
    atau disemak semula (RECHECK=1). Reel yang sudah dipost tidak akan diproses lagi."""
    def eligible(it):
        prev = done.get(it["id"])
        if prev is None:
            return True
        if prev.get("status") not in NOT_POSTED:
            return False
        return it["id"] in approve_ids or recheck
    picked = [it for it in feed if eligible(it)]
    picked.sort(key=lambda it: 0 if it["id"] in approve_ids else 1)   # yang diluluskan didahulukan
    return picked[:limit]


# ------------------------------------------------------------------ mod halaman Auto Reels (setiap profile)
def source_slug(url: str) -> str:
    from fb_reels_rss import page_slug      # sama dengan <category> dalam RSS
    return page_slug(url).lower()


def profile_cfg(base: Cfg, name: str, gl: dict, pc: dict, rows: dict, templates: list | None = None) -> Cfg:
    c = copy.copy(base)
    c.profile, c.dry_run, c.yellow_mode = name, bool(gl.get("dry_run", True)), "skip"
    if pc.get("slot_mode") == "custom":
        c.stagger, c.slots = int(pc.get("stagger") or 0), pc.get("slots") or None
    else:                            # ikut menu Time Slots profile di Postolab
        c.stagger, c.slots = 0, {"source": "profile"}
    c.allow_warn = set(pc.get("allow_warn") or []) - c.force_block
    c.caption_short = pc.get("caption_short", True) is not False
    tpl = next((t for t in templates or [] if t.get("id") and t.get("id") == pc.get("comment_id")), None)
    c.comment_text = (tpl.get("text") or "").strip() or None if tpl else None
    c.comment_image = pc.get("comment_image", True) is not False
    c.caption_suffix = pc["caption_suffix"] if isinstance(pc.get("caption_suffix"), str) else cap.SUFFIX
    try:
        c.caption_max_words = max(3, min(25, int(pc.get("caption_max_words") or cap.MAX_WORDS)))
    except (TypeError, ValueError):
        c.caption_max_words = cap.MAX_WORDS
    try:
        c.caption_min_words = max(1, min(c.caption_max_words, int(pc.get("caption_min_words") or 1)))
    except (TypeError, ValueError):
        c.caption_min_words = 1
    c.target_ids = ({str(x) for x in pc.get("target_page_ids") or []}
                    if pc.get("target_mode") == "selected" else None)
    c.sources = {source_slug(u) for u in pc.get("sources") or []} - {""}
    c.approve_ids = {rid for (prof, rid), row in rows.items() if prof == name and row.get("status") == "approved"}
    return c


def remote_eligible(row: dict | None, dry_run: bool) -> bool:
    if row is None:
        return True
    st = row.get("status")
    if st == "approved":
        return not dry_run           # diluluskan: dipost bila mod percubaan dimatikan
    if st in ("rejected", "posted"):
        return False
    if row.get("dry_run") and not dry_run:
        return True                  # keputusan mod percubaan: semak semula dalam mod sebenar
    if st == "error":
        return int(row.get("tries") or 0) < 3
    return False


def plan_jobs(feed: list[dict], cfgs: list[Cfg], rows: dict, limit: int) -> list[tuple[Cfg, dict]]:
    """Senarai (profile, reel). Yang diluluskan didahulukan; profile bergilir; had = bilangan reel unik."""
    queues = []
    for c in cfgs:
        q = [it for it in feed if it["source"] in c.sources
             and remote_eligible(rows.get((c.profile, it["id"])), c.dry_run)]
        q.sort(key=lambda it: 0 if it["id"] in c.approve_ids else 1)
        queues.append([(c, it) for it in q])
    jobs, seen = [], set()
    while any(queues):
        for q in queues:
            if not q:
                continue
            c, it = q[0]
            if it["id"] not in seen and len(seen) >= limit:
                q.clear()
                continue
            q.pop(0)
            seen.add(it["id"])
            jobs.append((c, it))
    return jobs


def to_row(c: Cfg, item: dict, rec: dict, tries: int = 0) -> dict:
    return {
        "profile": c.profile, "reel_id": item["id"], "status": rec["status"], "source": item["source"],
        "reel_url": item.get("url"), "thumb_url": item.get("thumb"), "verdict": rec.get("verdict"),
        "summary": rec.get("summary", ""), "reasons": rec.get("reasons", []), "scheduled": rec.get("scheduled", []),
        "detail": {k: rec[k] for k in ("accepted", "approved_manually", "approve_refused", "caption_rejected",
                                       "comment", "comment_image") if rec.get(k)},
        "dry_run": c.dry_run, "tries": tries,
    }


def run_remote(base: Cfg, settings: Settings, api: Postolab, remote: dict, feed: list[dict], pages: list[dict],
               process=process_reel) -> list[tuple[Cfg, dict, dict]]:
    gl = remote.get("settings") or {}
    if gl.get("paused"):
        print("Auto Reels DIHENTIKAN SEMENTARA (halaman Auto Reels → Tetapan).")
        return []
    rows = {(r["profile"], r["reel_id"]): r for r in remote.get("items") or []}
    # Template komen pertama ikut profile; "comment_templates" (senarai lama, dikongsi) hanya sebagai sandaran
    by_profile = remote.get("comment_templates_by_profile") or {}
    legacy = remote.get("comment_templates") or []
    cfgs = [profile_cfg(base, name, gl, pc, rows, by_profile.get(name, legacy))
            for name, pc in (gl.get("profiles") or {}).items() if pc.get("enabled")]
    if not cfgs:
        print("Tiada profile diaktifkan di halaman Auto Reels.")
        return []
    for c in cfgs:
        tgt = "semua page" if c.target_ids is None else f"{len(c.target_ids)} page dipilih"
        print(f"Profile '{c.profile}': sumber {', '.join(sorted(c.sources)) or '-'} | {tgt}")
    if gl.get("dry_run", True):
        print("MOD PERCUBAAN: caption dijana tetapi TIADA apa-apa dijadualkan ke Postolab.")
    suffixes = [c.caption_suffix for c in cfgs]
    state = {"captions": [cap.clean_body(x, suffixes) for x in remote.get("captions") or []]}
    cache, done = {}, []
    jobs = plan_jobs(feed, cfgs, rows, int(gl.get("max_videos_per_run") or base.max_per_run))
    for i, (c, item) in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}] {item['id']} ({item['source']}) → profile '{c.profile}'")
        prev = rows.get((c.profile, item["id"])) or {}
        try:
            rec = process(item, pages, c, settings, state, api, cache=cache)
        except Exception as e:
            rec = {"status": "error", "summary": str(e)[:300], "reasons": []}
        tries = 0
        if rec["status"] == "error":    # dicuba semula larian seterusnya, maksimum 3 kali
            tries = (0 if prev.get("dry_run") != c.dry_run else int(prev.get("tries") or 0)) + 1
            print(f"   ralat (cubaan {tries}/3)")
        try:
            api.save_items([to_row(c, item, rec, tries)])
        except Exception as e:
            print(f"   gagal simpan keputusan ke Postolab: {e}")
        done.append((c, item, rec))
        print(f"   {rec['status'].upper()}: {rec.get('summary', '')}")
        for s in rec.get("scheduled", []):
            print(f"     → {s['page']} @ {s['when']}: {s['message']}")
    if not jobs:
        print("Tiada reel baru untuk profile yang aktif.")
    return done


def run() -> int:
    cfg, settings = Cfg(), Settings()
    if not cfg.postolab_key:
        print("POSTOLAB_API_KEY belum diset.", file=sys.stderr)
        return 2
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY belum diset.", file=sys.stderr)
        return 2
    settings.visual_provider = "gemini"
    api = Postolab(cfg.postolab_base, cfg.postolab_key)

    r = requests.get(cfg.rss_url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    feed = parse_feed(r.text)
    pages = api.pages()

    remote = api.remote() if env("RELAY_MODE", "auto") != "env" else None
    if remote is not None:
        print(f"RSS: {len(feed)} reel | Postolab: {len(pages)} Page | tetapan dari halaman Auto Reels")
        done = run_remote(cfg, settings, api, remote, feed, pages)
        _summary_remote(done)
        return 0
    print("Halaman Auto Reels belum dipasang di Postolab: guna tetapan env (satu profile).")

    client = r2() if env("R2_BUCKET") else None
    store = Store(client, env("R2_BUCKET"), cfg.state_key)
    state = store.load()
    print(f"RSS: {len(feed)} reel | Postolab: {len(pages)} Page | profile '{cfg.profile}'")
    if cfg.source_map:
        known = {pid for ids in cfg.source_map.values() for pid in ids}
        print(f"Page sumber dikecualikan: {', '.join(p['page_name'] for p in pages if str(p['page_id']) in known)}")
    else:
        print("SOURCE_PAGE_MAP tidak diset (ok jika page sumber tiada dalam Postolab).")

    # Mod percubaan disimpan berasingan: reel yang dicuba masih akan dipost bila DRY_RUN dimatikan.
    recheck = env("RECHECK", "0") in ("1", "true", "ya")
    if recheck and cfg.dry_run:
        print("RECHECK=1: keputusan mod percubaan lama dikosongkan.")
        state["dry"] = {}
    done = state.setdefault("dry", {}) if cfg.dry_run else state["reels"]
    if cfg.dry_run:
        print("MOD PERCUBAAN (DRY_RUN=1): caption dijana tetapi TIADA apa-apa dijadualkan ke Postolab.")
    todo = select_todo(feed, done, cfg.approve_ids, recheck and not cfg.dry_run, cfg.max_per_run)
    for i, item in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {item['id']} ({item['source']})")
        try:
            rec = process_reel(item, pages, cfg, settings, state, api)
        except Exception as e:
            tries = int(state.setdefault("retry", {}).get(item["id"], 0)) + 1
            rec = {"source": item["source"], "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "status": "error", "summary": str(e)[:300], "tries": tries}
            if tries < 3:            # cuba lagi larian seterusnya
                print(f"   ralat (cuba lagi): {e}")
                state["retry"][item["id"]] = tries
                store.save(state, cfg.report_key, render_report(state))
                continue
        done[item["id"]] = rec
        print(f"   {rec['status'].upper()}: {rec.get('summary', '')}")
        for s in rec.get("scheduled", []):
            print(f"     → {s['page']} @ {s['when']}: {s['message']}")
        store.save(state, cfg.report_key, render_report(state))
        time.sleep(2)

    if not todo:
        print("Tiada reel baru.")
    store.save(state, cfg.report_key, render_report(state))
    _summary(todo, state)
    return 0


def _summary_remote(done):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("## Auto Reels → Postolab\n\n| Profile | Reel | Status | Sebab / caption |\n|---|---|---|---|\n")
        for c, it, r in done:
            detail = "<br>".join(f"{s['page']}: {s['message']}" for s in r.get("scheduled", [])) or r.get("summary", "")
            f.write(f"| {c.profile} | {it['id']} | {r['status']}{' (percubaan)' if c.dry_run else ''} | "
                    f"{detail.replace('|', '/')} |\n")


def _summary(todo, state):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("## Relay ke Postolab\n\n| Reel | Status | Sebab / caption |\n|---|---|---|\n")
        for it in todo:
            r = {**state.get("dry", {}), **state["reels"]}.get(it["id"], {"status": "ralat (akan dicuba lagi)", "summary": ""})
            detail = "<br>".join(f"{s['page']}: {s['message']}" for s in r.get("scheduled", [])) or r.get("summary", "")
            f.write(f"| {it['id']} | {r['status']} | {detail.replace('|', '/')} |\n")


if __name__ == "__main__":
    sys.exit(run())
