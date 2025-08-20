"""Simplified reply decision using a single Gemini call."""
from __future__ import annotations

from typing import List, Tuple

from .gemini_client import generate_reply
from .config import settings

RESP_FALLBACK_CURTO = (
    "Desculpe, não entendi muito bem sua mensagem. Você poderia explicar um pouco melhor para que eu consiga te ajudar?"
)


def decide_reply(
    _pairs: List[Tuple[str, str]] | None,
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide se deve responder e retorna o rascunho."""
    history_depth = getattr(settings, "history_depth", 15)
    pairs = _pairs or []
    history = "\n".join(f"{r}: {m}" for r, m in pairs[-history_depth:])
    reply = generate_reply(history)
    if reply.strip():
        return True, reply.strip()
    return False, ""
