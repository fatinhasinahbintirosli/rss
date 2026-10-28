"""Caption pendek gaya '...see-moo.re' — satu caption berbeza bagi setiap Page.

Gemini menonton video dan tulis ayat pembuka (maks 7 perkataan, tergantung).
Semua peraturan disemak semula oleh kod di bawah, bukan dipercayai bulat-bulat dari AI.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

SUFFIX = "...see-moo.re"
MAX_WORDS = 7

# Perkataan yang tidak dibenarkan dalam caption (tuduhan, kaum/agama, ejekan, kesat)
BANNED = [
    # tuduhan jenayah terhadap orang dalam video
    "curi", "mencuri", "pencuri", "tipu", "penipu", "pukul", "dipukul", "bunuh", "rogol", "samun", "rompak",
    "dadah", "penagih", "penyangak", "scammer", "scam",
    # kaum / agama
    "melayu", "cina", "india", "keling", "indon", "bangla", "negro", "islam", "kristian", "hindu", "buddha",
    "kafir", "murtad",
    # ejekan / kesat
    "padan muka", "padan", "bodoh", "bangang", "sial", "babi", "anjing", "lalat", "sondol", "penyondol",
    "celaka", "bangsat", "pukimak", "lancau", "puki",
    # engagement bait
    "share", "tag", "like", "komen", "comment",
]
_BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED) + r")\b", re.I)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
_LINK_RE = re.compile(r"https?://|www\.|\.com\b|\.my\b", re.I)


def normalize(body: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", body.lower()).strip()


def clean_body(raw: str, suffixes=()) -> str:
    """Buang suffix/titik/petikan yang Gemini mungkin tambah sendiri (atau ayat penutup profile)."""
    s = str(raw or "").strip().strip('"\'“”‘’')
    for suf in sorted({x.strip() for x in suffixes if x and x.strip()}, key=len, reverse=True):
        if s.lower().endswith(suf.lower()):
            s = s[: -len(suf)].strip()
            break
    s = re.sub(r"\.*\s*see[-\s]?mo+\.?re?\s*$", "", s, flags=re.I)
    s = re.sub(r"[\s.…,!?;:]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


_YANG_RE = re.compile(r"\byang\b", re.I)
# Kata ganda penuh: rama-rama → rama², baru-baru → baru², orang-orangnya → orang²nya
_GANDA_RE = re.compile(r"\b([^\W\d_]+)-\1([^\W\d_]*)\b", re.I)


def shorten(body: str) -> str:
    """Gaya singkatan: 'yang' → 'yg', kata ganda → kata² (ikut huruf besar asal)."""
    body = _YANG_RE.sub(lambda m: "Yg" if m.group(0)[0].isupper() else "yg", body)
    return _GANDA_RE.sub(lambda m: f"{m.group(1)}²{m.group(2)}", body)


def problems(body: str, max_words: int = MAX_WORDS, min_words: int = 1) -> list[str]:
    out = []
    words = body.split()
    if not words:
        out.append("kosong")
    if len(words) > max_words:
        out.append(f"{len(words)} perkataan (maks {max_words})")
    elif words and len(words) < min_words:
        out.append(f"{len(words)} perkataan (min {min_words})")
    if "#" in body:
        out.append("ada hashtag")
    if _EMOJI_RE.search(body):
        out.append("ada emoji")
    if _LINK_RE.search(body):
        out.append("ada pautan")
    m = _BANNED_RE.search(body)
    if m:
        out.append(f"perkataan dilarang: {m.group(1)}")
    return out


def too_similar(body: str, others: list[str], threshold: float = 0.8) -> bool:
    n = normalize(body)
    return any(n == normalize(o) or SequenceMatcher(None, n, normalize(o)).ratio() >= threshold for o in others)


def finalize(body: str, suffix: str = SUFFIX) -> str:
    """Ayat penutup yang bermula dengan huruf/nombor dipisahkan satu jarak; yang bermula dengan
    simbol (cth. "...see-moo.re") dilekatkan terus."""
    suffix = (suffix or "").strip()
    if not suffix:
        return body
    return f"{body} {suffix}" if suffix[0].isalnum() else f"{body}{suffix}"


def build_prompt(n: int, source_caption: str, avoid: list[str], max_words: int = MAX_WORDS,
                 suffix: str = SUFFIX, short: bool = False, min_words: int = 1) -> str:
    words_rule = (f"ANTARA {min_words} HINGGA {max_words} patah perkataan (cuba pelbagaikan panjang ayat)"
                  if 1 < min_words < max_words else f"MAKSIMUM {max_words} patah perkataan")
    examples = "\n".join(f"- {finalize(e, suffix)}" for e in [
        "Kejadian berpunca sbb", "Dpt info, dorg ni rupanya", "Katanya staff ni mrh sbb",
        "Katanya penumpang tu jatuh dan terus", "Dpt info laki ni dah"])
    avoid_txt = "\n".join(f"- {a}" for a in avoid[-40:]) or "(tiada)"
    return f"""Tonton video ini. Tulis {n} ayat pembuka caption Facebook yang BERBEZA antara satu sama lain.

