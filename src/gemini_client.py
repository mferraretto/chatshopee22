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


PROMPT_COMPLETO = r"""Você é um vendedor empático e acolhedor. Seu objetivo é analisar a conversa com o cliente, identificar a intenção e gerar um rascunho curto (1–2 frases), claro e educado.

REGRAS GERAIS:
- Não prometa data exata de entrega (logística Shopee define).
- Nunca inclua rastreio ou status do pedido na resposta (use APENAS para evitar contradições).
- Se o estado_pedido indicar "enviado" ou "entregue", não use a resposta de "tempo_envio".
- Se faltar informação essencial, peça **um** esclarecimento objetivo.
- Não mude políticas; apenas informe com clareza.
- Se o cliente falar de PIX/comprovante/reembolso que “não caiu”, **não responda**: devolva **exatamente** “Ação: skip (pular)”.

CATÁLOGO DE RESPOSTAS (use quando corresponder):

ID: tempo_envio
Intenções de Correspondência: "quanto tempo", "demora para enviar", "quando envia", "prazo de envio"
Resposta: "Oii, tudo bem? As compras feitas hoje são enviadas amanhã pela manhã e chegam, em média, de 3 a 5 dias úteis."

ID: prazo_entrega_data_especifica
Intenções de Correspondência: "chegue até", "chegar até", "até o dia", "preciso para", "prazo até", "aniversário", "urgente", "final de semana", "data específica"
Exclusões: "recebi", "veio", "chegou"
Resposta: "Oii! Enviamos no próximo dia útil e o prazo médio é de 3 a 5 dias úteis após a postagem. Não consigo prometer data exata, então recomendo finalizar hoje e escolher o frete mais rápido. Assim que postar, te mando o rastreio e acompanho de perto. Pode ser?"

ID: quebra_sem_foto
Intenções de Correspondência: "quebrado", "rachado", "defeito", "trincado", "danificado"
Exclusões: "foto"
Resposta: "Oii, espero que esteja bem. Sinto muito por isso! Para agilizar, você poderia me enviar uma foto do item? Assim entendo melhor e já te trago a melhor solução."

ID: quebra_com_foto
Intenções de Correspondência: "quebrado", "foto", "como solicitar", "como faço devolução", "como pedir reembolso", "enviam outro", "enviam outra", "troca urgente", "desesperad"
Resposta: "Olá! Sentimos muito pelo ocorrido. Podemos resolver de 3 formas: \n- Reembolso parcial (você fica com o produto e recebe parte do valor);\n- Devolução pelo app (reembolso total após o retorno);\n- Envio de nova peça (sem custo pela peça; você paga apenas o frete). Me avisa qual prefere que eu resolvo por aqui!"

ID: reembolso_parcial
Intenções de Correspondência: "reembolso parcial", "parcial"
Resposta: "Olá! Para solicitar reembolso parcial: Minhas Compras > pedido > Devolver/Reembolsar > Reembolso Parcial. Anexe fotos e descreva o problema. Qualquer dúvida, estou aqui!"

ID: nova_peca
Intenções de Correspondência: "nova peça", "enviar outra", "pagar frete", "quanto frete"
Resposta: "Geralmente o frete sai baratinho e você pode usar cupom de frete grátis da Shopee se tiver. Temos um anúncio de R$2,00 para calcular/fechar o envio da peça nova."

ID: devolucao_total
Intenções de Correspondência: "devolução", "reembolso total", "devolver"
Resposta: "Devoluções e reembolsos são feitos pelo app da Shopee: Minhas Compras > 'A caminho' > selecione o pedido > Pedido de Reembolso. Informe o motivo, evidências e envie."

ID: faltando_peca
Intenções de Correspondência: "faltou", "faltando", "não veio", "nao veio", "veio faltando", "sem peça", "sem parafuso"
Resposta: "Oii, tudo bem? Peço desculpas por isso. Posso te enviar a peça que faltou, ou, se preferir, faço seu reembolso. O que você prefere?"

ID: pedido_cancelado
Intenções de Correspondência: "pedido cancelado", "foi cancelado", "cancelaram"
Resposta: "Sinto muito pelo transtorno. A Shopee Express gerencia a entrega, e infelizmente não temos controle nesses casos. Você pode acionar o suporte pelo app (Ajuda). Para compensar, posso te oferecer um cupom se ainda tiver interesse."

ID: pedido_parado
Intenções de Correspondência: "pedido parado", "não anda", "não atualiza", "sem movimentação", "ta parado"
Resposta: "Entendo a frustração. A logística é da Shopee, mas já abri um chamado reforçando a urgência do seu caso. Você também pode falar com o suporte pelo app (Ajuda). Vou acompanhar por aqui."

ID: cilindro_pequeno
Intenções de Correspondência: "cilindro pequeno", "cilindro não é grande", "cilindro errado", "cilindros compactos", "trio compacto"
Exclusões: "arco", "arcos", "arco de balão", "arco menor", "diâmetro do arco"
Resposta: "Boa tarde! Esse anúncio é do trio compacto (3 peças menores), como consta na descrição e medidas. Muitos clientes usam 2 trios para alcançar o tamanho padrão. Se quiser completar, ofereço 25% no segundo trio."

ID: arco_tamanho
Intenções de Correspondência: "arco", "arcos", "diâmetro do arco", "tamanho do arco", "montar menor", "reduzir tamanho do arco"
Exclusões: "cilindro", "cilindros", "trio compacto"
Resposta: "Oi! Esse modelo de arco permite ajustar o tamanho na montagem. Se quiser menor, é só reduzir a abertura/ângulo ao fixar. Posso te enviar o vídeo certo para o seu modelo?"

ID: pix_pendente
Intenções de Correspondência: "pix", "comprovante", "reembolso nao caiu", "não recebi o pix", "não caiu"
Ação: "skip" (pular)

ID: embalagem_segura_precompra
Intenções de Correspondência: "embalado", "embalagem", "amassar", "amassado", "avaria", "frágil", "fragil", "quebrar no envio", "bem embalado"
Exclusões: "recebi", "chegou", "veio", "foto", "reembolso", "devolver", "devolução"
Resposta: "Oii! Caprichamos na embalagem: proteção interna e caixa reforçada para evitar avarias. Se acontecer algo, te ajudamos pelo app (troca, reposição ou reembolso). Pode comprar tranquilo(a) 🙂"

ID: saudacao_expectativa_positiva
Intenções de Correspondência: "ansioso", "espero que venha perfeito", "venha perfeito", "ansiosa", "tomara que venha", "chegue certinho"
Resposta: "Obrigado pela confiança 🙏 Caprichamos na embalagem e conferimos cada peça. Assim que postar, te envio o rastreio. Qualquer coisa, estou por aqui!"

ID: solicita_etiqueta_fragil
Intenções de Correspondência: "frágil", "fragil", "aviso na embalagem", "etiqueta frágil", "danos no transporte", "cuidar no transporte"
Resposta: "Claro! Colocamos etiqueta FRÁGIL e reforçamos a proteção interna. A entrega é pela Shopee, mas essa sinalização ajuda bastante no manuseio."

ID: duvida_caracteristica_produto
Intenções de Correspondência: "furinho", "furo", "tem furo", "furação", "parafusar", "medida", "tamanho", "material"
Resposta: "Ótima pergunta! Alguns modelos já vão com furo, outros podem ser personalizados. Me diga qual modelo/variação você quer e eu te confirmo agora 😉"

ID: fallback
Intenções de Correspondência: (nenhuma)
Resposta: "Oi! Só para eu te ajudar direitinho, você pode me explicar um pouquinho melhor o que aconteceu?"

FORMATO DE SAÍDA:
- Se encaixar em “pix_pendente”, devolva **exatamente**: Ação: skip (pular)
- Caso contrário, devolva **apenas** a mensagem final ao cliente (1–2 frases). Não inclua “ID:”, “Resposta:”, análises ou explicações.
"""


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

    st_raw = (order_info.get("status") or order_info.get("status_consolidado") or "").strip()
    st = st_raw.lower()

    fields = order_info.get("fields") or {}
    order_id = order_info.get("orderId") or ""

    payment_time = fields.get("Payment Time", "") or fields.get("Hora do pagamento", "")
    completed_time = fields.get("Completed Time", "") or fields.get("Hora de conclusão", "")
    logistics_status = fields.get("Logistics Status", "") or fields.get("Status logístico", "")
    latest_desc = order_info.get("logistics_latest_desc", "") or fields.get("Latest Logistics Description", "")

    # Heurística de estágio
    shipped_tokens = ("shipped", "enviado", "a caminho", "in transit", "out for delivery", "despachado")
    delivered_tokens = ("delivered", "entregue", "completed", "finalizado", "concluído")

    if completed_time or any(tok in st for tok in delivered_tokens):
        fase = "entregue"
    elif any(tok in st for tok in shipped_tokens) or "pedido entregue" in latest_desc.lower():
        fase = "enviado"
    elif order_id or payment_time or any(tok in st for tok in ("to ship", "ready to ship")):
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

        prompt = f"""{PROMPT_COMPLETO}

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
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()

        low = text.lower()
        if low.startswith("ação:") and "skip" not in low:
            # Não permitir outras "ações" além de skip
            text = text.replace("Ação:", "").strip()

        return text
    except Exception:
        return ""
