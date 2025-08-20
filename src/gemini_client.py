import json
import re
import unicodedata
from typing import List, Tuple

import google.generativeai as genai
from .config import settings

# ---------------------------
# Gemini client
# ---------------------------
def get_gemini():
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ausente. Configure no .env")
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={
            "temperature": 0.2,
            "top_p": 0.9,
            "response_mime_type": "application/json"  # usamos JSON nas duas passadas
        }
    )

# ---------------------------
# Prompt de classificação (igual ao seu, só mantido aqui)
# ---------------------------
PROMPT = r"""
Você é um classificador de mensagens de atendimento Shopee e decide se devemos responder.
Leia as últimas mensagens (comprador + vendedor). Classifique a INTENÇÃO e diga se devemos responder.

REGRAS DE NÃO-RESPOSTA (pular):
- Reclamação de PIX/reembolso que “não caiu”, “não recebi”, “comprovante” → pular
- Cobrança de peça/substituição prometida anteriormente que ainda não enviamos → pular

INTENÇÕES (enum):
- "quebra"               → dano/defeito/avaria em produto
- "faltando"             → peça/parafuso faltando
- "elogio"               → recebido/elogio/agradecimento
- "envio"                → dúvida logística genérica (prazo médio, rastreio etc.)
- "embalagem_precompra"  → dúvidas sobre embalagem, “vem bem embalado?”, medo de amassar (pré-venda)
- "prazo_data"           → quer que chegue até uma data específica (ex.: “chegue até dia 10”)
- "etiqueta_fragil"      → pedir aviso/etiqueta de FRÁGIL
- "duvida_produto"       → característica/técnica do item (ex.: “tem furinho?”, material, medidas)
- "pular"                → casos de NÃO-RESPOSTA acima

EXTRAÇÕES:
- "tem_foto": true/false (cliente anexou/relatou foto? palavras como “foto”, “imagem”, “segue foto”)
- "urgencia": true/false (ex.: “urgente”, “preciso para sábado”, “até o dia”, “desesperad”)
- "pre_venda": true/false (é antes da compra? sinais como “quero comprar”, “pretendo comprar”, sem ‘veio/chegou/recebi’)

Política:
- Nunca prometa data exata de chegada (quem define é a logística da Shopee).
- Jamais peça ou prometa nada sobre PIX quando a intenção for “pular”.

SAÍDA OBRIGATÓRIA (JSON apenas):
{
  "intent": "<quebra|faltando|elogio|envio|embalagem_precompra|prazo_data|etiqueta_fragil|duvida_produto|pular>",
  "reason": "<1 frase explicando>",
  "needs_reply": true/false,
  "signals": { "tem_foto": bool, "urgencia": bool, "pre_venda": bool }
}
"""

# ---------------------------
# Utils de parsing/matching
# ---------------------------
def _strip_code_fences(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.DOTALL).strip()
    return s

def _first_json_object(s: str) -> str | None:
    if not s:
        return None
    m = re.search(r"\{.*?\}", s, flags=re.DOTALL)
    return m.group(0) if m else None

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.lower()

# ---------------------------
# Classificação (inalterada)
# ---------------------------
def classify(pairs: List[Tuple[str, str]]) -> dict:
    """Classifica a intenção considerando apenas mensagens do comprador."""
    model = get_gemini()
    history = "\n".join([t for r, t in pairs if r == "buyer"][-8:])
    try:
        resp = model.generate_content(f"{PROMPT}\n\nHISTORICO:\n{history}")
        txt = _strip_code_fences((getattr(resp, "text", None) or "").strip())
        blob = _first_json_object(txt) or txt
        data = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("JSON não é um objeto")
        return data
    except Exception:
        return _fallback_classify(pairs)

