# gemini_client.py
import google.generativeai as genai
from .config import settings
from .firebase_client import get_product_by_sku
import json
import re
from typing import Any, Dict, Literal, List, Tuple
from pydantic import BaseModel, Field, ValidationError


class Entities(BaseModel):
    sku: str = ""
    pedido: str = ""
    produto: str = ""
    medida: str = ""


class PolicyFlags(BaseModel):
    nao_altera_endereco: bool = True
    nao_cobra_fora_app: bool = True


class GeminiResponse(BaseModel):
    action: Literal["reply", "skip", "ask_clarifying", "escalate"]
    reply: str
    intent: str
    confidence: float
    entities: Entities = Field(default_factory=Entities)
    policy_flags: PolicyFlags = Field(default_factory=PolicyFlags)
    reasons: list[str] = Field(default_factory=list)


ADDRESS_RE = re.compile(
    r"\b(rua|avenida|av\\.?|estrada|bairro|cep|logradouro|end(?:ereç|ereco))\b",
    re.I,
)
OFF_APP_RE = re.compile(
    r"\b(pix|transfer[êe]ncia|dep[óo]sito|whatsapp|zap|telefone|tel\\.?|boleto|chave)\b",
    re.I,
)


def validate_reply_text(text: str) -> bool:
    low = (text or "").lower()
    if ADDRESS_RE.search(low):
        return False
    if OFF_APP_RE.search(low):
        return False
    return True


JSON_CONTRACT = (
    "{\n"
    '  "action":"reply|skip|ask_clarifying|escalate",\n'
    '  "reply":"...",\n'
    '  "intent":"medidas|endereco|peca_faltando|reembolso_parcial|pos_venda|...",\n'
    '  "confidence": 0.0,\n'
    '  "entities": {"sku":"","pedido":"","produto":"","medida":""},\n'
    '  "policy_flags":{"nao_altera_endereco":true,"nao_cobra_fora_app":true},\n'
    '  "reasons":["...","..."]\n'
    "}"
)


def get_gemini():
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ausente. Configure no .env")
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={
            "temperature": 0.2,
            "top_p": 0.9,
        },
    )


def send_product_payload(produto: Dict[str, Any], contexto: str, objetivo: str) -> str:
    """Envia dados do produto e contexto ao Gemini e retorna a resposta.

    O payload enviado segue o formato definido pelas regras de negócio:
    {
        "produto": {...},
        "contextoConversa": "...",
        "objetivo": "..."
    }
    """
    if not settings.gemini_api_key:
        return ""
    model = get_gemini()
    payload = {
        "produto": produto,
        "contextoConversa": contexto,
        "objetivo": objetivo,
    }
    try:
        resp = model.generate_content(json.dumps(payload, ensure_ascii=False))
        return (getattr(resp, "text", "") or "").strip()
    except Exception:
        return ""


def detect_order_stage(order_info: dict | None) -> str:
    """Infer the order stage (pré-venda/pos-venda/enviado/entregue)."""

    if not order_info:
        return "desconhecido"
    st_raw = (
        order_info.get("status") or order_info.get("status_consolidado") or ""
    ).strip()
    st = st_raw.lower()
    fields = order_info.get("fields") or {}
    order_id = order_info.get("orderId") or ""
    payment_time = fields.get("Payment Time", "") or fields.get("Hora do pagamento", "")
    completed_time = fields.get("Completed Time", "") or fields.get(
        "Hora de conclusão", ""
    )
    logistics_status = fields.get("Logistics Status", "") or fields.get(
        "Status logístico", ""
    )
    latest_desc = order_info.get("logistics_latest_desc", "") or fields.get(
        "Latest Logistics Description", ""
    )

    shipped_tokens = (
        "shipped",
        "enviado",
        "a caminho",
        "in transit",
        "out for delivery",
        "despachado",
    )
    delivered_tokens = ("delivered", "entregue", "completed", "finalizado", "concluído")

    if completed_time or any(tok in st for tok in delivered_tokens):
        fase = "entregue"
    elif (
        any(tok in st for tok in shipped_tokens)
        or "pedido entregue" in latest_desc.lower()
    ):
        fase = "enviado"
    elif (
        order_id
        or payment_time
        or any(tok in st for tok in ("to ship", "ready to ship"))
    ):
        fase = "pos_venda"
    else:
        fase = "pre_venda"
    return fase


