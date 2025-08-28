# classifier.py

from __future__ import annotations
from typing import List, Tuple
import re

from .gemini_client import (
    generate_reply,
    validate_reply_text,
    detect_order_stage,
)
from .policies import detect_policies, load_snippets
from .config import settings
from .firebase_client import get_product_by_sku
from .semantic_cache import get_cached_reply, store_cached_reply

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

    last_msg = msgs[-1]
    etapa = detect_order_stage(order_info) if order_info else "desconhecido"
    intent_guess = intent_from_text(" ".join(msgs))
    cached = get_cached_reply(last_msg, etapa, intent_guess)
    if cached and validate_reply_text(cached):
        return True, cached

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

    policy_ids = detect_policies(" ".join(msgs))
    policy_block = load_snippets(policy_ids)

    resp = generate_reply(history, order_info=order_info, policy_context=policy_block)
    if not resp:
        return True, RESP_FALLBACK_CURTO
    if resp.action == "skip":
        return False, ""
    if resp.action != "reply":
        return True, RESP_FALLBACK_CURTO
    if resp.confidence < settings.reply_confidence_threshold:
        return True, RESP_FALLBACK_CURTO
    if not (
        resp.policy_flags.nao_altera_endereco and resp.policy_flags.nao_cobra_fora_app
    ):
        return True, RESP_FALLBACK_CURTO
    if not validate_reply_text(resp.reply):
        return True, RESP_FALLBACK_CURTO

    # Atualiza slots inferidos pelo modelo
    if order_info is not None:
        order_info.setdefault("etapa", etapa)
        ent = resp.entities
        if ent.sku and not order_info.get("sku"):
            prod = get_product_by_sku(ent.sku)
            if prod:
                order_info["sku"] = ent.sku
                order_info["product_info"] = prod
        if ent.pedido and not order_info.get("pedido"):
            order_info["pedido"] = ent.pedido
        if ent.produto and not order_info.get("produto"):
            order_info["produto"] = ent.produto

    store_cached_reply(last_msg, etapa, resp.intent, resp.reply)
    return True, resp.reply
