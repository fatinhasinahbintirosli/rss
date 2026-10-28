"""Facebook Reels -> RSS, headless version of rss.py for GitHub Actions.

Same approach as the desktop app (Playwright lists the reels, yt-dlp reads caption + mp4),
without the Tkinter window and local server. The RSS is uploaded to Cloudflare R2 so it
has a public link that works while the PC is off.

Run:   python fb_reels_rss.py
Env:   see .env.example
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from xml.dom import minidom

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Facebook's "See more" button text that ends up in scraped captions
SEE_MORE = re.compile(r"[\s.…]*i?see[-\s]?mo\.?re?n?\s*$|[\s.…]*(?:see more|lihat lagi)\s*$", re.I)
LOGIN_WALL = re.compile(r"(log ?in|log masuk|sign up|daftar).{0,40}(facebook|to continue|untuk teruskan)", re.I)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Reel:
    id: str
    url: str
    caption: str = ""
    caption_truncated: bool = False
    mp4: str = ""
    width: int | None = None
    height: int | None = None
    thumb: str = ""
    first_seen: str = ""
    error: str = ""
    source_page: str = ""


@dataclass
class RunReport:
    page_url: str
    started: str
    reel_links_found: int = 0
    with_mp4: int = 0
    with_caption: int = 0
    blocked_signals: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    uploaded_to: str = ""


# ------------------------------------------------------------------ helpers
def _numeric_page_id(url: str) -> str:
    """Page baharu Facebook tiada nama pengguna: profile.php?id=615... atau facebook.com/615..."""
    m = re.search(r"facebook\.com/profile\.php\?(?:[^#]*&)?id=(\d+)", url) or \
        re.search(r"facebook\.com/(\d{6,})(?:[/?#]|$)", url)
    return m.group(1) if m else ""


def page_slug(url: str) -> str:
    pid = _numeric_page_id(url or "")
    if pid:
        return pid
    m = re.search(r"facebook\.com/([^/?#]+)", url or "")
    return re.sub(r"[^a-zA-Z0-9_-]", "", m.group(1)) if m else ""


def normalize_reels_url(raw: str) -> tuple[str, str]:
    url = raw.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    pid = _numeric_page_id(url)
    if pid:
        return f"https://www.facebook.com/profile.php?id={pid}&sk=reels_tab", pid
    if "/reels" not in url and "/reel/" not in url:
        url += "/reels/"
    elif url.endswith("/reels"):
        url += "/"
    m = re.search(r"facebook\.com/([^/?#]+)", url)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", m.group(1)) if m else "fb_page"
    return url, slug


def clean_caption(text: str) -> tuple[str, bool]:
    text = (text or "").strip()
    cleaned = SEE_MORE.sub("", text).rstrip()
    return cleaned, cleaned != text


def pick_best_mp4(info: dict) -> tuple[str, int | None, int | None]:
    """Highest-resolution mp4 that has BOTH video and audio (rss.py took the last one in the list,
    which is sometimes SD)."""
    best = None
    for f in info.get("formats") or []:
        if not f.get("url") or f.get("ext") != "mp4":
            continue
        if f.get("vcodec") in (None, "none") or f.get("acodec") == "none":
            continue
        key = (f.get("height") or 0, f.get("tbr") or 0)
        if best is None or key > best[0]:
            best = (key, f)
    if best:
        f = best[1]
        return f["url"], f.get("width"), f.get("height")
    if info.get("url"):
        return info["url"], info.get("width"), info.get("height")
    return "", None, None


# ------------------------------------------------------------------ scraping
def list_reel_urls(page_url: str, max_items: int, proxy: str = "", debug_dir: str = "") -> tuple[list[str], list[str]]:
    """Open the Page's reels tab and collect /reel/ links. Returns (urls, blocked_signals)."""
    from playwright.sync_api import sync_playwright

    signals: list[str] = []
    with sync_playwright() as p:
        launch = {"headless": True}
        if proxy:
            launch["proxy"] = {"server": proxy}
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="ms-MY")
        page = ctx.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        page.keyboard.press("Escape")
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1500)")
            page.wait_for_timeout(1500)
        links = page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/reel/"]'))
                                         .map(a => a.href.split('?')[0])""")
        body = page.inner_text("body")[:5000] if not links else ""
        if not links:
            if LOGIN_WALL.search(body):
                signals.append("Halaman minta log masuk (login wall)")
            if re.search(r"facebook\.com/(login|checkpoint)", page.url):
                signals.append(f"Dialih ke {page.url}")
            if debug_dir:
                os.makedirs(debug_dir, exist_ok=True)
                page.screenshot(path=os.path.join(debug_dir, "reels_page.png"), full_page=False)
                with open(os.path.join(debug_dir, "reels_page.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
        browser.close()

    urls, seen = [], set()
    for u in links:
        m = re.search(r"/reel/(\d+)", u)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            urls.append(f"https://www.facebook.com/reel/{m.group(1)}/")
            if len(urls) >= max_items:
                break
    return urls, signals


def extract_reel(reel_url: str, proxy: str = "", cookiefile: str = "") -> Reel:
    import yt_dlp

    rid = re.search(r"/reel/(\d+)", reel_url).group(1)
    reel = Reel(id=rid, url=reel_url)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "user_agent": UA}
    if proxy:
        opts["proxy"] = proxy
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(reel_url, download=False) or {}
    except Exception as e:
        reel.error = str(e)[:300]
        return reel
    reel.caption, reel.caption_truncated = clean_caption(info.get("description") or "")
    reel.mp4, reel.width, reel.height = pick_best_mp4(info)
    reel.thumb = info.get("thumbnail") or ""
    return reel


# ------------------------------------------------------------------ sandaran pelayar
# Bila yt-dlp gagal ("Cannot parse data" — Facebook tukar format halaman), buka reel dalam Chromium
# dan cari pautan video (yang ada suara) terus dalam HTML halaman.
_VIDEO_KEYS = ("browser_native_hd_url", "playable_url_quality_hd", "browser_native_sd_url", "playable_url")


def _unescape_json(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s.replace("\\/", "/")


def parse_reel_html(text: str) -> dict:
    """Pulangkan {mp4, caption, thumb} dari HTML halaman reel. mp4 kosong jika tiada."""
    out = {"mp4": "", "caption": "", "thumb": ""}
    for key in _VIDEO_KEYS:
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)+)"', text)
        if m:
            url = _unescape_json(m.group(1))
            if url.startswith("http"):
                out["mp4"] = url
                break

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)', text) or \
            re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']', text)
        return html.unescape(m.group(1)).strip() if m else ""

    cap = meta("og:description") or meta("description")
    if not cap:
        m = re.search(r'"message"\s*:\s*\{[^{}]*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        cap = _unescape_json(m.group(1)) if m else ""
    out["caption"] = cap
    out["thumb"] = meta("og:image")
    return out


def _netscape_cookies(path: str) -> list[dict]:
    cookies = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, cpath, secure, expires, name, value = parts[:7]
                c = {"name": name, "value": value, "domain": domain, "path": cpath or "/",
                     "secure": secure.upper() == "TRUE"}
                if expires.isdigit() and int(expires) > 0:
                    c["expires"] = int(expires)
                cookies.append(c)
    except OSError:
        pass
    return cookies


def browser_fill(reels: list["Reel"], proxy: str = "", cookiefile: str = "") -> int:
    """Cuba isi mp4/caption untuk reel yang gagal dibaca yt-dlp. Satu pelayar untuk semua. Pulangkan bilangan berjaya."""
    todo = [r for r in reels if not r.mp4]
    if not todo:
        return 0
    from playwright.sync_api import sync_playwright

    fixed = 0
    with sync_playwright() as p:
        launch = {"headless": True}
        if proxy:
            launch["proxy"] = {"server": proxy}
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="ms-MY")
        if cookiefile:
            ck = _netscape_cookies(cookiefile)
            if ck:
                try:
                    ctx.add_cookies(ck)
                except Exception as e:  # noqa: BLE001
                    print(f"      cookies tidak dapat dimuat ke pelayar: {e}"[:200])
        page = ctx.new_page()
        for r in todo:
            try:
                page.goto(r.url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                got = parse_reel_html(page.content())
            except Exception as e:  # noqa: BLE001
                got, r.error = {"mp4": ""}, f"{r.error} | pelayar: {e}"[:300]
            if got.get("mp4"):
                r.mp4 = got["mp4"]
                if not r.caption and got.get("caption"):
                    r.caption, r.caption_truncated = clean_caption(got["caption"])
                r.thumb = r.thumb or got.get("thumb", "")
                r.error = ""
                fixed += 1
            time.sleep(1.0)
        browser.close()
    return fixed


# ------------------------------------------------------------------ RSS
def build_rss(slug: str, page_url: str, reels: list[Reel]) -> str:
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = f"{slug} - Reels Kapsyen Penuh"
    ET.SubElement(ch, "link").text = page_url
    ET.SubElement(ch, "description").text = f"Suapan RSS dengan Kapsyen Sebenar untuk {slug}"
    ET.SubElement(ch, "lastBuildDate").text = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    for r in reels:
        it = ET.SubElement(ch, "item")
        ET.SubElement(it, "title").text = (r.caption.split("\n")[0][:80] or f"Reel #{r.id}")
        ET.SubElement(it, "link").text = r.url
        ET.SubElement(it, "guid", isPermaLink="true").text = r.url
        ET.SubElement(it, "pubDate").text = r.first_seen
        src = page_slug(r.source_page or "")
        if src:
            ET.SubElement(it, "category").text = src
        d = ""
        if r.thumb:
            d += f'<p><img src="{html.escape(r.thumb)}" style="max-width:400px;"/></p>'
        d += f'<p><b>Kapsyen Penuh:</b><br/>{html.escape(r.caption).replace(chr(10), "<br/>")}</p>'
        if r.mp4:
            d += (f'<p><b>Pautan Direct MP4:</b><br/><a href="{html.escape(r.mp4)}">🔗 Muat Turun / Main Fail MP4</a>'
                  + (f" ({r.width}x{r.height})" if r.width and r.height else "") + "</p>")
            ET.SubElement(it, "enclosure", url=r.mp4, type="video/mp4", length="0")
        d += f'<p><a href="{r.url}">👉 Tonton Video Penuh di Facebook</a></p>'
        ET.SubElement(it, "description").text = d
    return minidom.parseString(ET.tostring(rss, encoding="utf-8")).toprettyxml(indent="  ")


def merge_with_previous(new: list[Reel], previous: list[Reel], keep: int) -> list[Reel]:
    """Keep first_seen dates stable and keep older items if this run saw fewer reels."""
    prev = {r.id: r for r in previous}
    out: list[Reel] = []
    now = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    for r in new:
        old = prev.pop(r.id, None)
        r.first_seen = old.first_seen if old and old.first_seen else now
        if old and not r.mp4 and old.mp4:          # extraction failed this time; keep last good data
            r.mp4, r.width, r.height = old.mp4, old.width, old.height
        if old and not r.caption and old.caption:
            r.caption, r.caption_truncated = old.caption, old.caption_truncated
        out.append(r)
    out.extend(prev.values())
    return out[:keep]


# ------------------------------------------------------------------ storage (Cloudflare R2, S3 API)
def r2_client():
    import boto3
    return boto3.client("s3", endpoint_url=f"https://{env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
                        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"), region_name="auto")


def r2_get_json(client, bucket: str, key: str) -> list[dict]:
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except Exception:
        return []


def r2_put(client, bucket: str, key: str, body: bytes, content_type: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type,
                      CacheControl="max-age=60")


# ------------------------------------------------------------------ main
DEFAULT_PAGES = "https://www.facebook.com/penmerahdotcoms/reels/,https://www.facebook.com/buletinmalaysiakuu/reels/"


def postolab_sources(fetch=None) -> list[str]:
    """Gabungan sumber RSS semua profile yang aktif di halaman Auto Reels (Postolab). [] jika tiada/ralat."""
    if not env("POSTOLAB_API_KEY"):
        return []
    try:
        if fetch is None:
            from postolab_relay import Postolab
            fetch = Postolab(env("POSTOLAB_URL", "https://postolab.com").rstrip("/"), env("POSTOLAB_API_KEY")).remote
        remote = fetch()
    except Exception as e:
        print(f"Tidak dapat baca sumber dari Postolab ({e}); guna FB_PAGE_URLS.")
        return []
    if not remote:
        return []
    out = []
    for pc in (remote.get("settings") or {}).get("profiles", {}).values():
        if pc.get("enabled"):
            out += [u for u in pc.get("sources") or [] if u not in out]
    return out


def page_list(fetch=None) -> list[str]:
    """Sumber dari halaman Auto Reels Postolab jika ada; jika tidak FB_PAGE_URLS (koma / baris baru)."""
    remote = postolab_sources(fetch)
    if remote:
        print(f"Sumber dari Postolab Auto Reels: {len(remote)} page")
        return remote
    raw = env("FB_PAGE_URLS") or env("FB_PAGE_URL") or DEFAULT_PAGES
    return [u.strip() for u in re.split(r"[,\n]", raw) if u.strip()]


def run_page(raw_url: str, client, cfg: dict) -> tuple[RunReport, list[Reel], str]:
    page_url, slug = normalize_reels_url(raw_url)
    debug_dir = os.path.join(cfg["debug_dir"], slug)
    rep = RunReport(page_url=page_url, started=dt.datetime.now(dt.timezone.utc).isoformat())
    print(f"\n=== {slug} ===\n[1/3] Buka {page_url}")
    try:
        urls, signals = list_reel_urls(page_url, cfg["max_items"], cfg["proxy"], debug_dir)
    except Exception as e:
        urls, signals = [], [f"Playwright gagal: {e}"[:300]]
    rep.reel_links_found, rep.blocked_signals = len(urls), signals
    print(f"      {len(urls)} pautan reel dijumpai" + (f" | {'; '.join(signals)}" if signals else ""))

    reels: list[Reel] = []
    for i, u in enumerate(urls, 1):
        r = extract_reel(u, cfg["proxy"], cfg["cookiefile"])
        r.source_page = page_url
        size = f"{r.width}x{r.height}" if r.height else "?"
        print(f"[2/3] {i}/{len(urls)} {r.id}: mp4={'ya' if r.mp4 else 'TIADA'} ({size}) "
              f"caption={len(r.caption)} aksara" + (f" ralat={r.error}" if r.error else ""))
        if r.error:
            rep.errors.append(f"{r.id}: {r.error}")
        reels.append(r)
        time.sleep(1.5)        # be gentle
    failed = [r for r in reels if not r.mp4]
    if failed:
        print(f"      yt-dlp gagal untuk {len(failed)} reel. Cuba melalui pelayar...")
        try:
            n = browser_fill(failed, cfg["proxy"], cfg["cookiefile"])
            print(f"      Pelayar berjaya dapatkan {n}/{len(failed)} video")
            if n:
                ok_ids = {r.id for r in failed if r.mp4}
                rep.errors = [e for e in rep.errors if e.split(":", 1)[0] not in ok_ids]
        except Exception as e:  # noqa: BLE001
            print(f"      Pelayar gagal: {e}"[:300])
    rep.with_mp4 = sum(1 for r in reels if r.mp4)
    rep.with_caption = sum(1 for r in reels if r.caption)

    prefix, bucket = cfg["prefix"], cfg["bucket"]
    state_key, xml_key = f"{prefix}{slug}.json", f"{prefix}{slug}.xml"
    previous = [Reel(**d) for d in r2_get_json(client, bucket, state_key)] if client else []
    if not reels and previous:
        print("[3/3] Tiada reel diperoleh kali ini. RSS lama dikekalkan (tidak ditimpa).")
        return rep, previous, slug

    merged = merge_with_previous(reels, previous, cfg["keep"])
    xml = build_rss(slug, page_url, merged)
    os.makedirs("out", exist_ok=True)
    with open(f"out/{slug}.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    if client:
        r2_put(client, bucket, xml_key, xml.encode("utf-8"), "application/rss+xml; charset=utf-8")
        r2_put(client, bucket, state_key, json.dumps([asdict(r) for r in merged], ensure_ascii=False).encode(),
               "application/json")
        rep.uploaded_to = f"{cfg['public_base']}/{xml_key}" if cfg["public_base"] else f"r2://{bucket}/{xml_key}"
        print(f"[3/3] RSS dikemas kini: {rep.uploaded_to}")
    else:
        print(f"[3/3] R2 tidak diset. RSS disimpan di out/{slug}.xml")
    return rep, merged, slug


def _pub_ts(r: Reel) -> float:
    try:
        return dt.datetime.strptime(r.first_seen, "%a, %d %b %Y %H:%M:%S GMT").timestamp()
    except (TypeError, ValueError):
        return 0.0


def run() -> int:
    cfg = {
        "max_items": int(env("MAX_REELS", "10")),
        "keep": int(env("KEEP_ITEMS", "30")),
        "proxy": env("PROXY_URL"),
        "debug_dir": env("DEBUG_DIR", "debug"),
        "cookiefile": env("COOKIES_FILE"),
        "bucket": env("R2_BUCKET"),
        "prefix": env("R2_PREFIX", "rss/"),
        "public_base": env("R2_PUBLIC_BASE_URL").rstrip("/"),
    }
    client = r2_client() if cfg["bucket"] else None

    reports: list[RunReport] = []
    combined: list[Reel] = []
    for url in page_list():
        rep, items, slug = run_page(url, client, cfg)
        reports.append(rep)
        combined.extend(items)

    # One feed with every page's reels, newest first (what the checker will read).
    combined.sort(key=_pub_ts, reverse=True)
    xml = build_rss("semua", "", combined)
    os.makedirs("out", exist_ok=True)
    with open("out/semua.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    if client and combined:
        r2_put(client, cfg["bucket"], f"{cfg['prefix']}semua.xml", xml.encode("utf-8"),
               "application/rss+xml; charset=utf-8")
        link = (f"{cfg['public_base']}/{cfg['prefix']}semua.xml" if cfg["public_base"]
                else f"r2://{cfg['bucket']}/{cfg['prefix']}semua.xml")
        print(f"\nRSS gabungan ({len(combined)} item): {link}")

    _write_report(reports, cfg["debug_dir"])
    # Page tanpa reel → amaran sahaja (page lain tetap jalan). Merah hanya jika SEMUA page gagal.
    for r in reports:
        if not r.with_mp4:
            print(f"::warning title=Tiada reel::{r.page_url} - 0 reel dijumpai. Semak pautan page "
                  "atau pastikan page itu ada tab Reels yang awam.")
    return 0 if any(r.with_mp4 for r in reports) else 3


def _write_report(reports: list[RunReport], debug_dir: str) -> None:
    os.makedirs(debug_dir, exist_ok=True)
    with open(os.path.join(debug_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in reports], f, ensure_ascii=False, indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as f:
        f.write("## Keputusan larian\n\n| Page | Pautan reel | Ada mp4 | Ada caption | RSS |\n|---|---|---|---|---|\n")
        for r in reports:
            f.write(f"| {r.page_url} | {r.reel_links_found} | {r.with_mp4} | {r.with_caption} | {r.uploaded_to} |\n")
        for r in reports:
            if r.blocked_signals:
                f.write(f"\n**{r.page_url} - tanda disekat:** " + "; ".join(r.blocked_signals) + "\n")
            if r.errors:
                f.write(f"\n**{r.page_url} - ralat yt-dlp:**\n" + "\n".join(f"- {e}" for e in r.errors[:10]) + "\n")


if __name__ == "__main__":
    sys.exit(run())
