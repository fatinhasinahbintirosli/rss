"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "ya", "on")


@dataclass
class Settings:
    # --- Layer 2: music fingerprinting -------------------------------------
    music_provider: str = field(default_factory=lambda: _env("MUSIC_PROVIDER", "audd"))  # audd | acrcloud | none
    audd_api_token: str = field(default_factory=lambda: _env("AUDD_API_TOKEN"))
    acrcloud_host: str = field(default_factory=lambda: _env("ACRCLOUD_HOST"))
    acrcloud_access_key: str = field(default_factory=lambda: _env("ACRCLOUD_ACCESS_KEY"))
    acrcloud_access_secret: str = field(default_factory=lambda: _env("ACRCLOUD_ACCESS_SECRET"))
    # Severity when a known song is found: "block" (red) or "warn" (yellow).
    # Reels made with Facebook's own music library are licensed on Facebook, so "warn" suits crossposting.
    music_match_severity: str = field(default_factory=lambda: _env("MUSIC_MATCH_SEVERITY", "warn"))
    audio_clip_seconds: int = field(default_factory=lambda: _env_int("AUDIO_CLIP_SECONDS", 12))
    audio_clip_every: int = field(default_factory=lambda: _env_int("AUDIO_CLIP_EVERY", 30))
    max_audio_clips: int = field(default_factory=lambda: _env_int("MAX_AUDIO_CLIPS", 10))

    # --- Layer 3: visual moderation ----------------------------------------
    visual_provider: str = field(default_factory=lambda: _env("VISUAL_PROVIDER", "gemini"))  # gemini | claude | rekognition | none
    frame_interval: float = field(default_factory=lambda: _env_float("FRAME_INTERVAL", 2.0))
    max_frames: int = field(default_factory=lambda: _env_int("MAX_FRAMES", 60))
    frames_per_request: int = field(default_factory=lambda: _env_int("FRAMES_PER_REQUEST", 8))
    rekognition_min_confidence: float = field(default_factory=lambda: _env_float("REKOGNITION_MIN_CONFIDENCE", 60))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "ap-southeast-1"))

    # --- Layer 4: speech + text --------------------------------------------
    transcribe_provider: str = field(default_factory=lambda: _env("TRANSCRIBE_PROVIDER", "local"))  # local | openai | none
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    ocr_provider: str = field(default_factory=lambda: _env("OCR_PROVIDER", "auto"))  # auto | tesseract | none
    tesseract_langs: str = field(default_factory=lambda: _env("TESSERACT_LANGS", "eng+msa"))
    text_llm_review: bool = field(default_factory=lambda: _env_bool("TEXT_LLM_REVIEW", True))

    # --- Gemini (whole-video review) ----------------------------------------
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.8-flash"))

    # --- Claude (vision + text review) -------------------------------------
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))

    # --- Verdict -----------------------------------------------------------
    # If a layer could not run (no key, provider error), the verdict cannot be green.
    strict: bool = field(default_factory=lambda: _env_bool("STRICT_MODE", True))

    def gemini_ready(self) -> bool:
        return bool(self.gemini_api_key)

    def claude_ready(self) -> bool:
        return bool(self.anthropic_api_key)
