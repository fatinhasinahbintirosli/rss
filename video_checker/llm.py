"""Thin wrapper around the Anthropic Messages API (vision + text)."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from .config import Settings


class LLMError(RuntimeError):
    pass


def _client(settings: Settings):
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise LLMError("Pakej 'anthropic' belum dipasang (pip install anthropic)") from e
    if not settings.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY belum diset")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def image_block(path: str) -> dict[str, Any]:
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def parse_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("Jawapan model bukan JSON yang sah")


def ask_json(settings: Settings, system: str, content: list[dict[str, Any]], max_tokens: int = 4000) -> Any:
    client = _client(settings)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_json(text)