Gaya (contoh, semuanya gantung pada punca / akibat / latar belakang, bukan babak dalam video):
{examples}

PALING PENTING — gantung pada perkara yang TIADA dalam video:
Penonton sudah nampak apa yang berlaku dalam video. Ayat yang menceritakan semula babak dalam video
tidak membuatkan sesiapa tekan "see more". Gantung pada perkara yang penonton TIDAK nampak:
- PUNCA / SEBAB kejadian (biasanya video bermula bila kejadian sudah berlaku):
  "Kejadian berpunca sbb", "Dpt info, dorg bergaduh sbb", "Katanya semua bermula bila".
- APA JADI SELEPAS video tamat: "Katanya penumpang tu jatuh dan terus", "Lepas kejadian ni, dia terus".
- LATAR BELAKANG / SIAPA dia: "Dpt info, dorg ni rupanya", "Katanya lelaki ni sebenarnya".
Sebut kejadian secara RINGKAS dan umum sahaja (cth. "jatuh", "bergaduh"), kemudian terus gantung pada
punca, akibat atau latar belakang.

Contoh: video penumpang bas terjatuh bila bas brek mengejut.
  SALAH: "Bas tu brake dan penumpang tu terus" (semua orang sudah nampak dalam video).
  SALAH: "Penumpang ni tiba² rebah ke lantai bila" (menceritakan semula babak).
  BETUL: "Katanya penumpang tu jatuh dan terus" / "Dpt info, pemandu tu brek sbb".
Contoh: video dua orang bergaduh.
  SALAH: "Lelaki ni tolak kawan dia sampai" (ada dalam video).
  BETUL: "Kejadian berpunca sbb" / "Dpt info, dorg ni bergaduh sbb".

Peraturan ayat tergantung:
- Akhiri dengan perkataan penyambung yang membuatkan ayat tidak lengkap, contohnya:
  sbb, rupanya, terus, bila, lepas, sampai, nak, dah, ada, yg, masa, tapi, kena.
- JANGAN habiskan cerita, JANGAN beri kesimpulan, JANGAN sebut punca atau akibat sebenar.
- JANGAN besar-besarkan: jangan bayangkan darah, kecederaan teruk atau kematian yang tidak berlaku.
  Gaduh biasa tetap "bergaduh", bukan "berlumuran darah".

Syarat setiap ayat:
- Bahasa Melayu santai, boleh guna singkatan (dpt, dorg, sbb, mrh, nk, tu).{"""
- Tulis "yg" (bukan "yang"). Kata ganda guna ² (rama², baru², kadang²), bukan rama-rama.""" if short else ""}
- {words_rule}.
- Kejadian yang disebut mesti betul-betul berlaku dalam video. Jangan reka fakta; biar punca/akibat tergantung.
- JANGAN tuduh orang dalam video (contoh: jangan guna curi, tipu, pukul, scam).
- JANGAN sebut bangsa, kaum atau agama. JANGAN ejek orang dalam video.
- Tiada hashtag, emoji, pautan. Jangan minta like/share/tag/komen.
- Jangan tulis "see more"{f' atau "{suffix.strip()}"' if suffix.strip() else ""} — ayat penutup ditambah kemudian.

Caption asal (untuk konteks sahaja, jangan salin): {source_caption or "(tiada)"}

Jangan ulang atau tiru ayat-ayat ini (sudah digunakan):
{avoid_txt}

