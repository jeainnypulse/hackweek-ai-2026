from __future__ import annotations

from chatbot.conversation.resolution import resolve_next_slot


def _linker_clarify_response(session: dict) -> dict:
    return resolve_next_slot(session)
