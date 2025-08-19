import json
import re
import unicodedata
import google.generativeai as genai
from .config import settings

def get_gemini():
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ausente. Configure no .env")
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={
            "temperature": 0.2,
            "top_p": 0.9,
            "response_mime_type": "application/json"  # força JSON
        }
    )

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

# --- util: remove acentos para facilitar matching do fallback
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    return s.lower()

def classify(messages: list[str]) -> dict:
    model = get_gemini()
    history = "\n".join(messages[-8:])
    try:
        resp = model.generate_content(f"{PROMPT}\n\nHISTORICO:\n{history}")
        txt = (getattr(resp, "text", None) or "").strip()

        # limpa cercas de código
        if txt.startswith("```"):
            txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.IGNORECASE | re.DOTALL).strip()

        # pega o primeiro bloco JSON { ... }
        m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
        if m:
            txt = m.group(0)

        data = json.loads(txt)
        if not isinstance(data, dict):
            raise ValueError("JSON não é um objeto")
        return data

    except Exception:
        return _fallback_classify(messages)

def _fallback_classify(messages: list[str]) -> dict:
    raw = " ".join(messages[-8:])
    t = _norm(raw)

    def has(*keys):
        return any(k in t for k in keys)

    # pular: PIX ou cobrança de item prometido
    if has("pix", "comprovante", "nao recebi o pix",
           "pix nao caiu", "reembolso nao caiu"):
        return {"intent": "pular", "reason": "pix/reembolso pendente", "needs_reply": False,
                "signals": {"tem_foto": False, "urgencia": False, "pre_venda": False}}
    if has("cade a peca", "prometeram enviar", "prometeram a peca",
           "ficaram de enviar", "nao enviaram ainda"):
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

def refine_reply(reply: str, buyer_text: str = "") -> str:
    if not settings.gemini_api_key:
        return reply
    try:
        model = get_gemini()
        prompt = (
            "Você é um assistente de atendimento. Reescreva a resposta mantendo-a curta, clara e educada, "
            "sem prometer data exata de entrega, sem falar sobre PIX/reembolso quando o cliente falar disso, "
            "e sem alterar as opções/condições apresentadas. Preserve o sentido.\n\n"
            f"Mensagem do cliente: {buyer_text}\nResposta sugerida: {reply}\n\n"
            "Retorne somente o texto reescrito (sem JSON)."
        )
        resp = model.generate_content(prompt)
        text = (getattr(resp, "text", None) or "").strip()
        return text or reply
    except Exception:
        return reply


