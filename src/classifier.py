# classifier.py

from __future__ import annotations
from typing import List, Tuple
import re

from .gemini_client import plan_reply, generate_reply, classify_conversation
from .config import settings
from .firebase_client import get_product_by_sku
from .state_machine import ConversationStateMachine

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


# Estado por conversa (simples memória em processo)
_STATE_MACHINES: dict[str, ConversationStateMachine] = {}


def decide_reply(
    pairs: List[Tuple[str, str]],
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str, dict]:
    """Decide se deve responder e retorna o rascunho + metadados.

    O terceiro elemento do retorno contém o dicionário com
    ``{intent, estado, sentimento, urgencia}`` obtido pelo classificador do
    Gemini. Esse dicionário também mantém o estado da conversa via uma pequena
    máquina de estados em memória.
    """
    depth = int(getattr(settings, "history_depth", 15) or 15)

    msgs = [m for m in (buyer_only or []) if m and m.strip()][-depth:]
    if not msgs:
        return False, ""

    history = order_info.get("history_block") if order_info else None
    if not history:
        history = "\n".join(msgs)

    # Analisa intenção/estado com Gemini
    analysis = classify_conversation(history)

    # Atualiza state machine por conversa (usa orderId ou nome do comprador)
    key = (order_info or {}).get("orderId") or (order_info or {}).get("buyer_name") or "_"
    machine = _STATE_MACHINES.setdefault(key, ConversationStateMachine())
    machine.update(analysis.get("estado", machine.state.value))
    analysis["estado"] = machine.state.value

    # Busca dados do produto pelo SKU e injeta em order_info
    sku = order_info.get("sku") if order_info else None
    if sku:
        prod = get_product_by_sku(sku)
        if prod:
            order_info = dict(order_info or {})
            order_info["product_info"] = prod

    # exemplo de uso do classificador regex (opcional)
    _ = intent_from_text(" ".join(msgs))

    plan = plan_reply(history, order_info=order_info, analysis=analysis)
    if not plan.get("should_reply"):
        return False, "", analysis
    reply = generate_reply(plan)
    clean = _sanitize_reply(reply)
    if clean:
        return True, clean, analysis
    return False, "", analysis
