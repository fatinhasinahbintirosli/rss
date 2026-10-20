"""Policy taxonomy, loosely mapped to Meta Community Standards / Branded Content rules.

Each category has a default severity and a Malay label shown to users.
Tune severities here to make the checker stricter or looser.
"""

from __future__ import annotations

from .models import BLOCK, INFO, WARN

# Visual categories (layer 3)
VISUAL = {
    "nudity_sexual":       (BLOCK, "Bogel atau kandungan seksual"),
    "suggestive":          (WARN,  "Kandungan menjolok mata / seksi"),
    "minor_safety":        (BLOCK, "Kanak-kanak dalam situasi tidak selamat atau tidak sesuai"),
    "graphic_violence":    (BLOCK, "Darah, kematian atau cedera parah (bunuh manusia/haiwan, mayat, luka teruk, kemalangan maut)"),
    "violence":            (WARN,  "Pergaduhan atau keganasan ringan (tanpa darah dan tanpa cedera parah)"),
    "self_harm":           (BLOCK, "Bunuh diri atau mencederakan diri"),
    "weapons":             (WARN,  "Senjata api atau senjata tajam"),
    "drugs":               (BLOCK, "Dadah, penggunaan dadah atau peralatan dadah"),
    "tobacco_vape_alcohol": (WARN, "Rokok, vape atau alkohol"),
    "gambling":            (WARN,  "Perjudian"),
    "hate_symbols":        (BLOCK, "Simbol kebencian atau organisasi berbahaya"),
    "dangerous_acts":      (WARN,  "Aksi berbahaya yang boleh ditiru"),
    "disturbing":          (WARN,  "Imej mengganggu / menjijikkan"),
    "third_party_watermark": (WARN, "Watermark platform lain (TikTok, CapCut, dll)"),
    "copyrighted_media":   (WARN,  "Klip TV, filem, siaran sukan atau kandungan orang lain"),
}

# Text / speech categories (layer 4)
TEXT = {
    "hate_speech":        (BLOCK, "Ucapan kebencian (kaum, agama, jantina, dll)"),
    "harassment":         (WARN,  "Buli atau gangguan terhadap individu"),
    "violence_threat":    (BLOCK, "Ugutan atau hasutan keganasan"),
    "adult_sexual":       (BLOCK, "Bahasa atau tawaran seksual"),
    "scam_fraud":         (BLOCK, "Penipuan, giveaway palsu atau phishing"),
    "get_rich_quick":     (WARN,  "Janji untung cepat / pelaburan terjamin"),
    "health_claims":      (WARN,  "Dakwaan kesihatan melampau"),
    "misinformation":     (WARN,  "Maklumat palsu yang boleh membahayakan"),
    "engagement_bait":    (WARN,  "Engagement bait (minta like/share/tag/komen)"),
    "gambling":           (BLOCK, "Promosi judi online"),
    "drugs":              (BLOCK, "Jual beli dadah"),
    "regulated_goods":    (BLOCK, "Jual senjata, haiwan, ubat preskripsi atau barang larangan"),
    "profanity":          (INFO,  "Bahasa kesat"),
    "spam_links":         (WARN,  "Pautan pendek / mencurigakan"),
    "caption_format":     (WARN,  "Masalah format caption"),
    "self_harm":          (BLOCK, "Galakan mencederakan diri"),
    "dangerous_orgs":     (BLOCK, "Sokongan kepada organisasi berbahaya"),
}

SEVERITIES = (BLOCK, WARN, INFO)


def visual_label(cat: str) -> tuple[str, str]:
    return VISUAL.get(cat, (WARN, cat))


def text_label(cat: str) -> tuple[str, str]:
    return TEXT.get(cat, (WARN, cat))
