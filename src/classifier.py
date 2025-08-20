# classifier.py

from __future__ import annotations
from typing import List, Tuple
import re

from .gemini_client import generate_reply
from .config import settings

RESP_FALLBACK_CURTO = "Desculpe, não entendi muito bem sua mensagem. Você poderia explicar um pouco melhor para que eu consiga te ajudar?"


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
    m2 = re.search(r"(?is)\bResposta:\s*(.+)$", t)
    if m2:
        return m2.group(1).strip()

    return t


ARCO = re.compile(
    r"\b(arco|arcos|di[âa]metro do arco|tamanho do arco|montar menor|reduzir tamanho)\b",
    re.I,
)
CIL = re.compile(
    r"\b(cilindro|cilindros|trio compacto|cilindro pequeno|cilindro errado)\b", re.I
)


def intent_from_text(txt: str) -> str:
    if ARCO.search(txt) and not CIL.search(txt):
        return "arco_tamanho"
    if CIL.search(txt) and not ARCO.search(txt):
        return "cilindro_pequeno"
    return "fallback"


def decide_reply(
    pairs: List[Tuple[str, str]],
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide se deve responder e retorna o rascunho (somente últimas N do comprador)."""
    depth = int(getattr(settings, "history_depth", 15) or 15)

    msgs = [m for m in (buyer_only or []) if m and m.strip()][-depth:]
    if not msgs:
        return False, ""

    history = order_info.get("history_block") if order_info else None
    if not history:
        history = "\n".join(msgs)

    # exemplo de uso do classificador regex (opcional)
    _ = intent_from_text(" ".join(msgs))

    reply = generate_reply(history, order_info=order_info)
    clean = _sanitize_reply(reply)
    if clean:
        return True, clean
    return False, ""
