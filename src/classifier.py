"""Simplified reply decision using a single Gemini call."""
from __future__ import annotations

from typing import List, Tuple

from .gemini_client import generate_reply
from .config import settings

RESP_FALLBACK_CURTO = (
    "Desculpe, não entendi muito bem sua mensagem. Você poderia explicar um pouco melhor para que eu consiga te ajudar?"
)

def decide_reply(
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide se deve responder e retorna o rascunho (somente últimas N do comprador)."""
    history_depth = int(getattr(settings, "history_depth", 15) or 15)

    # Últimas N mensagens do comprador
    msgs = [m for m in (buyer_only or []) if m and m.strip()][-history_depth:]
    if not msgs:
        return False, ""

    # Heurística simples: comprador enviou imagem/foto?
    # (ajuda o prompt a optar por 'quebra_com_foto' quando aplicável)
    buyer_sent_photo = any(
        any(k in m.lower() for k in ("[imagem]", "foto", "imagem", "segue foto", "enviei foto"))
        for m in msgs
    )

    # Monta o histórico apenas com o papel 'buyer'
    history_lines = [f"buyer: {m.strip()}" for m in msgs]
    if buyer_sent_photo:
        history_lines.append("system: Observação: o comprador já enviou uma foto.")

    history = "\n".join(history_lines)

    reply = generate_reply(history)
    if reply and reply.strip():
        return True, reply.strip()

    return False, ""
