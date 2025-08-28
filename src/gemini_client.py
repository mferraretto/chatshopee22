# gemini_client.py
import google.generativeai as genai
from .config import settings
from .firebase_client import get_product_by_sku
import json
import re
from typing import Any, Dict


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


def classify_conversation(history: str) -> Dict[str, str]:
    """Classifica uma conversa retornando {intent, estado, sentimento, urgencia}.

    Usa o Gemini para produzir um JSON estruturado. Em caso de falha ou chave
    ausente, devolve dicionário vazio.
    """

    if not settings.gemini_api_key:
        return {}
    model = get_gemini()
    prompt = (
        "Classifique a conversa abaixo retornando um JSON com as chaves"
        " intent, estado, sentimento e urgencia.\n"
        "Estados possíveis: pre_venda, pos_venda_sem_problema, pos_venda_problema,"
        " pagamento/checkout, silencio_do_cliente, encerrado.\n\n"
        f"Conversa:\n{history}"
    )
    try:
        resp = model.generate_content(prompt)
        txt = (getattr(resp, "text", "") or "").strip()
        # Tenta extrair o primeiro bloco JSON da resposta
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            return json.loads(m.group(0))
        return json.loads(txt)
    except Exception:
        return {}


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


def generate_reply(
    history: str,
    order_info: dict | None = None,
    analysis: Dict[str, str] | None = None,
) -> str:
    """Gera resposta direta com base nas últimas mensagens + contexto.

    ``analysis`` pode conter metadados retornados por ``classify_conversation``
    (intent, estado, sentimento, urgencia) para orientar o tom da resposta.
    Também garante que, se houver um SKU disponível, os dados do produto
    correspondente sejam recuperados do sistema de produtos e enviados como
    contexto ao Gemini."""
    if not settings.gemini_api_key:
        return ""
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
        analysis_context = ""
        if analysis:
            analysis_context = (
                "[Analise da Conversa]\n"
                f"intent: {analysis.get('intent', '')}\n"
                f"estado: {analysis.get('estado', '')}\n"
                f"sentimento: {analysis.get('sentimento', '')}\n"
                f"urgencia: {analysis.get('urgencia', '')}\n\n"
            )
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

INSTRUÇÕES ADICIONAIS (NÃO MOSTRAR AO CLIENTE):
- Use o contexto do pedido abaixo para entender se é pré-venda, pós-venda, enviado ou entregue.
- Se estado_pedido for "enviado" ou "entregue", **não** use o template de tempo_envio. Se perguntarem prazo, peça UM esclarecimento objetivo (ex.: “é para este pedido ou um novo?”) sem citar status/rastreio.
- Se a política for "pular" (ex.: pix/comprovante), devolva APENAS: "Ação: skip (pular)".
- Caso contrário, devolva APENAS a mensagem final em 1–2 frases (sem "ID:", sem "Resposta:", sem análises).

{analysis_context}{prod_context}[Contexto do Pedido]
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
