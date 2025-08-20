# classifier.py

from __future__ import annotations
from typing import List, Tuple
import re

from .gemini_client import generate_reply
from .config import settings

RESP_FALLBACK_CURTO = (
    "Desculpe, não entendi muito bem sua mensagem. Você poderia explicar um pouco melhor para que eu consiga te ajudar?"
)

def _sanitize_reply(text: str) -> str:
    if not text:
        return ""
    t = text.strip()

    # Se vier "Ação: skip (pular)" ou variações, devolve vazio
    low = t.lower()
    if (
        low == "skip"
        or "ação: skip" in low
        or "acao: skip" in low
        or "skip (pular)" in low
    ):
        return ""

    # Remove rótulos tipo "ID:" e extrai só o conteúdo após "Resposta:"
    t = re.sub(r"(?is)\bID:\s*.*?$", "", t).strip()
    m = re.search(r'(?is)\bResposta:\s*"(.*?)"\s*$', t)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'(?is)\bResposta:\s*(.+)$', t)
    if m2:
        return m2.group(1).strip()

    return t

def decide_reply(
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide se deve responder e retorna o rascunho (somente últimas N do comprador)."""
    depth = int(getattr(settings, "history_depth", 15) or 15)

    msgs = [m for m in (buyer_only or []) if m and m.strip()][-depth:]
    if not msgs:
        return False, ""

    history = "\n".join(f"buyer: {m.strip()}" for m in msgs)

    # >>> agora passamos order_info para a IA
    reply = generate_reply(history, order_info=order_info)
    clean = _sanitize_reply(reply)
    if clean:
        return True, clean
    return False, ""
