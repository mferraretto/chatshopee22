"""Classifier with regex-based intents and optional LLM refinement."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

from .gemini_client import refine_reply, classify
from .rules import get_reply_by_id, apply_rules
from .config import settings

CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "catalog_rules.json"

try:
    CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
except Exception:
    CATALOG = []

# -------------------- regex intents --------------------
# OBS: o texto é normalizado SEM acentos; evite 'ç' e acentos nos padrões.
ASK_HUMAN = re.compile(r"\b(robo|humano|pessoa|atendente|quero falar|robo nao)\b", re.I)

MISSING = re.compile(
    r"\b(parafus|ferragem|pecas?\s*falt|nao\s+veio|faltando|sem\s+parafuso)\b", re.I
)

ASSEMBLY = re.compile(
    r"\b(montar|montagem|manual|instalacao|passo\s*a\s*passo)\b", re.I
)

DEADLINE = re.compile(
    r"\b(chega|entrega|consegue(?:m)?|consigam|enviar|ate\s+dia\s*\d{1,2}|ate\s+o\s+dia\s*\d{1,2})\b",
    re.I,
)

PRESALE_ONE = re.compile(
    r"\b(peca\s*unica|vem\s+em\s+uma\s+peca|emendas?|em\s+partes?)\b", re.I
)

CUSTOM_GOLD = re.compile(
    r"\b(dourado|pintado\s+de\s+dourado|letras?\s+douradas?)\b", re.I
)

STATUS_RE = re.compile(
    r"(status|rastre|entrega|cheg|postado|andamento|onde\s+esta|tracking|codigo|prazo\s+de\s+envio)",
    re.I,
)

NOT_CHECKED_RE = re.compile(r"\b(ainda\s+)?nao\s+(verifiquei|conferi|olhei)\b", re.I)

REFUND_PARTIAL_RE = re.compile(r"\breembolso\s+parcial\b", re.I)
BROKEN_RE = re.compile(
    r"\b(quebrad[oa]?|quebrou|quebra|rachad[oa]?|trincad[oa]?|danificad[oa]?|defeit[oa]?)\b",
    re.I,
)
PHOTO_RE = re.compile(r"\bfoto(s)?\b", re.I)

# -------------------- respostas --------------------
RESP_HUMANO = (
    "Entendi, e obrigado por avisar 🙏. Sou do atendimento **humano** e vou cuidar do seu caso agora.\n"
    "Já estou conferindo seu pedido; me diga em uma frase o principal ponto que precisa resolver primeiro."
)

RESP_PARAFUSOS = (
    "Sinto muito pelo transtorno! 🙏 Vou resolver pessoalmente.\n"
    "Envio hoje um kit de parafusos completo do seu modelo **sem custo** e já mando o manual (PDF + vídeo).\n"
    "Se preferir, posso fazer **reembolso parcial** ou **devolução com reembolso total** — você escolhe.\n"
    "Confirma o endereço para envio? _Pedido:_ **{order_id}**."
)

RESP_PRAZO = (
    "Consigo verificar! Me informa o **CEP** e a **data** que você precisa (ex.: 23/08).\n"
    "Produção: {PROD_DIAS} dias úteis • Envio: {ENVIO_DIAS_ESTIMADO} úteis para {UF}.\n"
    "Se estiver apertado, vejo **envio expresso**."
)

RESP_PECA_UNICA = (
    "Este modelo vai **em {PECAS} peça(s)**. Para tamanhos maiores enviamos em {PECAS_GRANDES} partes por causa do transporte, "
    "com junção que não aparece de frente e kit de união incluso.\n"
    "Se quiser **peça única**, dá para produzir até {LIMITE_CM} cm (consulte frete)."
)

RESP_DOURADO = (
    "Fazemos sim letras douradas ✨. Pode ser **pintura** ou **vinil dourado**.\n"
    "Envie o **nome/frase** e fonte preferida; mando a simulação e o valor do adicional."
)

RESP_STATUS = "O status atual do pedido é **{status}**. Assim que houver novidades, aviso por aqui."
RESP_FALLBACK_CURTO = "Desculpa, não entendi. Pode explicar em uma frase?"
RESP_NOT_CHECKED = "Sem problema! Quando conseguir verificar, me avise por aqui \U0001F60A."

# -------------------- helpers --------------------
def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", text)


def match_catalog(order_info: dict | None, catalog: list[dict] | None = None) -> dict:
    catalog = catalog or CATALOG
    title = _normalize((order_info or {}).get("title", ""))
    for item in catalog:
        for m in item.get("match", []):
            if _normalize(m) in title:
                return item
    return {}


def prod_defaults(prod: dict | None) -> dict:
    prod = prod or {}
    return {
        "PECAS": prod.get("pecas_padrao", "?"),
        "PECAS_GRANDES": prod.get("pecas_grandes", "?"),
        "LIMITE_CM": prod.get("limite_peca_unica_cm", "?"),
        "PROD_DIAS": prod.get("producao_dias_uteis", "?"),
        "ENVIO_DIAS_ESTIMADO": prod.get("envio_dias_est", "?"),
        "UF": prod.get("uf", "sua região"),
    }

# -------------------- main --------------------
def decide_reply(
    _pairs: List[Tuple[str, str]] | None,
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide reply based only on buyer messages.

    1. Classifica as últimas mensagens do comprador.
    2. Se houver regra para a intenção detectada, usa ``rules.json``.
    3. Caso contrário, aplica regex de fallback sobre ``buyer_only``.
    4. O resultado passa por ``refine_reply`` para polir o tom.
    """
    order_info = order_info or {}
    history_depth = getattr(settings, "history_depth", 10)
    text = " | ".join(buyer_only[-history_depth:])
    norm_text = _normalize(text)
    order_id = order_info.get("orderId", "")

    # regras determinísticas (regex) antes do classificador LLM
    decide, rule_reply, action = apply_rules(buyer_only)
    if action == "skip":
        return False, ""
    if decide and rule_reply:
        refined = refine_reply(rule_reply, norm_text)
        return True, refined
    cls = classify(buyer_only)
    if cls.get("needs_reply") is False:
        return False, ""

    intent = cls.get("intent") or ""
    signals = cls.get("signals") or {}
    rule_id = intent
    if intent == "quebra":
        rule_id = "quebra_com_foto" if signals.get("tem_foto") else "quebra_sem_foto"

    base = get_reply_by_id(rule_id) if rule_id else None
    if not base:
        if REFUND_PARTIAL_RE.search(norm_text):
            base = get_reply_by_id("reembolso_parcial")
        elif BROKEN_RE.search(norm_text):
            q_id = "quebra_com_foto" if signals.get("tem_foto") or PHOTO_RE.search(norm_text) else "quebra_sem_foto"
            base = get_reply_by_id(q_id)
    if base:
        refined = refine_reply(base, norm_text)
        return True, refined

    reply = RESP_FALLBACK_CURTO

    if ASK_HUMAN.search(norm_text):
        reply = RESP_HUMANO

    elif MISSING.search(norm_text) or ASSEMBLY.search(norm_text):
        reply = RESP_PARAFUSOS.format(order_id=order_id or "{ORDER_ID}")

    elif DEADLINE.search(norm_text):
        prod = match_catalog(order_info)
        reply = RESP_PRAZO.format(**prod_defaults(prod))

    elif PRESALE_ONE.search(norm_text):
        prod = match_catalog(order_info)
        reply = RESP_PECA_UNICA.format(**prod_defaults(prod))

    elif CUSTOM_GOLD.search(norm_text):
        reply = RESP_DOURADO

    elif STATUS_RE.search(norm_text) and order_info.get("status"):
        reply = RESP_STATUS.format(status=order_info["status"])

    elif NOT_CHECKED_RE.search(norm_text):
        reply = RESP_NOT_CHECKED

    refined = refine_reply(reply, norm_text)
    return True, refined

