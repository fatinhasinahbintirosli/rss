"""Video Link: pautan video dari halaman Postolab /link-videos → queue Postolab.

Larian (GitHub Actions, workflow link.yml — dicetus serta-merta bila pautan ditambah, + setiap jam):
  1. GET /api/external/link-videos  → tetapan setiap profile + pautan yang menunggu
  2. setiap pautan: muat turun (yt-dlp; Facebook, YouTube, Threads, Instagram, TikTok, dll.)
  3. Gemini tonton video → caption berbeza bagi setiap Page (ayat penutup + bilangan perkataan ikut tetapan)
  4. upload mp4 ke R2 Postolab → POST /api/external/videos (slot kosong seterusnya bagi setiap Page)
  5. kemas kini status pautan di Postolab (posted / error)
Tiada semakan polisi: pengguna sendiri yang hantar pautan.
"""

from __future__ import annotations

import datetime as dt
import glob
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import requests

import captions as cap
from postolab_relay import UA, Postolab, download, env, gemini_ask_factory
from video_checker.config import Settings

MAX_TRIES = 3


# ------------------------------------------------------------------ Postolab API
class LinkApi(Postolab):
    def link_remote(self) -> dict | None:
        """None jika Postolab belum ada /api/external/link-videos (belum deploy)."""
        r = self.s.get(f"{self.base}/api/external/link-videos", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None
        return self._check(r)

    def save_links(self, items: list[dict]) -> None:
        self._check(self.s.post(f"{self.base}/api/external/link-videos", headers=self.h, timeout=30,
                                json={"items": items}))


# ------------------------------------------------------------------ muat turun
class DownloadError(RuntimeError):
    pass


def _probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout or "{}")
    except ValueError:
        return {}


def _has_video(path: str) -> bool:
    return any(s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png")
               for s in _probe(path).get("streams") or [])


def ensure_facebook_mp4(path: str) -> str:
    """Facebook terima mp4 H.264 + AAC dengan paling baik. Tukar jika video lain (cth. VP9/webm dari YouTube)."""
    info = _probe(path)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise DownloadError("Fail yang dimuat turun tiada video.")
    ok = path.lower().endswith(".mp4") and v.get("codec_name") == "h264" and (a is None or a.get("codec_name") == "aac")
    if ok:
        return path
    out = os.path.splitext(path)[0] + "_fb.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out):
        raise DownloadError(f"Gagal tukar format video: {r.stderr[-300:]}")
    return out


def threads_candidates(url: str) -> list[str]:
    """Pautan Threads (threads.com / threads.net, termasuk hujung /media atau ?xmt=...) → halaman embed
    (HTML lengkap dengan <video>) dan halaman post biasa, untuk dicuba satu demi satu."""
    m = re.search(r"threads\.(?:com|net)/@([\w.\-]+)/post/([\w\-]+)", url or "", re.I)
    if not m:
        return []
    user, code = m.group(1), m.group(2)
    base = f"https://www.threads.com/@{user}/post/{code}"
    return [f"{base}/embed", base]


def _unescape_url(u: str) -> str:
    u = u.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u0025", "%")
    return html.unescape(u)


