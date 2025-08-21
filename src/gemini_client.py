# gemini_client.py
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
        },
    )


def _order_stage_context(order_info: dict | None) -> str:
    """Gera um pequeno resumo do estágio do pedido para orientar o modelo (NÃO exibir ao cliente)."""
    # Default (sem info)
    if not order_info:
        return (
            "estado_pedido: desconhecido\n"
            "order_id:\n"
            "status:\n"
            "payment_time:\n"
            "logistics_status:\n"
            "latest_logistics_description:\n"
            "completed_time:\n"
        )

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

    # Heurística de estágio
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

    return (
        f"estado_pedido: {fase}\n"
        f"order_id: {order_id}\n"
        f"status: {st_raw}\n"
        f"payment_time: {payment_time}\n"
        f"logistics_status: {logistics_status}\n"
        f"latest_logistics_description: {latest_desc}\n"
        f"completed_time: {completed_time}\n"
    )


def generate_reply(history: str, order_info: dict | None = None) -> str:
    """Gera resposta direta com base nas últimas mensagens + contexto do pedido."""
    if not settings.gemini_api_key:
        return ""
    try:
        model = get_gemini()
        contexto = _order_stage_context(order_info)

        prompt = f"""{settings.base_prompt}

INSTRUÇÕES ADICIONAIS (NÃO MOSTRAR AO CLIENTE):
- Use o contexto do pedido abaixo para entender se é pré-venda, pós-venda, enviado ou entregue.
- Se estado_pedido for "enviado" ou "entregue", **não** use o template de tempo_envio. Se perguntarem prazo, peça UM esclarecimento objetivo (ex.: “é para este pedido ou um novo?”) sem citar status/rastreio.
- Se a política for "pular" (ex.: pix/comprovante), devolva APENAS: "Ação: skip (pular)".
- Caso contrário, devolva APENAS a mensagem final em 1–2 frases (sem "ID:", sem "Resposta:", sem análises).

[Contexto do Pedido]
{contexto}

[Conversa]
{history}
""".strip()

        resp = model.generate_content(prompt)
        text = (getattr(resp, "text", "") or "").strip()

        # Higienização: remover aspas externas e evitar "Ação:" indevida
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            text = text[1:-1].strip()

        low = text.lower()
        if low.startswith("ação:") and "skip" not in low:
            # Não permitir outras "ações" além de skip
            text = text.replace("Ação:", "").strip()

        return text
    except Exception:
        return ""
