"""Classifier with regex-based intents and optional LLM refinement."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

from .gemini_client import refine_reply

CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "catalog_rules.json"

try:
    CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
except Exception:
    CATALOG = []

# -------------------- regex intents --------------------
ASK_HUMAN = re.compile(r"\b(rob[oô]|humano|pessoa|atendente|quero falar|rob[oô] n[aã]o)\b", re.I)
MISSING = re.compile(r"\b(parafus|ferragem|peç[ao]s?\s*falt|n[aã]o\s+veio|faltando|sem\s+parafuso)\b", re.I)
ASSEMBLY = re.compile(r"\b(montar|montagem|manual|instala[cç][aã]o|passo\s*a\s*passo)\b", re.I)
DEADLINE = re.compile(r"\b(chega|entrega|consigue?m? enviar|d[aí]a\s+\d{1,2}|at[eé]\s+dia)\b", re.I)
PRESALE_ONE = re.compile(r"\b(pe[çc]a\s*[úu]nica|vem\s+em\s+uma\s+pe[çc]a|emendas?|em partes?)\b", re.I)
CUSTOM_GOLD = re.compile(r"\b(dourad[ao]|pintad[ao]\s+de\s+dourado|letras\s+douradas?)\b", re.I)
STATUS_RE = re.compile(
    r"(rastre|entrega|cheg|postado|andamento|onde est[aá]|tracking|c[oó]digo|prazo de envio)",
    re.I,
)

# -------------------- respostas --------------------
RESP_HUMANO = (
    "Entendi, e obrigado por avisar 🙏. Sou do atendimento **humano** e vou cuidar do seu caso agora.\n"
    "Já estou conferindo seu pedido; me diga em uma frase o principal ponto que precisa resolver primeiro."
)

RESP_PARAFUSOS = (
    "Sinto muito pelo transtorno! 🙏 Vou resolver pessoalmente.\n"
    "Envio hoje um kit de parafusos completo do seu modelo **sem custo** e já mando o manual (PDF + vídeo).\n"
    "Se preferir, posso fazer **reembolso parcial** ou **devolução com reembolso total** — você escolhe.\n"
    "Confirma o endereço para envio? _Pedido:_ **{ORDER_ID}**."
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
    pairs: List[Tuple[str, str]],
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """Decide reply based on regex intents with fallback and optional LLM refinement."""
    order_info = order_info or {}
    text = " | ".join(t for r, t in pairs[-3:] if r == "buyer") if pairs else " | ".join(buyer_only[-3:])
    norm_text = _normalize(text)
    order_id = order_info.get("orderId", "")

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

    refined = refine_reply(reply, norm_text)
    return True, refined

