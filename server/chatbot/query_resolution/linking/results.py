from __future__ import annotations

from typing import Any, Optional


def result(
    status: str,
    *,
    value: Optional[str] = None,
    candidates: Optional[list[str]] = None,
    scores: Optional[dict[str, float]] = None,
    method: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "method": method}
    if value is not None:
        out["value"] = value
    if candidates is not None:
        out["candidates"] = candidates
    if scores is not None:
        out["scores"] = scores
    return out