def _order_stage_context(order_info: dict | None) -> str:
    """Gera um pequeno resumo do estágio do pedido para orientar o modelo (NÃO exibir ao cliente)."""

    fase = detect_order_stage(order_info)
    if not order_info:
        order_id = ""
        st_raw = ""
        payment_time = ""
        logistics_status = ""
        latest_desc = ""
        completed_time = ""
    else:
        st_raw = (
            order_info.get("status") or order_info.get("status_consolidado") or ""
        ).strip()
        fields = order_info.get("fields") or {}
        order_id = order_info.get("orderId") or ""
        payment_time = fields.get("Payment Time", "") or fields.get("Hora do pagamento", "")
        completed_time = fields.get("Completed Time", "") or fields.get(
            "Hora de conclusão", ""
        )
        logistics_status = fields.get("Logistics Status", "") or fields.get(
            "Status logístico", ""
        )
        latest_desc = order_info.get("logistics_latest_desc", "") or fields.get(
            "Latest Logistics Description", ""
        )

    return (
        f"estado_pedido: {fase}\n"
        f"order_id: {order_id}\n"
        f"status: {st_raw}\n"
        f"payment_time: {payment_time}\n"
        f"logistics_status: {logistics_status}\n"
        f"latest_logistics_description: {latest_desc}\n"
        f"completed_time: {completed_time}\n"
    )


def generate_reply(
    history: str,
    order_info: dict | None = None,
    policy_context: str = "",
) -> GeminiResponse | None:
    """Gera resposta estruturada com base nas últimas mensagens e contexto do pedido.

    `policy_context` pode incluir trechos oficiais que devem ser considerados pelo modelo.
    """
    if not settings.gemini_api_key:
        return None
    try:
        if order_info and not order_info.get("product_info"):
            sku = order_info.get("sku")
            if sku:
                prod = get_product_by_sku(sku)
                if prod:
                    order_info = dict(order_info)
                    order_info["product_info"] = prod

        model = get_gemini()
        contexto = _order_stage_context(order_info)
        prod = order_info.get("product_info") if order_info else None
        prod_context = ""
        if prod:
            prod_context = (
                "[Dados do Produto]\n"
                f"nome: {prod.get('nome','')}\n"
                f"sku: {prod.get('sku','')}\n"
                f"descricao: {prod.get('descricao','')}\n"
                f"medidas: {prod.get('medidas','')}\n\n"
            )

        prompt = f"""{settings.base_prompt}
{policy_context}INSTRUÇÕES ADICIONAIS (NÃO MOSTRAR AO CLIENTE):
- Use o contexto do pedido abaixo para entender se é pré-venda, pós-venda, enviado ou entregue.
- Se estado_pedido for "enviado" ou "entregue", **não** use o template de tempo_envio. Se perguntarem prazo, peça UM esclarecimento objetivo (ex.: “é para este pedido ou um novo?”) sem citar status/rastreio.
- Se a política for "pular" (ex.: pix/comprovante), devolva APENAS: "Ação: skip (pular)".
- Caso contrário, responda usando APENAS o JSON com o schema abaixo.

{prod_context}[Contexto do Pedido]
{contexto}

[Conversa]
{history}

Responda APENAS com um JSON seguindo este schema:
{JSON_CONTRACT}
""".strip()

        resp = model.generate_content(prompt)
        text = (getattr(resp, "text", "") or "").strip()
        data = json.loads(text)
        return GeminiResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError, Exception):
        return None


def summarize_history(pairs: List[Tuple[str, str]]) -> str:
    """Generate a factual TL;DR summary of the conversation history."""

    if not settings.gemini_api_key or not pairs:
        return ""
    model = get_gemini()
    text = "\n".join(f"{r}: {t}" for r, t in pairs)
    prompt = (
        "Resuma de forma factual e sem opinião a conversa a seguir em até 60 palavras:\n\n"
        + text
    )
    try:
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", "") or "").strip()
    except Exception:
        return ""
