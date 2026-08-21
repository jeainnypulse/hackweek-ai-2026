"""Shared helpers for access and lives slot extraction."""

from __future__ import annotations

import json
from typing import Any, Optional

from bedrock import invoke_claude


def parse_llm_json(raw: str) -> Any:
    """Parse JSON from a model response, stripping markdown fences if present."""
    text = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def invoke_json_extract(prompt: str, question: str, *, max_tokens: int = 1024) -> dict:
    """One LLM call: prompt + question → JSON object."""
    parsed = parse_llm_json(invoke_claude(prompt + question, max_tokens=max_tokens))
    if not isinstance(parsed, dict):
        raise ValueError(f"Slot extract did not return a JSON object: {parsed!r}")
    return parsed


def slot_text(value: Any) -> Optional[str]:
    if value is None or value == "" or str(value).strip().lower() == "null":
        return None
    return str(value).strip()
