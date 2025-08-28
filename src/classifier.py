# classifier.py

from __future__ import annotations
from typing import List, Tuple
import re

from .gemini_client import generate_reply, validate_reply_text
from .config import settings
from .firebase_client import get_product_by_sku

RESP_FALLBACK_CURTO = "Desculpe, não entendi muito bem sua mensagem. Você poderia explicar um pouco melhor para que eu consiga te ajudar?"


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

    # Busca dados do produto pelo SKU e injeta em order_info
    sku = order_info.get("sku") if order_info else None
    if sku:
        prod = get_product_by_sku(sku)
        if prod:
            order_info = dict(order_info or {})
            order_info["product_info"] = prod

    # exemplo de uso do classificador regex (opcional)
    _ = intent_from_text(" ".join(msgs))

    resp = generate_reply(history, order_info=order_info)
    if not resp:
        return False, ""
    if resp.action != "reply":
        return False, ""
    if resp.confidence < settings.reply_confidence_threshold:
        return False, ""
    if not (
        resp.policy_flags.nao_altera_endereco
        and resp.policy_flags.nao_cobra_fora_app
    ):
        return False, ""
    if not validate_reply_text(resp.reply):
        return False, ""
    return True, resp.reply