def _fallback_classify(pairs: List[Tuple[str, str]]) -> dict:
    buyer_history = [t for r, t in pairs if r == "buyer"]
    raw = " ".join(buyer_history[-8:])
    t = _norm(raw)

    def has(*keys):
        return any(k in t for k in keys)

    # pular
    if has("pix", "comprovante", "nao recebi o pix", "pix nao caiu", "reembolso nao caiu"):
        return {"intent": "pular", "reason": "pix/reembolso pendente", "needs_reply": False,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": False}}
    if has("cade a peca", "prometeram enviar", "prometeram a peca", "ficaram de enviar", "nao enviaram ainda"):
        return {"intent": "pular", "reason": "cobranca de peca prometida", "needs_reply": False,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": False}}

    # novas intenções
    if has("bem embalado", "embalado", "embalagem", "amassar", "amassado", "avaria") and not has("chegou", "veio", "recebi", "foto"):
        return {"intent": "embalagem_precompra", "reason": "duvida de embalagem (pre-venda)", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": True}}
    if has("chegue ate", "chegar ate", "ate o dia", "preciso para", "prazo ate", "aniversario", "final de semana"):
        return {"intent": "prazo_data", "reason": "data especifica desejada", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": True, "pre_venda": not has("veio", "chegou", "recebi")}}
    if has("fragil", "etiqueta fragil", "aviso na embalagem"):
        return {"intent": "etiqueta_fragil", "reason": "pedido de etiqueta FRAGIL", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": not has("veio", "chegou", "recebi")}}
    if has("furinho", "furo", "furacao", "parafusar", "medida", "tamanho", "material"):
        return {"intent": "duvida_produto", "reason": "duvida de caracteristica do produto", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": not has("veio", "chegou", "recebi")}}

    # existentes
    if has("quebrou", "quebrado", "trincado", "amassado", "danificado", "rachado", "defeito", "estragado", "avaria"):
        return {"intent": "quebra", "reason": "dano/defeito relatado", "needs_reply": True,
                "signals": {"tem_foto": has("foto", "imagem", "segue foto"), "urgencia": has("urgente", "desesperad"), "pre_venda": False}}
    if has("faltou", "faltando", "nao veio", "veio faltando", "sem parafuso", "sem peca"):
        return {"intent": "faltando", "reason": "item faltante", "needs_reply": True,
                "signals": {"tem_foto": has("foto", "imagem"), "urgencia": has("urgente"), "pre_venda": False}}
    if has("chegou certinho", "amei", "perfeito", "obrigado", "obrigada", "tudo certo", "deu certo"):
        return {"intent": "elogio", "reason": "elogio/recebido", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": False}}
    if has("prazo", "quando chega", "nao chegou", "rastreamento", "rastreio", "codigo", "tracking"):
        return {"intent": "envio", "reason": "duvida logistica", "needs_reply": True,
                "signals": {"tem_foto": False, "urgencia": has("urgente"), "pre_venda": not has("veio", "chegou", "recebi")}}

    return {"intent": "envio", "reason": "fallback neutro", "needs_reply": True,
            "signals": {"tem_foto": False, "urgencia": False, "pre_venda": not has("veio", "chegou", "recebi")}}

# ============================================================
# Manager + Critic (duas passadas baratas) para refinar resposta
# ============================================================

_MANAGER_PROMPT = r"""
Você é o MANAGER de atendimento. Objetivo: gerar um rascunho curto e educado da resposta.

REGRAS:
- Não prometa data exata de entrega.
- Não mencione/peça PIX/reembolso se o cliente falou disso.
- Não altere políticas nem opções (apenas reescreva).
- Mantenha 1–2 frases, claras e amistosas.
- Se faltar informação essencial, peça **uma** coisa por vez.

ENTRADA:
- Mensagem do cliente: {{BUYER}}
- Resposta sugerida (não precisa copiar literalmente): {{DRAFT}}

SAÍDA OBRIGATÓRIA EM JSON:
{
  "draft": "<texto curto e educado>",
  "signals": {
    "asked_clarification": true/false
  }
}
"""

_CRITIC_PROMPT = r"""
Você é o CRITIC. Revise o texto final com este checklist:

CHECKLIST:
- Curto (máx. ~2 frases).
- Tom cordial e claro, sem jargão.
- Não prometa data de entrega.
- Não fale de PIX/reembolso.
- Não mude condições/opções originais.
- Se o MANAGER pediu 1 esclarecimento, mantenha um pedido simples.

ENTRADA:
- Mensagem do cliente: {{BUYER}}
- Texto do MANAGER: {{MANAGER_DRAFT}}

SAÍDA OBRIGATÓRIA EM JSON:
{ "final": "<texto pronto para enviar>" }
"""

def _gen_json(model, prompt: str) -> dict:
    """Chama o modelo, limpa cercas e retorna dict JSON (ou lança)."""
    resp = model.generate_content(prompt)
    raw = _strip_code_fences((getattr(resp, "text", None) or "").strip())
    blob = _first_json_object(raw) or raw
    return json.loads(blob)

def refine_reply(reply: str, pairs: List[Tuple[str, str]] | None = None) -> str:
    """
    Pipeline manager -> critic:
      1) Manager cria rascunho curto e educado.
      2) Critic aplica checklist e retorna o texto final.
    """
    if not settings.gemini_api_key:
        return reply
    try:
        model = get_gemini()

        buyer_msgs = [t for r, t in (pairs or []) if r == "buyer"]
        buyer_text = " | ".join(buyer_msgs[-3:])

        # 1) Manager
        mgr_prompt = (
            _MANAGER_PROMPT
            .replace("{{BUYER}}", buyer_text)
            .replace("{{DRAFT}}", reply or "")
        )
        try:
            mgr_data = _gen_json(model, mgr_prompt)
            manager_draft = (mgr_data.get("draft") or "").strip()
        except Exception:
            manager_draft = reply  # fallback: usa resposta original

        if not manager_draft:
            manager_draft = reply

        # 2) Critic
        critic_prompt = (
            _CRITIC_PROMPT
            .replace("{{BUYER}}", buyer_text)
            .replace("{{MANAGER_DRAFT}}", manager_draft)
        )
        try:
            crt_data = _gen_json(model, critic_prompt)
            final_txt = (crt_data.get("final") or "").strip()
            return final_txt or manager_draft or reply
        except Exception:
            return manager_draft or reply

    except Exception:
        return reply

