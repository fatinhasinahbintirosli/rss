"""Keyword/regex rules (Malay + English) for text, speech and captions.

Rules are a cheap first pass that always runs, even without an LLM key.
They catch obvious patterns; the LLM review catches context (hate, harassment...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import BLOCK, INFO, WARN


@dataclass(frozen=True)
class Rule:
    category: str
    severity: str
    pattern: re.Pattern
    note: str          # Malay explanation
    fix: str = ""


def _r(cat: str, sev: str, pattern: str, note: str, fix: str = "") -> Rule:
    return Rule(cat, sev, re.compile(pattern, re.I | re.U), note, fix)


RULES: list[Rule] = [
    # --- engagement bait (Facebook reduces reach for these) ---
    _r("engagement_bait", WARN,
       r"\b(share|kongsi)(kan)?\b.{0,25}\b(untuk|utk|nak|to)\b.{0,15}\b(menang|win|dapat|hadiah)",
       "Minta share untuk menang/dapat sesuatu.", "Buang ayat yang meminta share/tag/komen sebagai syarat."),
    _r("engagement_bait", WARN, r"\btag\s*\d+\s*(orang\s*)?(kawan|friends?|rakan)",
       "Minta tag kawan.", "Buang ayat 'tag N kawan'."),
    _r("engagement_bait", WARN,
       r"\b(type|taip|tulis|komen|comment)\s*['\"“]?\s*(amin|aamiin|amiin|yes|ya|setuju|nak|done)\b",
       "Minta komen perkataan tertentu.", "Ajak penonton berbincang, bukan taip satu perkataan."),
    _r("engagement_bait", WARN,
       r"\b(like|suka)\s*(kalau|jika|if)\b|\bshare\s*(kalau|jika|if)\s*(setuju|anda|you)",
       "Minta like/share bersyarat.", "Buang ayat 'like/share kalau...'."),
    _r("engagement_bait", WARN, r"\b(jangan|don'?t)\s*(scroll|skip)\b.{0,30}\b(sial|bad luck|nasib malang|celaka)",
       "Ugutan nasib malang untuk dapat engagement.", "Buang ayat ugutan."),

    # --- get rich quick / financial scams ---
    _r("get_rich_quick", WARN,
       r"\b(untung|profit|pendapatan|income|gaji)\b.{0,20}\bRM\s?\d[\d,.]*\s*(k\b)?.{0,15}\b(sehari|seminggu|sejam|per day|a day|daily)",
       "Janji pendapatan tertentu dalam masa singkat.", "Buang angka pendapatan atau tambah bukti/penafian yang jelas."),
    _r("get_rich_quick", WARN,
       r"\b(jamin|dijamin|guaranteed?|pasti|confirm)\s*(untung|profit|returns?|pulangan|kaya)",
       "Janji untung terjamin.", "Elak perkataan 'jamin untung'."),
    _r("get_rich_quick", WARN, r"\b\d{1,3}\s?%\s*(sebulan|seminggu|sehari|per month|monthly|weekly|daily)\b",
       "Janji peratus pulangan tetap.", "Buang janji pulangan tetap."),
    _r("get_rich_quick", WARN, r"\b(cepat|mudah|easy|fast)\s*(kaya|rich)\b|\bget rich\b|\bduit\s*(cepat|mudah)\b",
       "Ayat 'cepat kaya'.", ""),
    _r("scam_fraud", BLOCK, r"\b(gandakan|double)\s*(duit|wang|money|crypto|bitcoin)\b",
       "Tawaran menggandakan duit (corak penipuan).", "Buang tawaran ini."),

    # --- health claims ---
    _r("health_claims", WARN,
       r"\b(turun|kurus|hilang|lose|lost?)\s*\d{1,3}\s?(kg|kilo|lbs?|pounds?)\b.{0,25}\b(seminggu|sebulan|hari|days?|weeks?|minggu)",
       "Dakwaan turun berat badan dalam tempoh tertentu.", "Buang angka kg dan tempoh."),
    _r("health_claims", WARN,
       r"\b(sembuh(kan)?|ubat(i)?|cure[sd]?|heal|rawat)\b.{0,20}\b(kencing manis|diabetes|kanser|cancer|darah tinggi|hypertension|buah pinggang|kidney|strok|stroke|autisme|autism|hiv)",
       "Dakwaan menyembuhkan penyakit serius.", "Buang dakwaan rawatan; Facebook dan KKM tidak benarkan."),
    _r("health_claims", WARN, r"\b100\s?%\s*(berkesan|sembuh|effective|cure|selamat|safe)",
       "Dakwaan 100% berkesan/selamat.", ""),
    _r("health_claims", WARN, r"\b(tanpa|no|without)\s*(diet|senaman|exercise)\b.{0,20}\b(kurus|turun|lose)",
       "Dakwaan kurus tanpa diet/senaman.", ""),
    _r("health_claims", WARN, r"\bubat\s*(kuat|batin)\b|\bpenambah\s*saiz\b|\bpembesar\b",
       "Produk kesihatan seksual.", "Produk ini terhad di Facebook."),

    # --- giveaways / phishing ---
    _r("scam_fraud", BLOCK, r"\b(iphone|ipad|samsung|kereta|motor|rumah)\s*(percuma|free)\b.{0,30}\b(klik|click|daftar|isi|link)",
       "Giveaway barang mahal + klik link (corak penipuan).", "Buang tawaran atau buat giveaway rasmi dengan syarat jelas."),
    _r("scam_fraud", BLOCK,
       r"\b(akaun|account|page)\s*(anda|awak|you(r)?)\s*(akan\s*)?(digantung|disekat|dipadam|will be (suspended|disabled|deleted))",
       "Ayat tiruan notis Facebook (phishing).", "Buang ayat ini."),
    _r("scam_fraud", BLOCK, r"\b(verify|sahkan)\s*(akaun|account)\b.{0,30}\b(link|klik|click)",
       "Minta sahkan akaun melalui link.", ""),

    # --- gambling ---
    _r("gambling", BLOCK,
       r"\b(mega888|scr888|918\s?kiss|pussy888|xe88|lpe88|joker123|ace333|live22|slot\s*online|casino\s*online|online\s*casino)\b",
       "Promosi judi online.", "Kandungan judi online tidak dibenarkan tanpa kebenaran bertulis Meta."),
    _r("gambling", WARN, r"\b(judi|taruhan|bertaruh|betting|bet\s*sekarang|free\s*credit|kredit\s*percuma)\b",
       "Rujukan perjudian.", ""),

    # --- drugs / regulated goods ---
    _r("drugs", BLOCK, r"\b(jual|beli|ada stok|ready stock|order)\b.{0,25}\b(ganja|syabu|sabu|pil kuda|ecstasy|ketum|kush|weed|cannabis|meth)\b",
       "Jual beli dadah.", "Buang sepenuhnya."),
    _r("drugs", WARN, r"\b(ganja|syabu|pil kuda|ketum|weed|cannabis)\b", "Rujukan dadah.", ""),
    _r("regulated_goods", BLOCK, r"\b(jual|beli|for sale)\b.{0,20}\b(pistol|senapang|airsoft|senjata api|gun|peluru|ammo)\b",
       "Jual senjata.", "Jualan senjata dilarang di Facebook."),
    _r("regulated_goods", BLOCK, r"\b(jual|beli)\b.{0,20}\b(antibiotik|ubat preskripsi|steroid|xanax|tramadol|viagra)\b",
       "Jual ubat terkawal.", ""),
    _r("regulated_goods", WARN, r"\b(jual|for sale|ready stock)\b.{0,20}\b(vape|pod|liquid|rokok|cigarette|arak|beer|wine)\b",
       "Jualan vape/rokok/arak (terhad).", ""),
    _r("regulated_goods", WARN, r"\b(jual|for sale)\b.{0,20}\b(anak kucing|kitten|puppy|anak anjing|burung|haiwan)\b",
       "Jual haiwan (dilarang di Facebook).", ""),

    # --- adult ---
    _r("adult_sexual", BLOCK, r"\b(onlyfans|porn|lucah|bogel|video\s*panas|sex\s*video|hookup|teman\s*sosial|escort|urut\s*\+\+)\b",
       "Kandungan atau tawaran seksual.", "Buang sepenuhnya."),

    # --- links ---
    _r("spam_links", WARN, r"\b(bit\.ly|tinyurl\.com|cutt\.ly|shorturl\.at|t\.ly|is\.gd|rb\.gy|ow\.ly|s\.id)/\S+",
       "Pautan pendek (sering dianggap spam).", "Guna pautan penuh ke laman anda."),

    _r("spam_links", WARN,
       r"\b(?:vt\.tiktok\.com|vm\.tiktok\.com|tiktok\.com|youtu\.be|youtube\.com/shorts|instagram\.com/reel)\S*",
       "Pautan ke platform lain (TikTok/YouTube/IG). Facebook kurangkan capaian dan ia tanda video mungkin bukan asal.",
       "Buang pautan platform lain daripada caption."),

    # --- profanity (info only; may limit monetisation) ---
    _r("profanity", INFO, r"\b(babi|bodoh|sial|celaka|puki|pukimak|pantat|fuck|shit|bitch|bangsat|lancau|kimak)\b",
       "Bahasa kesat (boleh hadkan monetisasi).", ""),
]


@dataclass
class RuleHit:
    rule: Rule
    match: str
    source: str            # caption | speech | screen
    t: float | None = None


def scan(text: str, source: str, t: float | None = None) -> list[RuleHit]:
    hits: list[RuleHit] = []
    if not text:
        return hits
    for rule in RULES:
        m = rule.pattern.search(text)
        if m:
            hits.append(RuleHit(rule, m.group(0)[:120], source, t))
    return hits


HASHTAG = re.compile(r"(?<!\w)#\w+", re.U)
FB_CAPTION_MAX = 63206


def caption_checks(caption: str) -> list[tuple[str, str, str]]:
    """Returns (severity, note, fix) for format issues."""
    out = []
    if len(caption) > FB_CAPTION_MAX:
        out.append((BLOCK, f"Caption terlalu panjang ({len(caption)} aksara, had {FB_CAPTION_MAX}).", "Pendekkan caption."))
    tags = HASHTAG.findall(caption)
    if len(tags) > 30:
        out.append((WARN, f"Terlalu banyak hashtag ({len(tags)}). Nampak seperti spam.", "Guna 3-5 hashtag yang relevan."))
    letters = [c for c in caption if c.isalpha()]
    if len(letters) > 40 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        out.append((INFO, "Caption kebanyakannya HURUF BESAR.", "Tulis dalam huruf biasa."))
    return out
