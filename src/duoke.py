# src/duoke.py
import inspect
import asyncio
import os
import re
import json
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import time

from playwright.async_api import (
    async_playwright,
    Error as PwError,
    TimeoutError as PWTimeoutError,
)
from .config import settings
from .complaint_classifier import decide_reply as complaint_decide_reply
from .cases import (
    append_row as log_case,
    append_label as log_label,
    infer_problema,
    determine_label,
)
from .history import get_history, append_history

# Carrega seletores configuráveis
SEL = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "selectors.json").read_text(
        encoding="utf-8"
    )
)

WANTS_PARTS_RE = re.compile(
    r"(quero|prefiro|pode|manda|mandar|envia|enviar|me envia|me mandar).{0,25}(peça|peças|pecas|as peças|as pecas|a peça|a peca)",
    re.I,
)


def buyer_wants_missing_parts(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if WANTS_PARTS_RE.search(t):
        return True
    simples = [
        "quero as peças",
        "pode enviar as peças",
        "prefiro as peças",
        "pode mandar as peças",
        "quero receber a peça",
        "manda a peça",
        "envia as peças",
        "prefiro receber a peça",
    ]
    return any(s in t for s in simples)


async def safe_text(locator):
    try:
        if await locator.count() == 0:
            return None
        txt = (await locator.first.inner_text()).strip()
        return re.sub(r"\s+", " ", txt)
    except Exception:
        return None


async def extract_order_details_with_selectors(page, SEL: dict) -> Dict[str, Any]:
    """Extrai detalhes do pedido do painel lateral usando seletores CSS específicos."""
    details: Dict[str, Any] = {}

    selector_map = {
        "status": "status_badge",
        "buyer_payment_amount": "buyer_payment_amount",
        "payment_method": "payment_method",
        "payment_time": "payment_time",
        "shipping_provider": "shipping_provider",
        "tracking_number": "tracking_number",
        "logistics_status": "logistics_status",
        "latest_logistics_description": "latest_logistics_description",
        "logistics_update_time": "logistics_update_time",
        "completed_time": "completed_time",
    }

    for key, selector_key in selector_map.items():
        selector = SEL.get(selector_key)
        if selector:
            text_content = await safe_text(page.locator(selector))
            if text_content:
                details[key] = text_content

    try:
        product_info = await extract_order_from_dom(page, SEL)
        details.update(product_info)
    except Exception:
        details["title"] = await safe_text(page.locator(SEL.get("item_title", "")))
        details["variation"] = await safe_text(page.locator(SEL.get("item_variation", "")))
        details["sku"] = await safe_text(page.locator(SEL.get("item_sku", "")))

    try:
        content = await page.locator("div.order_item_info_id").inner_text()
        match = re.search(r"#([A-Z0-9]{10,})", content)
        if match:
            details["orderId"] = match.group(1)
    except Exception:
        pass

    details.setdefault("sku", "")
    details.setdefault("orderId", "")
    details.setdefault("status", "")

    details["status_consolidado"] = (
        details.get("status") or details.get("logistics_status") or "desconhecido"
    )
    details["logistics_latest_desc"] = details.get("latest_logistics_description", "")

    return details


async def extract_order_from_dom(page, SEL: dict) -> Dict[str, Any]:
    await page.wait_for_selector(SEL["order.product_list"], state="visible", timeout=20000)
    await page.wait_for_selector(SEL["order.buyer_name"], state="visible", timeout=20000)

    buyer_name = await safe_text(page.locator(SEL["order.buyer_name"]))

    items = page.locator(SEL["order.product_list"])
    count = await items.count()
    products: List[Dict[str, str]] = []

    for i in range(count):
        item = items.nth(i)

        title = await safe_text(item.locator(SEL["order.product_title"]))
        variation_raw = await safe_text(item.locator(SEL["order.product_variation"]))
        sku_raw = await safe_text(item.locator(SEL["order.product_sku"]))

        variation = None
        if variation_raw:
            variation = re.sub(r"^(Varia[çc][aã]o[:：]\s*)?", "", variation_raw, flags=re.I).strip()
            if variation in ("-", "—", "–", ""):
                variation = ""

        sku = None
        if sku_raw:
            sku = re.sub(r"^SKU[:：]\s*", "", sku_raw, flags=re.I).strip()

        products.append({
            "title": title or "",
            "variation": variation or "",
            "sku": sku or "",
        })

    first = products[0] if products else {"title": "", "variation": "", "sku": ""}

    return {
        "buyer_name": buyer_name or "",
        "products": products,
        "title": first["title"],
        "variation": first["variation"],
        "sku": first["sku"],
    }


# Botões de confirmação comuns em modais (várias línguas)
CONFIRM_RE = re.compile(
    r"(confirm|confirmar|ok|continue|verify|submit|login|entrar|fechar|iniciar\s*sess[aã]o|确认|確定|确定)",
    re.I,
)


def _env_or_settings(name_env: str, name_settings: str, default: str = "") -> str:
    v = os.getenv(name_env)
    if v:
        return v
    return str(getattr(settings, name_settings, default) or "")


class DuokeBot:
    """
    Bot Duoke independente de UI. Mantém referência à página atual para o espelho,
    faz login (com fechamento de modal), tenta detectar 2FA e expõe método para submeter o código.
    """

    def __init__(self, storage_state_path: str = "storage_state.json"):
        # Mantido por compat
        self.storage_state_path = storage_state_path
        # Página atual (usada pelo espelho da UI)
        self.current_page = None
        # Sinaliza quando ficou parado aguardando 2FA
        self.awaiting_2fa = False
        # Evento simples para pausar/retomar o ciclo via UI
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        # Registro de última resposta por conversa
        self.last_replied_at: dict[str, float] = {}
        # Armazena respostas já enviadas por conversa para evitar duplicatas
        self.sent_replies: dict[str, set[str]] = {}

    # ---------- infra de navegador ----------

    async def _new_context(self, p):
        """
        Contexto persistente: mantém cookies/localStorage dentro de 'pw-user-data'.
        Em produção (Render), iniciamos em headless e sem sandbox.
        """
        user_data_dir = Path(__file__).resolve().parents[1] / "pw-user-data"
        user_data_dir.mkdir(exist_ok=True)

        # HEADLESS=1 (padrão) para servidores sem display; HEADLESS=0 no dev local
        headless = os.getenv("HEADLESS", "1").lower() not in {"0", "false", "no"}

        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            ignore_https_errors=True,
            viewport={"width": 1366, "height": 768},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        # Bloqueia mídia/analytics e evita travas; resiliente a exceções
        async def _route_handler(route):
            req = route.request
            try:
                url = req.url.lower()
                if req.resource_type in {"media"} or "analytics" in url:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                # fallback defensivo para não quebrar o fluxo
                try:
                    await route.continue_()
                except Exception:
                    pass

        # importante: no contexto assíncrono, route deve ser aguardado
        await ctx.route("**/*", _route_handler)

        # injeta CSS para não depender de animações/transitions que atrasam cliques
        await ctx.add_init_script(
            """
        (() => {
          const style = document.createElement('style');
          style.innerHTML = '*{animation:none!important;transition:none!important;}';
          document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));
        })();
        """
        )

        return ctx

    async def _get_page(self, ctx):
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        self.current_page = page
        page.set_default_timeout(5000)
        return page

    # ---------- utilitários de login / 2FA ----------

    async def _click_confirm_anywhere(self, target) -> Optional[str]:
        """Tenta clicar num botão de confirmação no frame dado."""
        # por role/name
        try:
            await target.get_by_role("button", name=CONFIRM_RE).first.click(
                timeout=1200
            )
            return "role"
        except PWTimeoutError:
            pass

        # por seletores CSS comuns
        css_candidates = [
            SEL.get("modal_confirm_button", ""),
            ".el-message-box__btns button",
            ".el-dialog__footer .el-button--primary",
            "button.el-button--primary",
        ]
        for sel in css_candidates:
            if not sel:
                continue
            loc = target.locator(sel).locator(":visible")
            try:
                if await loc.count() > 0:
                    await loc.first.click(timeout=800)
                    return f"css:{sel}"
            except PWTimeoutError:
                try:
                    await target.evaluate(
                        "sel => { const el = document.querySelector(sel); if (el) el.click(); }",
                        sel,
                    )
                    return f"js:{sel}"
                except Exception:
                    continue

        # Busca genérica via JS por texto
        try:
            clicked = await target.evaluate(
                "names => {\n"
                "  const norm = s => (s || '').trim().toLowerCase();\n"
                "  const btn = Array.from(document.querySelectorAll('button')).find(b => names.includes(norm(b.textContent)));\n"
                "  if (btn) { btn.click(); return true; }\n"
                "  return false;\n"
                "}",
                ["confirm", "确定", "确认", "ok", "confirmar", "fechar"],
            )
            if clicked:
                return "js:text"
        except Exception:
            pass

        # Enter como último recurso
        try:
            await target.keyboard.press("Enter")
            return "enter"
        except Exception:
            return None

    async def _try_close_modal(self, page):
        try:
            await self.close_modal(page)
        except Exception:
            pass

    async def _find_login_frame(self, page):
        """
        Retorna (frame, sel_email, sel_pass). Se estiver na própria page, frame = page.
        """
        selectors_email = ["input[type='email']", "input[placeholder*='email' i]"]
        selectors_pass = ["input[type='password']", "input[placeholder*='password' i]"]

        # própria página
        for se in selectors_email:
            if await page.locator(se).count() > 0:
                for sp in selectors_pass:
                    if await page.locator(sp).count() > 0:
                        return page, se, sp

        # iframes
        for fr in page.frames:
            try:
                for se in selectors_email:
                    if await fr.locator(se).count() > 0:
                        for sp in selectors_pass:
                            if await fr.locator(sp).count() > 0:
                                return fr, se, sp
            except Exception:
                continue

        return None, None, None

    async def _is_logged_ui(self, page) -> bool:
        """
        Considera logado se achar contêiner de chat ou mensagens
        visíveis.
        """
        chat_list_container = SEL.get("chat_list_container", "")
        chat_list_item = SEL.get("chat_list_item", "ul.chat_list li")
        try:
            if chat_list_container:
                sel = f"{chat_list_container}, {chat_list_item}, ul.message_main"
            else:
                sel = f"{chat_list_item}, ul.message_main"
            loc = page.locator(sel).locator(":visible")
            return await loc.count() > 0
        except Exception:
            return False

    async def _detect_2fa_input(self, page):
        sel = "input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']"
        # procura na página e iframes
        if await page.locator(sel).count() > 0:
            return page, sel
        for fr in page.frames:
            try:
                if await fr.locator(sel).count() > 0:
                    return fr, sel
            except Exception:
                pass
        return None, None

    def _get_creds(self) -> Tuple[str, str]:
        email = _env_or_settings("DUOKE_EMAIL", "duoke_email")
        password = _env_or_settings("DUOKE_PASSWORD", "duoke_password")
        return email, password

    # ---------- login principal ----------

    async def ensure_login(self, page) -> None:
        """
        Vai até a URL, fecha modal de sessão expirada, faz login se necessário,
        tenta detectar 2FA. Se 2FA for solicitado, deixa self.awaiting_2fa=True
        e retorna (sem levantar exceção) — a UI deve chamar provide_2fa_code().
        """
        await page.goto(
            settings.douke_url,
            wait_until="domcontentloaded",
            timeout=settings.goto_timeout_ms,
        )

        try:
            await page.wait_for_timeout(800)
            await self.close_modal(page)
        except Exception:
            pass

        # Aguarda rede “assentar”
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        # Fecha modal “Your login has expired…”
        await self._try_close_modal(page)

        # Já está logado?
        if await self._is_logged_ui(page):
            self.awaiting_2fa = False
            return

        # Detecta formulário de login
        fr, sel_email, sel_pass = await self._find_login_frame(page)
        if fr is None:
            # Dá mais um tempo para montar UI
            try:
                await page.wait_for_timeout(1000)
            except Exception:
                pass
            fr, sel_email, sel_pass = await self._find_login_frame(page)

        if fr is None:
            # Pode ser que o chat não tenha renderizado ainda; não falha.
            return

        # Credenciais
        email, password = self._get_creds()
        if not email or not password:
            raise RuntimeError(
                "Credenciais Duoke ausentes. Defina DUOKE_EMAIL e DUOKE_PASSWORD (ou settings.duoke_email/duoke_password). "
                "Como alternativa, faça login manual executando `python -m src.login` antes de iniciar o bot."
            )

        # Preenche e tenta logar
        await fr.fill(sel_email, email)
        await fr.fill(sel_pass, password)

        # Clica Login (vários nomes)
        try:
            await fr.get_by_role(
                "button",
                name=re.compile(
                    r"(login|entrar|sign\s*in|iniciar\s*sess[aã]o|提交|登录)", re.I
                ),
            ).click(timeout=2500)
        except PWTimeoutError:
            # fallback: primeiro botão visível
            try:
                await fr.locator("button").first.click()
            except Exception:
                pass

        # Espera algo acontecer
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # Fecha modal novamente se reapareceu
        await self._try_close_modal(page)

        # 2FA?
        fr_code, sel_code = await self._detect_2fa_input(page)
        if fr_code and sel_code:
            # Deixa a UI saber que precisa do código
            self.awaiting_2fa = True
            return

        # Caso contrário, espera o chat aparecer
        try:
            chat_list_container = SEL.get("chat_list_container", "")
            chat_list_item = SEL.get("chat_list_item", "ul.chat_list li")
            if chat_list_container:
                await page.wait_for_selector(
                    f"{chat_list_container}, {chat_list_item}, ul.message_main",
                    timeout=30000,
                )
            else:
                await page.wait_for_selector(
                    f"{chat_list_item}, ul.message_main",
                    timeout=30000,
                )
        except Exception:
            # não quebra o fluxo, apenas segue
            pass

        self.awaiting_2fa = False

    async def provide_2fa_code(self, code: str) -> bool:
        """
        Chame este método quando a UI receber o código 2FA do usuário.
        Preenche e confirma; retorna True se login concluído.
        """
        page = self.current_page
        if not page:
            raise RuntimeError("Nenhuma página ativa para submeter o 2FA.")

        fr_code, sel_code = await self._detect_2fa_input(page)
        if not (fr_code and sel_code):
            # nada para fazer
            self.awaiting_2fa = False
            return True

        await fr_code.fill(sel_code, code)
        # botão de confirmar/verify/submit/login
        try:
            await fr_code.get_by_role("button", name=CONFIRM_RE).click(timeout=2000)
        except PWTimeoutError:
            try:
                await fr_code.locator("button").first.click()
            except Exception:
                pass

        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # tenta fechar eventual modal remanescente
        await self._try_close_modal(page)

        # considera logado se achar chat
        ok = await self._is_logged_ui(page)
        self.awaiting_2fa = not ok
        return ok

    # ---------- filtros/UX ----------

    async def apply_needs_reply_filter(self, page):
        if not getattr(settings, "apply_needs_reply_filter", False):
            return
        try:
            sel = SEL.get("filter_needs_reply", "")
            if not sel:
                return
            locator = page.locator(sel)
            if await locator.count() > 0:
                await locator.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def show_all_conversations(self, page):
        """Garante que todas as conversas estejam visíveis, removendo filtros como 'Precisa responder'."""
        try:
            sel = SEL.get("filter_all_conversations", "")
            if not sel:
                return
            locator = page.locator(sel)
            if await locator.count() > 0:
                await locator.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            # Não deve interromper o fluxo se o seletor não existir ou falhar
            pass

    # ---------- navegação entre conversas ----------

    def conversations(self, page):
        return page.locator(SEL.get("chat_list_item", "ul.chat_list li"))

    async def open_conversation_by_index(self, page, idx: int) -> bool:
        conv_locator = self.conversations(page)
        total = await conv_locator.count()
        if idx >= total:
            return False

        await conv_locator.nth(idx).click()

        # Aguarda painel renderizar
        try:
            if SEL.get("message_container"):
                await page.wait_for_selector(SEL["message_container"], timeout=9000)
            await page.wait_for_function(
                """() => {
                    const ul = document.querySelector('ul.message_main');
                    return ul && ul.children && ul.children.length > 0;
                }""",
                timeout=9000,
            )
        except Exception:
            pass

        try:
            if SEL.get("input_textarea"):
                await page.wait_for_selector(SEL["input_textarea"], timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(
            int(getattr(settings, "delay_between_actions", 1.0) * 1000)
        )
        return True

    # ---------- leitura de mensagens ----------

    async def read_messages_with_roles(self, page, depth: int) -> list[tuple[str, str]]:
        """Retorna últimos N [(role,text)], role ∈ {'buyer','seller'}."""
        out: list[tuple[str, str]] = []
        try:
            items = page.locator("ul.message_main > li")

            # Força mais histórico: rola ao topo algumas vezes
            try:
                container = page.locator(
                    SEL.get("message_container", "ul.message_main")
                ).first
                for _ in range(3):
                    await container.evaluate("(el) => { el.scrollTop = 0; }")
                    await page.wait_for_timeout(120)
            except Exception:
                pass

            texts = await items.evaluate_all(
                """
                (els) => els
                    .map(li => {
                        const cls = (li.className || '').toLowerCase();
                        const role = cls.includes('lt')
                            ? 'buyer'
                            : (cls.includes('rt') ? 'seller' : 'system');
                        const txtNode = li.querySelector('div.text_cont, .bubble .text, .record_item .content');
                        const txt = (txtNode?.innerText || '').trim();
                        const hasImg = !!li.querySelector('img');
                        if (role === 'system') return null;
                        if (txt) return [role, txt];
                        if (hasImg) return [role, '[imagem]'];
                        return null;
                    })
                    .filter(Boolean)
            """
            )
            out = texts[-depth:]
        except Exception:
            pass
        return out

    async def read_messages(self, page, depth: int = 8) -> list[str]:
        """Compat: apenas textos do comprador."""
        msgs: list[str] = []
        container = page.locator(SEL.get("message_container", "ul.message_main")).first
        if not await container.count():
            print("[DEBUG] Nenhum container de mensagens encontrado")
            return msgs

        for _ in range(3):
            try:
                await container.evaluate("(el) => { el.scrollTop = 0; }")
                await page.wait_for_timeout(60)
            except Exception:
                break

        try:
            nodes = page.locator("ul.message_main li.lt")
            msgs = await nodes.evaluate_all(
                """
                (els) => els
                    .map(li => {
                        const txtNode = li.querySelector('div.text_cont, .bubble .text, .record_item .content');
                        const txt = (txtNode?.innerText || '').trim();
                        const hasImg = !!li.querySelector('img');
                        if (txt) return txt;
                        if (hasImg) return '[imagem]';
                        return null;
                    })
                    .filter(Boolean)
            """
            )
            print(f"[DEBUG] Mensagens do cliente encontradas: {len(msgs)}")
            return msgs[-depth:]
        except Exception as e:
            print(f"[DEBUG] erro ao extrair mensagens com evaluate_all: {e}")
            return []

    # ---------- painel lateral (pedido) ----------

    async def read_sidebar_order_info(self, page) -> dict:
        """Extrai status, orderId, título, variação, SKU, nome do comprador e campos rotulados do painel de pedido."""
        info = await page.evaluate(
            """
        () => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

          const panels = Array.from(document.querySelectorAll('div,section,article'));
          let right = panels.find(el => /Buyer payment amount|Payment Time|Variation:|Varia[cç][aã]o:|SKU\\s*:/i.test(el.textContent || ''));
          if (!right) right = document.body;

          let statusNode =
            right.querySelector('[class*="order_item_status_tags"] .el-tag, .el-tag.el-tag--warning, .el-tag--success, .el-tag--info, .el-tag') ||
            Array.from(right.querySelectorAll('span')).find(s => {
              const t = norm(s.textContent || '');
              return t && t.length <= 32 && /shipped|enviado|to ship|a caminho|entregue|ready to ship|to return|returned|cancelado|canceled/i.test(t);
            }) || null;
          const status = norm(statusNode && statusNode.textContent) || '';

          const allText = norm(right.textContent || '');
          let orderId = '';
          const hashId = allText.match(/#([A-Z0-9]{8,})\\b/);
          const plainId = allText.match(/\\b[0-9A-Z]{10,}\\b/);
          if (hashId && hashId[1]) orderId = hashId[1];
          else if (plainId) orderId = plainId[0];

          const candidates = Array.from(right.querySelectorAll('div,section,article'));
          const scored = candidates.map(el => {
            const t = el.textContent || '';
            const score =
              (/SKU\\s*:/i.test(t) ? 1 : 0) +
              (/(Variation|Varia[cç][aã]o)\\s*:/i.test(t) ? 1 : 0) +
              (/Buyer payment amount/i.test(t) ? 1 : 0) +
              (/Payment Time/i.test(t) ? 1 : 0) +
              (el.querySelector('.product_name, .order_item, .order_title, .dk_msg_order') ? 1 : 0);
            return { el, score, len: t.length };
          }).filter(x => x.score > 0).sort((a,b)=> b.score - a.score || b.len - a.len);
          const card = (scored[0] && scored[0].el) || right;

          let titleNode =
            card.querySelector('.product_name, [class*="product_name"], .line_clamp_2, a[title]') ||
            card.querySelector('a, [class*="title"], [class*="products_item"]') ||
            card;
          let title = '';
          if (titleNode) {
            const lines = norm(titleNode.textContent).split('\\n').map(norm).filter(Boolean);
            title = lines[0] || '';
          }

          const cardText = card.textContent || '';
          const vMatch = cardText.match(/(?:Variation|Varia[cç][aã]o)\\s*:\\s*(.+)/i);
          const variation = norm((vMatch && vMatch[1] || '').split('\\n')[0]);

          const sMatch = cardText.match(/\\bSKU\\s*:\\s*([A-Za-z0-9\\-\\._]+)/i);
          const sku = norm((sMatch && sMatch[1]) || '');

          const fields = {};
          (right.querySelectorAll('*') || []).forEach(el => {
            const t = norm(el.textContent);
            const m = t.match(/^([^:]{3,}):\\s*(.+)$/);
            if (m) {
              const key = norm(m[1]);
              const val = norm(m[2]);
              if (key && val && key.length <= 64) fields[key] = val;
            }
          });

          return { status, orderId, title, variation, sku, fields };
        }
        """
        )
        buyer_name = await DuokeBot._text_or_empty(
            page.locator(SEL.get("buyer_name", ""))
        )
        info["buyer_name"] = buyer_name
        return info

    # ---------- sistema de tags ----------

    async def mark_conversation_with_tag(self, page, complaint_type: str) -> bool:
        """Marca a conversa com uma tag visual baseada no tipo de reclamação detectada"""
        try:
            print(f"[DEBUG] 🏷️ Iniciando marcação com tag para tipo: {complaint_type}")
            
            # 1. BUSCA E CLICA NO ÍCONE DE BANDEIRINHA
            tag_icon_selectors = [
                'i[data-v-29ac6776][class*="icon_mark_1"]',
                'i.icon_mark_1',
                'i[class*="icon_mark"]',
                '[class*="contact_action_icon"] i',
                'span[class*="contact_action_icon"]'
            ]
            
            tag_clicked = False
            for selector in tag_icon_selectors:
                try:
                    tag_icon = page.locator(selector)
                    if await tag_icon.count() > 0:
                        await tag_icon.first.click()
                        tag_clicked = True
                        print(f"[DEBUG] Ícone de tag clicado com seletor: {selector}")
                        break
                except Exception as e:
                    print(f"[DEBUG] Falha com seletor {selector}: {e}")
                    continue
            
            if not tag_clicked:
                print("[DEBUG] ❌ Não foi possível clicar no ícone de tag")
                return False
            
            # Aguarda o modal abrir
            await page.wait_for_timeout(1500)
            
            # 2. MAPEIA TIPOS PARA ETIQUETAS DISPONÍVEIS
            tag_mapping = {
                'falta_peca': [
                    'FALTA DE PEÇA', 'FALTA DE PECA', 'FALTA PEÇA', 'FALTA PECA'
                ],
                'quebra': [
                    'QUEBRAS/DEFEITOS', 'QUEBRAS DEFEITOS', 'QUEBRA', 'DEFEITO', 
                    'OUTROS PROBLEMAS'
                ],
                'outro': ['OUTROS PROBLEMAS']
            }
            
            tag_options = tag_mapping.get(complaint_type, ['OUTROS PROBLEMAS'])
            print(f"[DEBUG] Tentando tags: {tag_options}")
            
            # 3. TENTA SELECIONAR UMA DAS ETIQUETAS
            tag_selected = False
            for tag_text in tag_options:
                try:
                    # Diferentes abordagens para encontrar a etiqueta
                    selectors_to_try = [
                        f'span:text("{tag_text}")',
                        f'span:has-text("{tag_text}")',
                        f'[class*="label_item_name"]:has-text("{tag_text}")',
                        f'div:has-text("{tag_text}")',
                        f'*:has-text("{tag_text}"):visible'
                    ]
                    
                    for sel in selectors_to_try:
                        tag_element = page.locator(sel)
                        if await tag_element.count() > 0:
                            await tag_element.first.click()
                            tag_selected = True
                            print(f"[DEBUG] ✅ Tag selecionada: {tag_text} (seletor: {sel})")
                            break
                    
                    if tag_selected:
                        break
                        
                except Exception as e:
                    print(f"[DEBUG] Erro ao selecionar tag '{tag_text}': {e}")
                    continue
            
            # Fallback: tenta qualquer "OUTROS PROBLEMAS" se não selecionou nada
            if not tag_selected:
                try:
                    fallback_selectors = [
                        'span:has-text("OUTROS PROBLEMAS")',
                        'span:has-text("OUTROS")',
                        '[class*="label_item"]:first-child'
                    ]
                    
                    for sel in fallback_selectors:
                        fallback_element = page.locator(sel)
                        if await fallback_element.count() > 0:
                            await fallback_element.first.click()
                            tag_selected = True
                            print(f"[DEBUG] ✅ Tag fallback selecionada com: {sel}")
                            break
                except Exception as e:
                    print(f"[DEBUG] Erro no fallback: {e}")
            
            if not tag_selected:
                print("[DEBUG] ❌ Nenhuma tag foi selecionada")
                return False
            
            print("[DEBUG] ⏱️ Aguardando modal de confirmação aparecer...")
            await page.wait_for_timeout(1200)  # Mais tempo para modal renderizar
            
            # 4. CLICA EM "CONFIRM" PARA APLICAR A TAG
            print("[DEBUG] 🎯 Procurando botão Confirm...")
            
            # Estratégias múltiplas para encontrar o botão Confirm
            confirm_strategies = [
                # Estratégia 1: Seletores específicos baseados no elemento real
                {
                    'name': 'Seletores específicos do Duoke',
                    'selectors': [
                        'button[data-v-c0d8ee92][class*="el-button--primary"] span:text("Confirm")',
                        'button[data-v-c0d8ee92][class*="el-button--primary"]',
                        'button[fdprocessedid][class*="el-button--primary"] span:text("Confirm")',
                        'button[fdprocessedid][class*="el-button--primary"]',
                        'button[class*="el-button--primary"] span:text("Confirm")',
                        'button[class*="el-button--primary"]:has-text("Confirm")',
                        'button:has-text("Confirm")',
                        'span:text("Confirm")'
                    ]
                },
                # Estratégia 2: Por posição (último botão visível)
                {
                    'name': 'Botão primário visível',
                    'selectors': [
                        'button:visible:last-child',
                        'button[class*="primary"]:visible',
                        '.el-dialog__footer button:last-child',
                        '.el-message-box__btns button:last-child'
                    ]
                }
            ]
            
            confirm_clicked = False
            
            for strategy in confirm_strategies:
                if confirm_clicked:
                    break
                    
                print(f"[DEBUG] 🔍 Tentando: {strategy['name']}")
                
                for sel in strategy['selectors']:
                    try:
                        confirm_element = page.locator(sel)
                        count = await confirm_element.count()
                        
                        if count > 0:
                            print(f"[DEBUG] 👆 Encontrou {count} elemento(s) com: {sel}")
                            
                            # Tenta clicar no primeiro elemento visível
                            element = confirm_element.first
                            
                            # Verifica se é visível
                            is_visible = await element.is_visible() if await element.count() > 0 else False
                            if is_visible:
                                await element.click()
                                confirm_clicked = True
                                print(f"[DEBUG] ✅ Confirm clicado com sucesso: {sel}")
                                break
                            else:
                                print(f"[DEBUG] ⚠️ Elemento não visível: {sel}")
                                
                    except Exception as e:
                        print(f"[DEBUG] ❌ Erro com seletor {sel}: {e}")
                        continue
            
                # Estratégia 3: Fallback com JavaScript específico para o elemento Duoke
                if not confirm_clicked:
                    print("[DEBUG] 🔧 Tentando fallback JavaScript específico...")
                    try:
                        # JavaScript específico para o botão Confirm do Duoke
                        js_result = await page.evaluate("""
                            () => {
                                // Busca pelo botão específico do Duoke
                                const specificBtn = document.querySelector('button[data-v-c0d8ee92][class*="el-button--primary"]');
                                if (specificBtn && specificBtn.offsetParent) {
                                    specificBtn.click();
                                    return true;
                                }
                                
                                // Fallback: busca por botão com fdprocessedid
                                const processedBtn = document.querySelector('button[fdprocessedid][class*="el-button--primary"]');
                                if (processedBtn && processedBtn.offsetParent) {
                                    processedBtn.click();
                                    return true;
                                }
                                
                                // Fallback: busca por span "Confirm" dentro de botão
                                const confirmSpans = Array.from(document.querySelectorAll('span'));
                                for (const span of confirmSpans) {
                                    if (span.textContent.trim() === 'Confirm') {
                                        const button = span.closest('button');
                                        if (button && button.offsetParent) {
                                            button.click();
                                            return true;
                                        }
                                    }
                                }
                                
                                // Último fallback: qualquer botão com "Confirm"
                                const buttons = Array.from(document.querySelectorAll('button'));
                                const confirmBtn = buttons.find(btn => {
                                    const text = (btn.textContent || '').trim().toLowerCase();
                                    return text === 'confirm' || text === 'confirmar';
                                });
                                if (confirmBtn && confirmBtn.offsetParent) {
                                    confirmBtn.click();
                                    return true;
                                }
                                
                                return false;
                            }
                        """)
                        
                        if js_result:
                            confirm_clicked = True
                            print("[DEBUG] ✅ Confirm clicado via JavaScript específico")
                        else:
                            print("[DEBUG] ❌ JavaScript não encontrou botão Confirm específico")
                            
                    except Exception as e:
                        print(f"[DEBUG] ❌ Erro no fallback JavaScript: {e}")
            
            # Estratégia 4: Tentativa direta com seletor exato
            if not confirm_clicked:
                print("[DEBUG] 🎯 Tentando seletor exato do elemento...")
                try:
                    # Tenta o seletor exato baseado no elemento fornecido
                    exact_selector = 'button[data-v-c0d8ee92][type="button"][class*="el-button--primary"][fdprocessedid]'
                    exact_element = page.locator(exact_selector)
                    
                    if await exact_element.count() > 0:
                        await exact_element.click()
                        confirm_clicked = True
                        print(f"[DEBUG] ✅ Confirm clicado com seletor exato: {exact_selector}")
                    else:
                        print(f"[DEBUG] ❌ Seletor exato não encontrou elemento")
                        
                except Exception as e:
                    print(f"[DEBUG] ❌ Erro com seletor exato: {e}")
            
            # Estratégia 5: Enter como último recurso
            if not confirm_clicked:
                print("[DEBUG] ⌨️ Tentando tecla Enter como último recurso...")
                try:
                    await page.keyboard.press("Enter")
                    confirm_clicked = True
                    print("[DEBUG] ✅ Enter pressionado")
                except Exception as e:
                    print(f"[DEBUG] ❌ Erro ao pressionar Enter: {e}")
            
            if not confirm_clicked:
                print("[DEBUG] ❌ FALHA: Não foi possível confirmar a tag")
                # Tenta fechar o modal mesmo assim
                try:
                    await page.keyboard.press("Escape")
                    print("[DEBUG] 🚫 Modal fechado com Escape")
                except Exception:
                    pass
                return False
            
            # Aguarda o modal fechar e a tag ser aplicada
            print("[DEBUG] ⏱️ Aguardando modal fechar e tag ser aplicada...")
            await page.wait_for_timeout(2000)  # Mais tempo para garantir que a tag seja aplicada
            
            # Verifica se o modal realmente fechou
            try:
                modal_closed = await page.evaluate("""
                    () => {
                        // Verifica se não há mais modais visíveis
                        const modals = document.querySelectorAll('.el-dialog__wrapper, .el-message-box__wrapper, [role="dialog"]');
                        return Array.from(modals).every(modal => 
                            !modal.offsetParent || modal.style.display === 'none'
                        );
                    }
                """)
                
                if modal_closed:
                    print("[DEBUG] ✅ Modal fechado com sucesso")
                else:
                    print("[DEBUG] ⚠️ Modal pode ainda estar aberto")
            except Exception as e:
                print(f"[DEBUG] ❓ Não foi possível verificar se modal fechou: {e}")
            
            print(f"[DEBUG] 🎉 Conversa marcada com sucesso com tag para {complaint_type}")
            return True
            
        except Exception as e:
            print(f"[DEBUG] ❌ Erro geral ao marcar conversa com tag: {e}")
            return False

    # ---------- envio de resposta ----------

    async def send_reply(self, page, text: str):
        candidates = [
            s.strip() for s in SEL.get("input_textarea", "").split(",") if s.strip()
        ]
        box = None

        for sel in candidates:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                if await loc.is_enabled():
                    box = loc
                    break
            except Exception:
                continue

        if not box:
            try:
                box = page.get_by_placeholder(
                    re.compile(
                        r"Type a message here|press Enter to send|Enter to send", re.I
                    )
                ).first
                await box.wait_for(state="visible", timeout=3000)
            except Exception:
                raise RuntimeError(
                    "Campo de mensagem não encontrado (todos candidatos estavam ocultos)."
                )

        await box.click()
        try:
            await box.fill(text)
        except Exception:
            await box.type(text, delay=4)

        await page.keyboard.press("Enter")

        try:
            btn_sel = SEL.get("send_button", "")
            if btn_sel:
                btn = page.locator(btn_sel)
                if await btn.count() > 0:
                    await btn.first.click()
        except Exception:
            pass

    # ---------- ações manuais de login/2FA ----------

    async def close_modal(self, page, retries: int = 3):
        """Fecha modais, tooltips ou anúncios tentando várias abordagens."""
        frames = [page] + list(page.frames)
        wrappers = [
            ".el-message-box__wrapper",
            ".el-dialog__wrapper",
            ".ant-modal-root",
            ".modal",
            "[role='dialog']",
            "[role='alert']",
            "[class*='tooltip']",
            "[class*='announcement']",
        ]

        for _ in range(retries):
            for fr in frames:
                try:
                    method = await self._click_confirm_anywhere(fr)
                except Exception:
                    continue
                if method:
                    try:
                        await fr.locator(",".join(wrappers)).locator(
                            ":visible"
                        ).first.wait_for(state="hidden", timeout=3000)
                    except Exception:
                        await page.wait_for_timeout(300)
                    where = "iframe" if fr is not page else "page"
                    print(f"[DEBUG] close_modal: {method} in {where}")
                    return True

            # Botões de fechar genéricos
            try:
                loc = page.locator(
                    "button[aria-label='close'], .ant-modal-close, .close, [class*='close'] button"
                ).locator(":visible")
                if await loc.count() > 0:
                    await loc.first.click()
                    await page.wait_for_timeout(200)
                    print("[DEBUG] close_modal: generic close button")
                    return True
            except Exception:
                pass

            # Fallback: tecla Escape
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

            # Remoção manual via DOM
            try:
                await page.evaluate(
                    "sels => { for (const sel of sels) { document.querySelectorAll(sel).forEach(el => el.remove()); } }",
                    wrappers,
                )
            except Exception:
                pass

            await page.wait_for_timeout(200)

        print("[DEBUG] close_modal: nenhum modal visível")
        return False

    async def enter_verification_code(self, page, code: str):
        """Digita o código de verificação e confirma."""
        code = (code or "").strip()
        if not code:
            raise RuntimeError("Código vazio.")
        # input
        ipt_sel = SEL.get("verify_code_input") or ""
        try:
            if ipt_sel:
                ipt = page.locator(ipt_sel).first
            else:
                ipt = page.get_by_placeholder(re.compile(r"code|c[oó]digo", re.I)).first
            await ipt.wait_for(state="visible", timeout=8000)
            await ipt.click()
            try:
                await ipt.fill(code)
            except Exception:
                await ipt.type(code, delay=30)
        except Exception as e:
            raise RuntimeError(f"Campo de código não encontrado: {e}")

        # submit
        try:
            sub_sel = SEL.get("verify_submit") or ""
            if sub_sel:
                btn = page.locator(sub_sel).first
            else:
                btn = page.get_by_role(
                    "button", name=re.compile(r"(Verify|Confirm|Enviar|OK)", re.I)
                ).first
            await btn.click(timeout=5000)
        except Exception:
            # fallback: Enter
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(800)
        return True

    # ---------- utilidades ----------

    async def maybe_extract_tracking(self, page) -> Optional[str]:
        try:
            content = await page.content()
        except Exception:
            return None
        m = re.search(r"\b([A-Z]{2}\d{8,}[A-Z0-9]{1,})\b", content or "")
        return m.group(1) if m else None

    # ---------- modos de execução / helpers ----------

    @staticmethod
    async def _text_or_empty(locator):
        if await locator.count():
            t = await locator.first().inner_text()
            return (t or "").strip()
        return ""

    @staticmethod
    async def get_order_bits(page):
        """Lê status/desc/track + produto/variação/SKU do painel direito usando SEL."""
        status_tag = await DuokeBot._text_or_empty(
            page.locator(SEL["order_status_tag"])
        )

        log_status_el = page.locator(SEL["logistics_status"])
        logistics_status = ""
        if await log_status_el.count():
            logistics_status = (await log_status_el.first().get_attribute("title")) or (
                await log_status_el.first().inner_text()
            )
            logistics_status = (logistics_status or "").strip()

        latest_desc = await DuokeBot._text_or_empty(
            page.locator(SEL["latest_logistics_description"])
        )
        tracking = await DuokeBot._text_or_empty(page.locator(SEL["tracking_number"]))
        product = await DuokeBot._text_or_empty(page.locator(SEL["product_title"]))
        variation = await DuokeBot._text_or_empty(
            page.locator(SEL["product_variation"])
        )
        sku = await DuokeBot._text_or_empty(page.locator(SEL["product_sku"]))

        status_consolidado = (
            status_tag or logistics_status or latest_desc or "desconhecido"
        )

        return {
            "status_tag": status_tag,
            "logistics_status": logistics_status,
            "latest_desc": latest_desc,
            "tracking": tracking,
            "product": product,
            "variation": variation,
            "sku": sku,
            "status_consolidado": status_consolidado,
        }

    @staticmethod
    async def get_review_text(page):
        stars = page.locator(SEL["review_stars"])
        if not await stars.count():
            return ""
        try:
            await stars.first().hover()
            popup = page.locator(SEL["review_text"])
            await popup.first().wait_for(state="visible", timeout=9000)
            return (await popup.first().inner_text() or "").strip()
        except Exception:
            try:
                await stars.first().click()
                popup = page.locator(SEL["review_text"])
                await popup.first().wait_for(state="visible", timeout=9000)
                return (await popup.first().inner_text() or "").strip()
            except Exception:
                return ""

    @staticmethod
    def build_history_from_pairs(pairs, max_depth: int = 20):
        """Monta histórico rotulado com mensagens de comprador e vendedor.

        pairs: lista [(role, text)] em ordem cronológica.
        Retorna bloco de texto com as últimas ``max_depth`` mensagens, cada
        uma precedida por ``Comprador:`` ou ``Vendedor:`` para indicar a
        origem. Essas informações são enviadas ao Gemini como contexto da
        conversa.
        """

        recents = pairs[-max_depth:]
        lines: list[str] = []
        for role, text in recents:
            prefix = "Comprador" if role == "buyer" else "Vendedor"
            lines.append(f"{prefix}: {text.strip()}")
        return "\n\n".join(lines)

    async def _cycle(self, page, decide_reply_fn):
        """Executa um ciclo sobre as conversas visíveis."""
        # Se estiver aguardando 2FA, não tenta responder
        if self.awaiting_2fa:
            print("[DEBUG] Aguardando 2FA, ciclo pausado.")
            await asyncio.sleep(1)
            return

        # Garante que conversas cujo último envio foi do vendedor também apareçam
        await self.show_all_conversations(page)

        conv_locator = self.conversations(page)
        await page.wait_for_timeout(300)
        total = await conv_locator.count()
        print(f"[DEBUG] conversas visíveis: {total}")

        max_convs = int(getattr(settings, "max_conversations", 0) or 0)
        if max_convs > 0:
            total = min(total, max_convs)

        for i in range(total):
            await self.pause_event.wait()
            try:
                ok = await self.open_conversation_by_index(page, i)
                if not ok:
                    continue
            except Exception as e:
                print(f"[DEBUG] falha ao abrir conversa {i}: {e}")
                continue

            await self.pause_event.wait()

            # ----- Order info (extração precisa com seletores) -----
            order_info = {}
            try:
                order_info = await extract_order_details_with_selectors(page, SEL)
            except Exception as e:
                print(f"[DEBUG] falha ao extrair detalhes do pedido com seletores: {e}")
                try:
                    order_info = await extract_order_from_dom(page, SEL)
                except Exception as e_dom:
                    print(
                        f"[DEBUG] falha total na extração de dados do pedido: {e_dom}"
                    )

            print("[DEBUG] Order info:", order_info)

            # ----- Mensagens + history (últimas 20 conforme solicitado) -----
            depth = 20  # Fixo em 20 mensagens conforme requisito
            pairs = await self.read_messages_with_roles(page, depth)
            print(f"[DEBUG] conversa {i}: {len(pairs)} msgs (com role)")
            if not pairs:
                continue

            buyer_name = (order_info.get("buyer_name") or "").strip()
            buyer_only = [t for r, t in pairs if r == "buyer"][-20:]  # Últimas 20 mensagens do comprador
            problema = infer_problema(buyer_only)
            etiqueta = determine_label(problema)
            order_info["etiqueta"] = etiqueta
            order_info["problema"] = problema
            if buyer_name:
                stored = get_history(buyer_name)
                if stored:
                    for p in pairs:
                        if p not in stored:
                            stored.append(p)
                    pairs = stored
            if buyer_name:
                append_history(
                    buyer_name, pairs, order_info=order_info, max_depth=20
                )

            wants_parts = bool(buyer_only) and buyer_wants_missing_parts(buyer_only[-1])

            # Se a última mensagem do vendedor foi o texto de "quebra_com_foto"
            # e o cliente respondeu em seguida, apenas registramos a conversa
            # e pulamos sem reprocessar.
            if len(pairs) >= 2:
                last_role, last_txt = pairs[-1]
                prev_role, prev_txt = pairs[-2]
                prev_lower = (prev_txt or "").lower()
                if (
                    last_role == "buyer"
                    and prev_role == "seller"
                    and "podemos resolver de 3 formas" in prev_lower
                    and "reembolso parcial" in prev_lower
                    and "devolu" in prev_lower
                    and "envio de nova peça" in prev_lower
                ):
                    try:
                        log_case(order_info, buyer_only)
                    except Exception as e:
                        print(f"[DEBUG] falha ao registrar atendimento: {e}")
                    print(
                        "[DEBUG] conversa registrada (cliente respondeu à mensagem de quebra_com_foto)"
                    )
                    continue

            # Últimas mensagens de comprador e vendedor para contexto
            history_block = self.build_history_from_pairs(pairs, max_depth=20)
            order_info["history_block"] = history_block

            # ----- dedupe por conversa e rate-limit -----
            conv_key = order_info.get("orderId") or "|".join(buyer_only[-2:]) or str(i)
            now = time.time()
            last = self.last_replied_at.get(conv_key)
            if last and now - last < 180:
                print(
                    f"[DEBUG] pulando conversa já respondida recentemente: {conv_key}"
                )
                continue

            if problema in {"reembolso parcial", "enviar peça faltante", "enviar nova peça"} or wants_parts:
                try:
                    log_case(order_info, buyer_only)
                except Exception as e:
                    print(f"[DEBUG] falha ao registrar atendimento: {e}")
                try:
                    log_label(order_info, buyer_only)
                except Exception as e:
                    print(f"[DEBUG] falha ao registrar pedido: {e}")
                print("[DEBUG] conversa registrada (pendência manual)")
                continue

            # ----- ANÁLISE RÁPIDA: detecta se há reclamações específicas -----
            flagged = False
            analysis_result = ""
            try:
                # Usa o novo classificador que detecta reclamações
                flagged, analysis_result = complaint_decide_reply(pairs, buyer_only, order_info)
                print(f"[DEBUG] 🔍 Análise: {analysis_result}")
                
                # ✅ SE NÃO HÁ RECLAMAÇÃO: PULA RAPIDAMENTE PARA PRÓXIMA CONVERSA
                if not flagged and not any(keyword in analysis_result.lower() for keyword in ['reclamação', 'marcado']):
                    print("[DEBUG] ⚡ Conversa normal - PULANDO para próxima (sem problemas detectados)")
                    continue
                
                # 🚨 SE HÁ RECLAMAÇÃO DETECTADA: Processa marcação e registro
                print(f"[DEBUG] 🚨 RECLAMAÇÃO DETECTADA - Iniciando processamento completo...")
                
                # 1. MARCA VISUALMENTE A CONVERSA COM TAG
                try:
                    # Detecta o tipo de reclamação para escolher a tag correta
                    complaint_type = 'outro'  # padrão
                    
                    # Analisa o resultado para extrair o tipo principal
                    analysis_lower = analysis_result.lower()
                    if 'tipo principal:' in analysis_lower:
                        # Extrai o tipo principal da resposta do classifier
                        import re
                        match = re.search(r'tipo principal:\s*(\w+)', analysis_lower)
                        if match:
                            detected_type = match.group(1)
                            complaint_type = detected_type
                    else:
                        # Fallback para detecção baseada em palavras-chave
                        if any(keyword in analysis_lower for keyword in ['falta de peça', 'falta de peca', 'missing']):
                            complaint_type = 'falta_peca'
                        elif any(keyword in analysis_lower for keyword in ['quebra', 'defeito', 'broken']):
                            complaint_type = 'quebra'
                    
                    # Marca a conversa com a tag visual apropriada
                    print(f"[DEBUG] 🎯 Iniciando marcação visual para tipo: {complaint_type}")
                    tag_success = await self.mark_conversation_with_tag(page, complaint_type)
                    
                    if tag_success:
                        print(f"[DEBUG] 🎉 ✅ Conversa marcada visualmente com tag '{complaint_type}' COM SUCESSO!")
                        # Aguarda mais um tempo para garantir que a tag foi aplicada completamente
                        print("[DEBUG] ⏱️ Aguardando estabilização após marcação...")
                        await page.wait_for_timeout(1500)
                    else:
                        print(f"[DEBUG] ❌ ⚠️ FALHA ao marcar conversa visualmente com tag '{complaint_type}' - continuando sem marcação")
                        
                except Exception as e:
                    print(f"[DEBUG] ❌ Erro ao marcar visualmente: {e}")
                
                # 2. REGISTRA NOS SISTEMAS DE DADOS
                try:
                    log_case(order_info, buyer_only)
                    log_label(order_info, buyer_only)
                    print("[DEBUG] ✅ Conversa com reclamação registrada nos sistemas")
                except Exception as e:
                    print(f"[DEBUG] falha ao registrar no sistema antigo: {e}")
                    
            except Exception as e:
                print(f"[DEBUG] erro no classificador de reclamações: {e}")
                analysis_result = f"Erro na análise: {e}"

            # SISTEMA TRANSFORMADO: detecta reclamações, marca visualmente e salva para revisão manual
            # NÃO ENVIA MAIS RESPOSTAS AUTOMÁTICAS

    async def run_once(self, decide_reply_fn):
        """Modo pontual (mantido por compat)."""
        async with async_playwright() as p:
            ctx = await self._new_context(p)
            page = await self._get_page(ctx)
            await self.ensure_login(page)
            await self._cycle(page, decide_reply_fn)
            print("[DEBUG] Execução concluída. Mantendo o navegador aberto por ~60s...")
            await asyncio.sleep(60)
            try:
                await ctx.close()
            finally:
                self.current_page = None

    async def run_forever(self, decide_reply_fn, idle_seconds: float = 3.0):
        """
        Loop infinito, com auto-recuperação.
        Use este método a partir do app_ui (start/stop via task).
        """
        async with async_playwright() as p:
            while True:
                ctx = None
                try:
                    ctx = await self._new_context(p)
                    page = await self._get_page(ctx)
                    await self.ensure_login(page)

                    while True:
                        await self._cycle(page, decide_reply_fn)
                        await asyncio.sleep(idle_seconds)

                except asyncio.CancelledError:
                    try:
                        if ctx:
                            await ctx.close()
                    finally:
                        self.current_page = None
                    break
                except PwError as e:
                    print(f"[ERROR] Playwright: {e}. Reiniciando em 2s...")
                    await asyncio.sleep(2)
                    continue
                except Exception as e:
                    print(f"[ERROR] run_forever: {e}. Tentando novamente em 2s...")
                    await asyncio.sleep(2)
                    continue
                finally:
                    try:
                        if ctx:
                            await ctx.close()
                    except Exception:
                        pass
                    self.current_page = None