def strip_byte_range(u: str) -> str:
    """Pautan video Meta kadang-kadang membawa bytestart/byteend (sebahagian fail sahaja) → buang supaya fail penuh."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("bytestart", "byteend")]
    return urlunsplit(parts._replace(query=urlencode(q)))


_PW_READY = False


def ensure_chromium() -> None:
    """Pasang Chromium untuk Playwright hanya bila diperlukan (cth. pautan Threads)."""
    global _PW_READY
    if _PW_READY:
        return
    # Tanpa --with-deps dahulu (pelayan GitHub sudah ada pustaka sistem; apt-get kadang-kadang tergantung)
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], timeout=600, capture_output=True)
    if r.returncode != 0:
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                       check=True, timeout=600, capture_output=True)
    _PW_READY = True


def browser_video_url(url: str, proxy: str = "", wait_ms: int = 8000) -> tuple[str, str, str]:
    """Buka halaman dalam Chromium (tanpa paparan) seperti pelawat biasa, tangkap fail video yang dimuatkan
    oleh JavaScript halaman (Threads memuatkan video selepas halaman dibuka). Pulangkan (mp4, tajuk, thumbnail)."""
    ensure_chromium()
    from playwright.sync_api import sync_playwright

    seen: list[str] = []
    with sync_playwright() as p:
        launch = {"headless": True}
        if proxy:
            launch["proxy"] = {"server": proxy}
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="ms-MY")
        page = ctx.new_page()

        def on_response(r):
            try:
                ct = (r.headers or {}).get("content-type", "")
                if ".mp4" in r.url or ct.startswith("video/"):
                    seen.append(r.url)
            except Exception:
                pass
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        page.keyboard.press("Escape")                    # tutup popup "log masuk"
        try:
            page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.play().catch(() => {}); } }")
        except Exception:
            pass
        page.wait_for_timeout(wait_ms)
        info = page.evaluate("""() => {
            const v = document.querySelector('video');
            const og = (p) => (document.querySelector(`meta[property="${p}"]`) || {}).content || '';
            return { src: v ? (v.currentSrc || v.src || '') : '', poster: v ? (v.poster || '') : '',
                     title: og('og:title') || og('og:description') || document.title || '', image: og('og:image') };
        }""")
        browser.close()
    src = info.get("src") or ""
    mp4 = src if src.startswith("http") else next((u for u in seen if ".mp4" in u), seen[0] if seen else "")
    if not mp4:
        raise DownloadError("Tiada video dimuatkan oleh halaman (post mungkin gambar sahaja atau peribadi).")
    return strip_byte_range(mp4), info.get("title") or "", info.get("poster") or info.get("image") or ""


def _page_video_url(url: str, session=None) -> tuple[str, str, str]:
    """Sandaran untuk laman yang yt-dlp tidak kenal (cth. Threads): cari pautan mp4 dalam HTML halaman.
    Pulangkan (mp4_url, tajuk, thumbnail)."""
    s = session or requests
    r = s.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "ms,en;q=0.8"})
    r.raise_for_status()
    text = r.text

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)', text) or \
            re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', text)
        return html.unescape(m.group(1)) if m else ""

    mp4 = meta("og:video:secure_url") or meta("og:video:url") or meta("og:video")
    if not mp4:
        m = re.search(r'<(?:video|source)\b[^>]*\bsrc=["\']([^"\']+)', text, re.I)
        if m and not m.group(1).startswith("blob:"):
            mp4 = m.group(1)
    if not mp4:                                       # JSON yang di-escape dalam <script> (\"video_versions\")
        i = text.find("video_versions")
        if i >= 0:
            m = re.search(r'(https?:(?:\\\\?/){2}[^"\s<>]*?\.mp4[^"\s<>]*?)(?=\\\\?")', text[i:i + 6000])
            if m:
                mp4 = _unescape_url(m.group(1))
    if not mp4:
        m = re.search(r'"video_versions"\s*:\s*\[\s*\{[^\]]*?"url"\s*:\s*"([^"]+)"', text)
        if m:
            mp4 = m.group(1)
    if not mp4:
        m = re.search(r'https?:\\?/\\?/[^"\'\s<>]+?\.mp4[^"\'\s<>]*', text)
        if m:
            mp4 = m.group(0)
    if not mp4:
        raise DownloadError("Laman ini tidak disokong atau video tidak awam (tiada pautan video ditemui).")
    mp4 = mp4.replace("\\/", "/").encode().decode("unicode_escape") if "\\" in mp4 else mp4
    return html.unescape(mp4), meta("og:title") or meta("og:description"), meta("og:image")


def is_tiktok(url: str) -> bool:
    return bool(re.search(r"(^|\.)tiktok\.com/|//(vm|vt)\.tiktok\.com/", url or "", re.I))


def pick_tiktok_format(formats: list[dict]) -> tuple[dict | None, bool]:
    """Format TikTok TANPA watermark (play_addr), utamakan H.264 dan resolusi tertinggi.
    Pulangkan (format, tanpa_watermark)."""
    av = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    clean = [f for f in av if "watermark" not in (f.get("format_note") or "").lower()
             and not str(f.get("format_id") or "").lower().startswith("download")]
    pool = clean or av
    if not pool:
        return None, False

    def rank(f):
        h264 = 1 if str(f.get("vcodec") or "").startswith(("h264", "avc")) else 0
        return (h264, f.get("height") or 0, f.get("tbr") or 0)
    return max(pool, key=rank), bool(clean)


def fetch_video(url: str, outdir: str, max_minutes: int = 15, cookiefile: str = "", proxy: str = "") -> dict:
    """Muat turun video. Pulangkan {path, title, description, thumb, duration, watermark_free}."""
    import yt_dlp

    tiktok = {"free": None}

    def tiktok_selector(ctx):
        f, free = pick_tiktok_format(ctx.get("formats") or [])
        tiktok["free"] = free
        if f:
            yield f

    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30,
        "outtmpl": os.path.join(outdir, "video.%(ext)s"),
        # Terbaik ≤1080p; utamakan mp4 (H.264) supaya tidak perlu tukar format
        # [vcodec!=none] elak "format" gambar/storyboard atau audio sahaja
        "format": ("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080][ext=mp4][vcodec!=none]"
                   "/bv*[height<=1080][vcodec!=none]+ba/b[height<=1080][vcodec!=none]/bv*[vcodec!=none]+ba/b[vcodec!=none]"),
        "merge_output_format": "mp4",
        "http_headers": {"User-Agent": UA},
    }
    if is_tiktok(url):
        opts["format"] = tiktok_selector          # versi tanpa watermark TikTok
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if proxy:
        opts["proxy"] = proxy
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise DownloadError("Pautan ini tiada video.")
                info = entries[0]
            dur = info.get("duration") or 0
            if dur and dur > max_minutes * 60:
                raise DownloadError(f"Video terlalu panjang ({round(dur / 60)} minit, had {max_minutes} minit).")
            ydl.process_info(info)          # muat turun format yang sudah dipilih
        files = [f for f in glob.glob(os.path.join(outdir, "video.*"))
                 if f.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".ts"))]
        files = [f for f in files if _has_video(f)]
        if not files:
            raise DownloadError("Laman ini hanya beri audio atau gambar, tiada fail video. Jika YouTube, "
                                "pastikan workflow link.yml terbaru (Deno) dipasang, atau tambah cookies LINK_COOKIES.")
        path = max(files, key=os.path.getsize)
        return {"path": ensure_facebook_mp4(path), "title": info.get("title") or "",
                "description": info.get("description") or "", "thumb": info.get("thumbnail") or "",
                "duration": info.get("duration") or 0, "watermark_free": tiktok["free"]}
    except DownloadError:
        raise
    except Exception as e:
        first = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
        print(f"   yt-dlp: {first[:500]}")
        # Sandaran (cari mp4 dalam halaman) HANYA untuk laman yang yt-dlp tidak kenal (cth. Threads).
        # Untuk laman yang disokong (YouTube, TikTok, FB...) tunjukkan ralat sebenar.
        if "unsupported url" not in first.lower() or is_known_site(url):
            raise DownloadError(_friendly(first) or first[:300]) from e
        path, title, thumb, last_err = os.path.join(outdir, "video_page.mp4"), "", "", None
        for page in threads_candidates(url) or [url]:
            try:
                mp4, title, thumb = _page_video_url(page)
                download(mp4, path)
                if not _has_video(path):
                    raise DownloadError("Pautan video dalam halaman bukan fail video.")
                last_err = None
                break
            except Exception as e2:
                last_err = e2
        if last_err is not None and threads_candidates(url):
            # Threads memuatkan video melalui JavaScript → buka dalam Chromium tanpa paparan
            try:
                print("   Threads: cuba melalui browser (Chromium)…")
                mp4, title, thumb = browser_video_url(threads_candidates(url)[1], env("PROXY_URL"))
                download(mp4, path)
                if not _has_video(path):
                    raise DownloadError("Fail yang dimuatkan oleh halaman bukan video.")
                last_err = None
            except Exception as e3:
                last_err = e3
        if last_err is not None:
            raise DownloadError(f"Laman tidak disokong atau video tidak awam ({str(last_err)[:150]}).") from e
        return {"path": ensure_facebook_mp4(path), "title": title, "description": "", "thumb": thumb, "duration": 0}


def is_known_site(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be|tiktok\.com|facebook\.com|fb\.watch|instagram\.com)", url or "", re.I))


def _friendly(msg: str) -> str:
    m = msg.lower()
    if "sign in to confirm" in m or "not a bot" in m:
        return "YouTube minta log masuk (sekatan bot). Tambah cookies YouTube dalam secret LINK_COOKIES."
    if "private" in m or "login" in m or "log in" in m:
        return "Video ini peribadi atau perlu log masuk."
    if "requested format is not available" in m or "only images are available" in m:
        return ("YouTube tidak beri format video. Pastikan workflow link.yml terbaru (dengan Deno) dipasang, "
                "atau tambah cookies YouTube dalam secret LINK_COOKIES.")
    if "unsupported url" in m:
        return ""
    if "po token" in m or "http error 403" in m or "403: forbidden" in m:
        return "YouTube menyekat muat turun dari pelayan GitHub (403). Tambah cookies YouTube dalam secret LINK_COOKIES."
    if "unavailable" in m or "removed" in m or "not available" in m:
        return "Video tidak lagi tersedia."
    return re.sub(r"\x1b\[[0-9;]*m", "", msg)[:300]


# ------------------------------------------------------------------ buang muzik (kekal suara)
_DEMUCS_READY = False


def ensure_demucs() -> None:
    """Pasang Demucs (pemisah suara) hanya bila diperlukan — torch versi CPU supaya cepat dipasang."""
    global _DEMUCS_READY
    if _DEMUCS_READY:
        return
    try:
        import demucs  # noqa: F401
    except ImportError:
        pip = [sys.executable, "-m", "pip", "install", "-q"]
        subprocess.run(pip + ["torch==2.5.1", "torchaudio==2.5.1", "--index-url",
                              "https://download.pytorch.org/whl/cpu"], check=True, timeout=900)
        subprocess.run(pip + ["demucs==4.0.1", "soundfile"], check=True, timeout=900)
    _DEMUCS_READY = True


def has_audio(path: str) -> bool:
    return any(s.get("codec_type") == "audio" for s in _probe(path).get("streams") or [])


def remove_music(path: str, separator=None) -> str:
    """Buang muzik & bunyi latar, kekalkan suara (Demucs htdemucs, --two-stems=vocals).
    `separator(wav_in, out_dir) -> vocals_wav` boleh diganti untuk ujian."""
    if not has_audio(path):
        return path
    work = os.path.dirname(path)
    wav = os.path.join(work, "audio.wav")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "2", "-ar", "44100", wav],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise DownloadError(f"Gagal ambil audio: {r.stderr[-200:]}")
    if separator is None:
        def separator(wav_in, out_dir):
            ensure_demucs()
            sep = subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                                  "-o", out_dir, wav_in], capture_output=True, text=True, timeout=3600)
            out = os.path.join(out_dir, "htdemucs", os.path.splitext(os.path.basename(wav_in))[0], "vocals.wav")
            if sep.returncode != 0 or not os.path.exists(out):
                raise DownloadError(f"Gagal buang muzik: {(sep.stderr or sep.stdout)[-300:]}")
            return out
    vocals = separator(wav, os.path.join(work, "sep"))
    out = os.path.splitext(path)[0] + "_vocal.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-i", vocals, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not os.path.exists(out):
        raise DownloadError(f"Gagal gabung suara ke video: {r.stderr[-200:]}")
    return out


def screenshot(path: str, duration: float = 0) -> str:
    """Satu gambar dari video (sekitar 30% tempoh, elak bingkai hitam di awal) untuk komen pertama."""
    if not duration:
        try:
            duration = float((_probe(path).get("format") or {}).get("duration") or 0)
        except ValueError:
            duration = 0
    t = max(0.5, min(duration * 0.3, max(duration - 0.5, 0.5))) if duration else 1.0
    out = os.path.splitext(path)[0] + "_shot.jpg"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale='min(1080,iw)':-2", "-q:v", "3", out], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(out):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-frames:v", "1", "-q:v", "3", out],
                           capture_output=True, text=True, timeout=120)
    if not os.path.exists(out):
        raise DownloadError("Gagal ambil screenshot video.")
    return out


# ------------------------------------------------------------------ satu pautan
def targets_for(pc: dict, pages: list[dict]) -> list[dict]:
    if pc.get("target_mode") == "selected":
        ids = {str(x) for x in pc.get("target_page_ids") or []}
        return [p for p in pages if str(p["page_id"]) in ids]
    return list(pages)


def times_for(item: dict) -> list[str | None]:
    """Bila post: 'now' = [segera], 'at' = setiap masa dipilih (semua Page serentak pada setiap masa),
    lain = [None] (slot kosong seterusnya). Masa yang sudah lepas → post segera."""
    mode = item.get("schedule_mode") or "queue"
    now = dt.datetime.now(dt.timezone.utc)
    if mode == "now":
        return [now.isoformat(timespec="seconds")]
    if mode == "at":
        raw = item.get("schedule_times") or ([item["schedule_at"]] if item.get("schedule_at") else [])
        out = []
        for v in raw:
            at = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            out.append((at if at > now else now).isoformat(timespec="seconds"))
        if out:
            return out
    return [None]


def when_for(item: dict) -> str | None:
    return times_for(item)[0]


def slots_for(pc: dict) -> tuple[dict | None, int]:
    if pc.get("slot_mode") == "custom":
        return pc.get("slots") or None, int(pc.get("stagger") or 0)
    return {"source": "profile"}, 0


def process_link(item: dict, pc: dict, pages: list[dict], settings: Settings, history: list[str], api,
                 fetcher=fetch_video, ask_factory=gemini_ask_factory, log=print, music_remover=remove_music) -> dict:
    rec = {"status": "error", "scheduled": [], "detail": {}}
    targets = targets_for(pc, pages)
    if not targets:
        rec["error"] = "Tiada page penerima dipilih untuk profile ini (halaman Video Link → Tetapan)."
        return rec
    suffix = pc["caption_suffix"] if isinstance(pc.get("caption_suffix"), str) else cap.SUFFIX
    max_words = max(3, min(25, int(pc.get("caption_max_words") or cap.MAX_WORDS)))
    short = pc.get("caption_short", True) is not False

    with tempfile.TemporaryDirectory() as td:
        v = fetcher(item["url"], td, int(env("LINK_MAX_MINUTES", "15") or 15), env("COOKIES_FILE"), env("PROXY_URL"))
        rec["title"] = (v.get("title") or "")[:300]
        rec["thumb_url"] = v.get("thumb") or None
        if v.get("watermark_free") is False:
            rec["detail"]["note"] = "TikTok: versi tanpa watermark tiada, video dengan watermark digunakan."
        if item.get("remove_music"):
            log("   buang muzik, kekal suara…")
            v["path"] = music_remover(v["path"])
            rec["detail"]["music_removed"] = True
        context = " ".join(x for x in [v.get("title"), (v.get("description") or "")[:500]] if x)
        ask, cleanup = ask_factory(settings, v["path"])
        try:
            caps, rejected = cap.generate(v["path"], len(targets), context, history, ask,
                                          max_words=max_words, suffix=suffix, short=short)
        finally:
            cleanup()
        if rejected:
            rec["detail"]["caption_rejected"] = rejected[:10]
        if not caps:
            rec["error"] = "Gemini gagal hasilkan caption yang menepati syarat."
            return rec
        if len(caps) < len(targets):
            log(f"   nota: hanya {len(caps)} caption sah untuk {len(targets)} page; page selebihnya dilangkau.")
        posts = [{"pageId": str(p["page_id"]), "message": c} for p, c in zip(targets, caps)]
        comment = (item.get("comment_text") or "").strip() or None
        comment_img = None
        if comment and item.get("comment_image", True):
            comment_img = api.upload(screenshot(v["path"], v.get("duration") or 0), "image/jpeg")
        video_url = api.upload(v["path"])

    slots, stagger = slots_for(pc)
    names = {str(p["page_id"]): p["page_name"] for p in pages}
    msg = {p["pageId"]: p["message"] for p in posts}
    created, skipped = [], []
    times = times_for(item)
    for i, when in enumerate(times):
        # Setiap masa ada sourceId sendiri supaya semakan "sudah dijadualkan" tidak menghalang masa kedua dst.
        sid = f"link:{item['id']}" if i == 0 else f"link:{item['id']}@{i}"
        res = api.schedule(video_url, sid, posts, item["profile"], stagger, slots,
                           first_comment=comment, comment_image_url=comment_img, when=when)
        created += res.get("created", [])
        skipped += res.get("skipped", [])
    res = {"created": created, "skipped": skipped}
    rec["scheduled"] = [{"page": names.get(c["pageId"], c["pageId"]), "message": msg.get(c["pageId"], ""),
                         "when": c["scheduled_at"]} for c in res.get("created", [])]
    already = [s for s in res.get("skipped", []) if s.get("reason") == "sudah_dijadualkan"]
    if rec["scheduled"] or already:
        rec["status"] = "posted"
        rec["error"] = None
        history.extend(cap.clean_body(p["message"], [suffix]) for p in posts)
    else:
        reasons = ", ".join(sorted({s.get("reason", "") for s in res.get("skipped", [])})) or "tiada slot"
        rec["error"] = f"Tiada post dijadualkan ({reasons})."
    return rec


# ------------------------------------------------------------------ main
def run_links(api, remote: dict, pages: list[dict], settings: Settings, limit: int,
              process=process_link, log=print) -> list[tuple[dict, dict]]:
    profiles = (remote.get("settings") or {}).get("profiles") or {}
    history = [cap.clean_body(x, [p.get("caption_suffix", "") for p in profiles.values()])
               for x in remote.get("captions") or []]
    done = []
    items = (remote.get("items") or [])[:limit]
    for i, item in enumerate(items, 1):
        pc = profiles.get(item["profile"]) or {}
        log(f"[{i}/{len(items)}] {item['url']} → profile '{item['profile']}'")
        try:
            api.save_links([{"id": item["id"], "status": "processing"}])
        except Exception as e:
            log(f"   gagal kemas kini status: {e}")
        try:
            rec = process(item, pc, pages, settings, history, api)
        except Exception as e:
            rec = {"status": "error", "error": str(e)[:500], "scheduled": [], "detail": {}}
        if rec["status"] == "error":
            rec["tries"] = int(item.get("tries") or 0) + 1
            log(f"   RALAT (cubaan {rec['tries']}/{MAX_TRIES}): {rec.get('error')}")
        else:
            rec["tries"] = 0
            log(f"   DIJADUALKAN ke {len(rec['scheduled'])} page")
            for s in rec["scheduled"]:
                log(f"     → {s['page']} @ {s['when']}: {s['message']}")
        try:
            api.save_links([{"id": item["id"], **rec}])
        except Exception as e:
            log(f"   gagal simpan keputusan: {e}")
        done.append((item, rec))
        time.sleep(1)
    if not items:
        log("Tiada pautan menunggu.")
    return done


def run() -> int:
    settings = Settings()
    key = env("POSTOLAB_API_KEY")
    if not key or not settings.gemini_api_key:
        print("POSTOLAB_API_KEY / GEMINI_API_KEY belum diset.", file=sys.stderr)
        return 2
    settings.visual_provider = "gemini"
    api = LinkApi(env("POSTOLAB_URL", "https://postolab.com").rstrip("/"), key)
    remote = api.link_remote()
    if remote is None:
        print("Halaman Video Link belum dipasang di Postolab.")
        return 0
    pages = api.pages()
    print(f"Pautan menunggu: {len(remote.get('items') or [])} | Postolab: {len(pages)} page")
    done = run_links(api, remote, pages, settings, int(env("LINK_MAX_PER_RUN", "10") or 10))
    _summary(done)
    return 0


def _summary(done):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"## Video Link → Postolab ({dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC)\n\n"
                "| Pautan | Status | Caption / sebab |\n|---|---|---|\n")
        for it, r in done:
            detail = "<br>".join(f"{s['page']}: {s['message']}" for s in r.get("scheduled", [])) or (r.get("error") or "")
            f.write(f"| {it['url']} | {r['status']} | {detail.replace('|', '/')} |\n")


if __name__ == "__main__":
    sys.exit(run())
