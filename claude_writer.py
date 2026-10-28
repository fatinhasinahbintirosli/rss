"""Penulis caption Claude (pilihan). Gemini tonton video & tulis penerangan, Claude tulis ayat tergantung.

Aktif hanya bila CLAUDE_API_KEY diset. Jika Claude gagal (rangkaian, kredit habis, jawapan rosak),
caption terus dijana oleh Gemini seperti biasa — sistem tidak pernah berhenti kerana Claude.

Tetapan (GitHub Secrets / Variables):
  CLAUDE_API_KEY   kunci API (MagnetAPI atau Anthropic rasmi)
  CLAUDE_BASE_URL  lalai https://api.anthropic.com  (MagnetAPI: https://api.magnetapi.org)
  CLAUDE_MODEL     lalai claude-sonnet-5-5
"""
from __future__ import annotations

import json
import os
import re

import requests

DESCRIBE_PROMPT = """Tonton video ini dengan teliti dan terangkan apa yang berlaku, untuk penulis caption yang TIDAK menonton video.
Tulis dalam Bahasa Melayu. Jangan reka apa-apa yang tiada dalam video.

Balas JSON sahaja:
{
  "ringkasan": "apa yang berlaku dari awal hingga akhir, ikut urutan",
  "jelas_nampak": ["perkara yang penonton NAMPAK serta-merta dalam beberapa saat pertama"],
  "tersembunyi": ["maklumat yang hanya terdedah di tengah/hujung video, dalam percakapan, atau tidak nampak terus: sebab, latar belakang, kejutan"],
  "twist": "bahagian paling mengejutkan / menyentuh hati / kelakar, atau kosong jika tiada",
  "percakapan": "apa yang dicakap orang dalam video (ringkas), atau kosong",
  "teks_skrin": "teks yang tertulis di skrin, atau kosong",
  "emosi": "perasaan utama video: sedih, lucu, marah, terharu, seram, dll."
}"""

CLAUDE_EXTRA = """

TAMBAHAN — anda pakar caption viral Facebook Malaysia. Matlamat: pembaca WAJIB tekan "see more".
- Gantung pada perkara dalam senarai "tersembunyi" atau "twist", BUKAN pada "jelas_nampak".
- Teknik yang berkesan: mula dengan reaksi/emosi lalu putus ("Yg buat semua sebak, rupanya"),
  sebut akibat tanpa sebab ("Lepas kejadian ni, terus"), atau janji pendedahan ("Tak sangka, rupanya dia").
- Setiap ayat guna sudut berbeza (sebab, akibat, reaksi orang, apa jadi sebelum, apa jadi selepas).
- Ayat mesti berbunyi macam orang Malaysia biasa menaip, bukan macam berita atau iklan."""


def adapt_prompt(prompt: str, description: dict) -> str:
    """Tukar arahan 'Tonton video ini' kepada penerangan video bertulis + arahan tambahan."""
    desc = json.dumps(description, ensure_ascii=False, indent=1)
    head = ("Anda tidak dapat menonton video, tetapi ini penerangan lengkap video tersebut "
            f"(ditulis oleh orang yang sudah menontonnya):\n{desc}\n\nBerdasarkan penerangan ini,")
    body = re.sub(r"^Tonton video ini\.", head, prompt.strip(), count=1)
    if body == prompt.strip():
        body = f"{head}\n{body}"
    return body.replace("Balas JSON sahaja:", CLAUDE_EXTRA.strip() + "\n\nBalas JSON sahaja:", 1)


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("Jawapan Claude bukan JSON")
        data = json.loads(text[a:b + 1])
    if not isinstance(data, dict):
        raise ValueError("Jawapan Claude bukan objek JSON")
    return data


class ClaudeWriter:
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com", model: str = "claude-sonnet-5-5",
                 session: requests.Session | None = None, log=print):
        self.key = api_key
        self.base = base_url.rstrip("/").removesuffix("/v1")
        self.model = model
        self.s = session or requests.Session()
        self.log = log
        self.used = 0          # bilangan permintaan berjaya (untuk log)
        self.fallbacks = 0

    def _headers(self) -> dict:
        h = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        if "anthropic.com" not in self.base:     # MagnetAPI & proxy lain guna Bearer
            h["authorization"] = f"Bearer {self.key}"
        return h

    def ask_json(self, prompt: str) -> dict:
        r = self.s.post(f"{self.base}/v1/messages", headers=self._headers(), timeout=120, json={
            "model": self.model, "max_tokens": 2500, "temperature": 1.0,
            "messages": [{"role": "user", "content": prompt}],
        })
        if r.status_code >= 400:
            raise RuntimeError(f"Claude {r.status_code}: {r.text[:200]}")
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content") or [] if b.get("type") == "text")
        if not text:
            raise RuntimeError(f"Jawapan Claude kosong: {json.dumps(j)[:200]}")
        return parse_json(text)

    def wrap(self, gemini_ask, describe):
        """Pulangkan ask(prompt) baharu: Claude dahulu, Gemini sebagai sandaran."""
        state = {"desc": None, "off": False}

        def ask(prompt: str) -> dict:
            if not state["off"]:
                try:
                    if state["desc"] is None:
                        d = describe()
                        if not isinstance(d, dict) or not (d.get("ringkasan") or d.get("tersembunyi")):
                            raise RuntimeError("penerangan video dari Gemini kosong")
                        state["desc"] = d
                    data = self.ask_json(adapt_prompt(prompt, state["desc"]))
                    if isinstance(data.get("captions"), list) and data["captions"]:
                        self.used += 1
                        self.log(f"  caption: Claude ({self.model}) → {len(data['captions'])} calon")
                        return data
                    raise RuntimeError("jawapan Claude tiada senarai captions")
                except Exception as e:  # noqa: BLE001 — apa-apa ralat → Gemini
                    self.fallbacks += 1
                    self.log(f"  caption: Claude gagal ({str(e)[:150]}) → guna Gemini")
                    if state["desc"] is None or self.fallbacks >= 2:
                        state["off"] = True      # jangan cuba Claude lagi untuk video ini
            return gemini_ask(prompt)

        return ask


def from_env(log=print) -> ClaudeWriter | None:
    key = os.environ.get("CLAUDE_API_KEY", "").strip()
    if not key:
        return None
    return ClaudeWriter(key, os.environ.get("CLAUDE_BASE_URL", "").strip() or "https://api.anthropic.com",
                        os.environ.get("CLAUDE_MODEL", "").strip() or "claude-sonnet-5-5", log=log)