Balas JSON sahaja: {{"captions": ["...", "..."]}}"""


# Perkataan penyambung yang membuatkan ayat "tergantung" bila diletak di hujung
HANGING = {
    "bila", "sbb", "sebab", "rupanya", "rupa", "terus", "tiba²", "tiba-tiba", "lepas", "selepas", "sebelum",
    "sampai", "akan", "nak", "nk", "dah", "sudah", "ada", "yg", "yang", "masa", "waktu", "time", "tapi", "tp",
    "mcm", "macam", "kena", "dia", "dorg", "dia", "ni", "tu", "untuk", "utk", "dengan", "dgn", "kat", "dekat",
    "pada", "dalam", "dlm", "jadi", "buat", "bagi", "kata", "cakap", "nampak", "tengok", "dengar", "ingatkan",
    "ingat", "sangka", "tak", "x", "pun", "je", "mula", "baru", "kalau", "klu", "boleh", "punya", "apa",
    "siapa", "mana", "drpd", "dari", "ke", "cuba", "terus²", "bawa", "minta", "suruh", "jumpa", "dapat", "dpt",
}


def is_hanging(body: str) -> bool:
    words = re.findall(r"[\w²-]+", body.lower())
    return bool(words) and words[-1] in HANGING


def pick_captions(candidates: list[str], n: int, history: list[str], max_words: int = MAX_WORDS,
                  suffix: str = SUFFIX, short: bool = False, allow_flat: bool = True,
                  min_words: int = 1) -> tuple[list[str], list[str]]:
    """Return (valid bodies up to n, rejected notes). allow_flat=False tolak ayat yang tidak tergantung."""
    chosen: list[str] = []
    rejected: list[str] = []
    # Utamakan ayat yang berakhir dengan perkataan tergantung; ayat "lengkap" hanya jika tidak cukup.
    def prep(raw):
        b = clean_body(raw, [suffix])
        return shorten(b) if short else b
    bodies = [prep(c) for c in candidates]
    ordered = [b for b in bodies if is_hanging(b)] + [b for b in bodies if not is_hanging(b)]
    for body in ordered:
        p = problems(body, max_words, min_words)
        if not p and not allow_flat and not is_hanging(body):
            p = ["kurang tergantung"]
        if p:
            rejected.append(f"{body!r}: {', '.join(p)}")
            continue
        if too_similar(body, chosen + history):
            rejected.append(f"{body!r}: terlalu serupa")
            continue
        chosen.append(body)
        if len(chosen) >= n:
            break
    return chosen, rejected


CHUNK = 25   # caption setiap permintaan Gemini (banyak Page → beberapa permintaan)


def fill_to(captions: list[str], n: int) -> list[str]:
    """Jika caption unik tidak cukup untuk semua Page, ulang caption sedia ada (lebih baik dari Page dilangkau)."""
    if not captions:
        return []
    return [captions[i % len(captions)] for i in range(n)]


def generate(path_or_file, n: int, source_caption: str, history: list[str], ask, rounds: int = 3,
             max_words: int = MAX_WORDS, suffix: str = SUFFIX, short: bool = False, min_words: int = 1):
    """ask(prompt) -> dict with 'captions'. Retries until n valid captions or rounds used up.
    Untuk banyak Page (cth. 100), caption diminta berperingkat (CHUNK setiap permintaan)."""
    chosen: list[str] = []
    rejected_all: list[str] = []
    rounds = max(rounds, -(-n // CHUNK) + 2)
    for rnd in range(rounds):
        need = n - len(chosen)
        if need <= 0:
            break
        data = ask(build_prompt(min(need, CHUNK) + 3, source_caption, history + chosen, max_words, suffix, short,
                                min_words))
        cands = data.get("captions") if isinstance(data, dict) else None
        if not isinstance(cands, list):
            rejected_all.append("jawapan Gemini tiada senarai captions")
            continue
        # Pusingan awal: hanya ayat tergantung. Pusingan terakhir: terima ayat biasa supaya tidak gagal.
        got, rej = pick_captions([str(c) for c in cands], min(need, CHUNK + 3), history + chosen, max_words, suffix, short,
                                 allow_flat=(rnd == rounds - 1), min_words=min_words)
        chosen.extend(got)
        rejected_all.extend(rej)
    return [finalize(b, suffix) for b in chosen], rejected_all
