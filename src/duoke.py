# src/duoke.py
import inspect
import asyncio
import os
import re
import json
from pathlib import Path
from typing import Optional, Tuple
import time

from playwright.async_api import (
    async_playwright,
    Error as PwError,
    TimeoutError as PWTimeoutError,
)
from .config import settings
from .classifier import RESP_FALLBACK_CURTO
from .cases import (
    append_row as log_case,
    save_conversation_snapshot,
    mark_conversation_skipped,
)

# Carrega seletores configuráveis
SEL = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "selectors.json").read_text(
        encoding="utf-8"
    )
)


def dbg(tag, data):
    try:
        print(f"[DEBUG] {tag}: {data}")
    except Exception as e:
        print(f"[DEBUG] {tag}: <unprintable> {e}")

RESPONDER_MESMO_SE_ULTIMA_FOR_SELLER = True
PULAR_QUANDO_JA_HOUVE_OFERTA_SEM_RESPOSTA_DO_COMPRADOR = True

THREAD_LIST_SEL = SEL.get(
    "chat_list_root",
    "div.session_list, ul.session_list, div.chat_list",
)

_root_parts = [s.strip() for s in THREAD_LIST_SEL.split(",")]
_default_rows = []
for r in _root_parts:
    _default_rows.extend(
        [f"{r} > ul > li", f"{r} li.session_item", f"{r} li[role='listitem']"]
    )

THREAD_ROW_SEL = SEL.get("chat_list_item", ", ".join(_default_rows))
UNREAD_BADGE_SEL = ".unread, .red_point, .badge"

TS_ONLY_RE = re.compile(r"^\d{2}/\d{2}\s+\d{2}:\d{2}$")
NOISE = ("FAQ History", "[The referenced message cannot be found]")
ALL_ITEMS_SEL = "ul.message_main li[class*='lt'], ul.message_main li[class*='rt']"

MESSAGES_CONTAINER_SEL = SEL.get(
    "messages_container",
    "ul.message_main, ul.message_main.watermark_shopee",
)
STATUS_BADGE_SEL = SEL.get(
    "status_badge",
    "div.order_item_status .el-tag",
)

WANTS_PARTS_RE = re.compile(
    r"(quero|prefiro|pode|manda|mandar|envia|enviar|me envia|me mandar).{0,25}(peça|peças|pecas|as peças|as pecas|a peça|a peca)",
    re.I,
)


async def get_current_conversation_id(page) -> str:
    for sel in [
        "div.order_header :text('Order ID')",
        "div.order_item_products_item_info_title_name_url .product_url",
        "div.chat_header .account_name",
    ]:
        try:
            t = await page.text_content(sel)
            if t and t.strip():
                return t.strip()
        except Exception:
            pass
    return ""


async def safe_open_thread_by_index(page, i: int) -> bool:
    rows = page.locator(THREAD_ROW_SEL)
    row = rows.nth(i)

    await row.scroll_into_view_if_needed()
    await row.wait_for(state="visible", timeout=8000)

    try:
        await row.locator("a[href]").evaluate_all(
            "(as)=>as.forEach(a=>{a.removeAttribute('href');a.removeAttribute('target');})"
        )
    except Exception:
        pass

    url_before = page.url
    conv_before = await get_current_conversation_id(page)

    try:
        await row.click(no_wait_after=True)
    except Exception as e:
        print(f"[DEBUG] safe_open_thread_by_index click error: {e}")
        return False

    for _ in range(20):
        await page.wait_for_timeout(150)
        conv_after = await get_current_conversation_id(page)
        if conv_after and conv_after != conv_before and page.url == url_before:
            return True

    if page.url != url_before:
        try:
            await page.go_back()
            await page.wait_for_timeout(300)
        except Exception:
            pass
    print("[DEBUG] conversa não trocou ao clicar no índice", i)
    return False


async def iterate_threads(page, max_threads=300):
    await page.wait_for_selector(THREAD_ROW_SEL, timeout=15000)
    seen_ids: set[str] = set()
    idx = 0
    rows = page.locator(THREAD_ROW_SEL)
    while idx < max_threads:
        count = await rows.count()
        if idx >= count:
            try:
                await page.locator(THREAD_LIST_SEL).first.evaluate(
                    "el => el.scrollTop = el.scrollHeight"
                )
            except Exception:
                await page.eval_on_selector(
                    THREAD_LIST_SEL, "el => el.scrollTop = el.scrollHeight"
                )
            await page.wait_for_timeout(500)
            count = await rows.count()
            if idx >= count:
                break
        ok = await safe_open_thread_by_index(page, idx)
        if not ok:
            idx += 1
            continue
        conv_id = await get_current_conversation_id(page)
        if conv_id in seen_ids:
            idx += 1
            continue
        seen_ids.add(conv_id)
        print("[DEBUG] >>> Conversa aberta:", conv_id)
        yield conv_id
        idx += 1


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


def _clean(t: str) -> str:
    t = (t or "").replace("\u200b", "").strip()
    return re.sub(r"\s+", " ", t)


def _parse_money_br(txt: str) -> float:
    txt = _clean(txt)
    if not txt:
        return 0.0
    num = re.sub(r"[^0-9,]", "", txt)
    num = num.replace(".", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return 0.0


def _parse_qty(txt: str) -> int:
    txt = _clean(txt)
    m = re.search(r"(\d+)", txt)
    return int(m.group(1)) if m else 0


async def _all_texts(page, selector: str) -> list[str]:
    if not selector:
        return []
    try:
        texts = await page.eval_on_selector_all(
            selector, "nodes => nodes.map(n => n.textContent || '').filter(Boolean)"
        )
        if texts:
            return texts
    except Exception as e:
        dbg("eval_on_selector_all.error", f"{selector} -> {e}")

    try:
        nodes = await page.query_selector_all(selector)
        out = []
        for n in nodes:
            t = await n.text_content()
            if t:
                out.append(t)
        return out
    except Exception as e:
        dbg("query_selector_all.error", f"{selector} -> {e}")
        return []



# ----- Novas rotinas de leitura de mensagens -----

async def get_chat_frame(page):
    if await page.locator(MESSAGES_CONTAINER_SEL).count():
        return page
    for f in page.frames:
        try:
            if await f.locator(MESSAGES_CONTAINER_SEL).count():
                return f
        except Exception:
            pass
    return page


async def ensure_messages_rendered(frame):
    try:
        await frame.wait_for_selector(MESSAGES_CONTAINER_SEL, timeout=12000)
    except Exception:
        return
    await frame.eval_on_selector(MESSAGES_CONTAINER_SEL, "el => el.scrollTop = el.scrollHeight")
    await frame.wait_for_timeout(250)


async def _bubble_text_from_li(li) -> str:
    for sel in (".msg_cont .msg_text .text_cont", ".text_cont", ".quote_content_wrap_new"):
        node = li.locator(sel).first
        if await node.count() > 0:
            t = _clean(await node.text_content() or "")
            if t and not TS_ONLY_RE.match(t) and not any(n.lower() in t.lower() for n in NOISE):
                return t
    t = _clean(await li.text_content() or "")
    t = re.sub(r"\b\d{2}/\d{2}\s+\d{2}:\d{2}\b", "", t).strip()
    if t and not any(n.lower() in t.lower() for n in NOISE):
        return t
    return ""


async def get_timeline(page, limit=120) -> list[dict]:
    f = await get_chat_frame(page)
    await ensure_messages_rendered(f)
    loc = f.locator(ALL_ITEMS_SEL)
    n = await loc.count()
    out = []
    for i in range(max(0, n - limit), n):
        li = loc.nth(i)
        klass = (await li.get_attribute("class")) or ""
        role = "buyer" if "lt" in klass else "seller"
        try:
            text = await _bubble_text_from_li(li)
        except Exception as e:
            print("[ERROR] bubble_extract:", e)
            text = ""
        if text:
            out.append({"role": role, "text": text})
    dbg("timeline_last", out[-6:])
    return out


async def get_last_buyer_texts(page, limit=20) -> list[str]:
    tl = await get_timeline(page, 200)
    buyer = [m["text"] for m in tl if m["role"] == "buyer"]
    dbg("buyer_last", buyer[-limit:])
    return buyer[-limit:]


async def get_last_seller_texts(page, limit=30) -> list[str]:
    tl = await get_timeline(page, 200)
    seller = [m["text"] for m in tl if m["role"] == "seller"]
    dbg("seller_last", seller[-limit:])
    return seller[-limit:]


OFFER_RE = re.compile(
    r"(reembols\w+|estorno|troca(r|remos)?|efetuar\s+a?\s*troca|reenviar|reenvio|enviar\s+(a\s+)?pe(ç|c)a\s+(faltante|que\s+faltou)|devolu(c|ç)ão)",
    re.I,
)


def seller_offered_resolution(msgs: list[str]) -> bool:
    return bool(OFFER_RE.search(" \n ".join(msgs)))


def buyer_after_offer(timeline: list[dict]) -> bool:
    last_offer = None
    for i, m in enumerate(timeline):
        if m["role"] == "seller" and OFFER_RE.search(m["text"]):
            last_offer = i
    if last_offer is None:
        return False
    return any(m["role"] == "buyer" for m in timeline[last_offer + 1 :])


STATUS_RANK = {
    "cancelled": 100,
    "completed": 90,
    "ready to ship": 60,
    "to ship": 50,
    "to pack": 40,
    "to pay": 10,
}


def _norm_status(s: str) -> str:
    s = (s or "").lower()
    if "cancel" in s:
        return "cancelled"
    if "complete" in s or "deliver" in s:
        return "completed"
    if "ready to ship" in s:
        return "ready to ship"
    if "to ship" in s:
        return "to ship"
    if "to pack" in s:
        return "to pack"
    if "to pay" in s:
        return "to pay"
    return "unknown"


async def get_status_consolidated(page) -> str:
    try:
        badges = await _all_texts(page, STATUS_BADGE_SEL)
    except Exception as e:
        dbg("status_badge.error", e)
        badges = []
    mapped = [_norm_status(x) for x in badges]
    status = max(mapped, key=lambda x: STATUS_RANK.get(x, 0)) if mapped else "unknown"
    dbg("status_consolidated", status)
    return status


async def get_order_items(page) -> list[dict]:
    await page.wait_for_selector(SEL.get("order_items_root", ""), timeout=15000)
    items = []
    for h in await page.query_selector_all(SEL.get("order_item", "")):
        title = await h.eval_on_selector(
            SEL.get("item_title", ""),
            "el => el.textContent || ''",
        )
        variation = await h.eval_on_selector(
            SEL.get("item_variation", ""),
            "el => el.getAttribute('title') || el.textContent || ''",
        )
        sku = await h.eval_on_selector(
            SEL.get("item_sku", ""),
            "el => el.getAttribute('title') || el.textContent || ''",
        )
        price_txt = await h.eval_on_selector(
            SEL.get("item_price_block", ""),
            "el => el.textContent || ''",
        )
        qty_txt = await h.eval_on_selector(
            SEL.get("item_qty_block", ""),
            "el => el.textContent || ''",
        )
        items.append(
            {
                "title": _clean(title),
                "variation": _clean(variation),
                "sku": _clean(sku),
                "price": _parse_money_br(price_txt),
                "qty": _parse_qty(qty_txt),
            }
        )
    return items


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
            if SEL.get("messages_container"):
                await page.wait_for_selector(MESSAGES_CONTAINER_SEL, timeout=9000)
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
                await page.wait_for_selector(SEL.get("input_textarea", ""), timeout=8000)
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
                    SEL.get("messages_container", "ul.message_main")
                ).first
                for _ in range(3):
                    await container.evaluate("(el) => { el.scrollTop = 0; }")
                    await page.wait_for_timeout(120)
            except Exception:
                pass

            texts = await items.evaluate_all(
                """
                (els) => els.map(li => {
                    const cls = (li.className || '').toLowerCase();
                    const role = cls.includes('lt') ? 'buyer' : (cls.includes('rt') ? 'seller' : 'system');
                    const txtNode = li.querySelector('div.text_cont, .bubble .text, .record_item .content');
                    const txt = (txtNode?.innerText || '').trim();
                    return txt && role !== 'system' ? [role, txt] : null;
                }).filter(Boolean)
            """
            )
            out = texts[-depth:]
        except Exception:
            pass
        return out

    async def read_messages(self, page, depth: int = 8) -> list[str]:
        """Compat: apenas textos do comprador."""
        try:
            msgs = await get_last_buyer_texts(page, limit=depth)
            print(f"[DEBUG] Mensagens do cliente encontradas: {len(msgs)}")
            return msgs
        except Exception as e:
            print(f"[DEBUG] erro ao extrair mensagens: {e}")
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

    # ---------- envio de resposta ----------

    async def send_reply(self, page, text: str):
        print(f"[DEBUG] Enviando resposta: {text}")
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

    async def apply_label(self, page, label_name: str = "gpt") -> bool:
        """Abre o modal de etiquetas, seleciona a etiqueta `label_name` e confirma."""
        timeout = settings.goto_timeout_ms
        try:
            # 1) Botão correto no cabeçalho do painel direito
            btn_sel = SEL.get(
                "tag_button",
                ".cont_header .contact_action_icon:has(i.icon_mark_1), .contact_action .contact_action_icon:has(i.icon_mark_1)",
            )

            btn = page.locator(btn_sel).locator(":visible").first
            await btn.scroll_into_view_if_needed()
            await btn.wait_for(state="visible", timeout=timeout)
            await btn.click()

            # 2) Modal de etiquetas
            modal_sel = SEL.get(
                "tag_modal",
                ".el-dialog.select_label_dialog, .el-dialog__wrapper:has(.label_item)",
            )
            modal = page.locator(modal_sel).first
            await modal.wait_for(state="visible", timeout=timeout)

            # 3) Clicar na etiqueta pelo texto
            item_sel = SEL.get("tag_item", "span.label_item_name")
            tag_span = modal.locator(item_sel, has_text=label_name).first
            await tag_span.scroll_into_view_if_needed()
            await tag_span.click()

            # 4) Validar que o card ficou ativo
            tag_card = tag_span.locator(
                "xpath=ancestor::div[contains(@class,'label_item')]"
            ).first
            try:
                await tag_card.wait_for(state="attached", timeout=timeout)
                await page.wait_for_function(
                    """(el) => el.classList.contains('active')""",
                    arg=await tag_card.element_handle(),
                    timeout=timeout,
                )
            except PWTimeoutError:
                await tag_span.click()
                await page.wait_for_function(
                    """(el) => el.classList.contains('active')""",
                    arg=await tag_card.element_handle(),
                    timeout=timeout,
                )

            # 5) Confirmar e aguardar fechar
            confirm_btn = modal.locator(
                "button.el-button.el-button--primary",
                has_text="Confirm",
            ).first
            if await confirm_btn.count() == 0:
                confirm_btn = modal.locator(
                    "div.el-dialog__footer button.el-button.el-button--primary"
                ).first

            await confirm_btn.scroll_into_view_if_needed()
            await confirm_btn.click()
            await modal.wait_for(state="hidden", timeout=timeout)

            return True
        except Exception as e:
            print(f"[DEBUG] apply_label falhou: {e}")
            return False

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
            page.locator(SEL.get("order_status_tag", ""))
        )

        log_status_el = page.locator(SEL.get("logistics_status", ""))
        logistics_status = ""
        if await log_status_el.count():
            logistics_status = (await log_status_el.first().get_attribute("title")) or (
                await log_status_el.first().inner_text()
            )
            logistics_status = (logistics_status or "").strip()

        latest_desc = await DuokeBot._text_or_empty(
            page.locator(SEL.get("latest_logistics_description", ""))
        )
        tracking = await DuokeBot._text_or_empty(page.locator(SEL.get("tracking_number", "")))
        product = await DuokeBot._text_or_empty(page.locator(SEL.get("product_title", "")))
        variation = await DuokeBot._text_or_empty(
            page.locator(SEL.get("product_variation", ""))
        )
        sku = await DuokeBot._text_or_empty(page.locator(SEL.get("product_sku", "")))

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
        stars = page.locator(SEL.get("review_stars", ""))
        if not await stars.count():
            return ""
        try:
            await stars.first().hover()
            popup = page.locator(SEL.get("review_text", ""))
            await popup.first().wait_for(state="visible", timeout=9000)
            return (await popup.first().inner_text() or "").strip()
        except Exception:
            try:
                await stars.first().click()
                popup = page.locator(SEL.get("review_text", ""))
                await popup.first().wait_for(state="visible", timeout=9000)
                return (await popup.first().inner_text() or "").strip()
            except Exception:
                return ""

    @staticmethod
    def build_history_from_pairs(pairs, max_buyer=8, max_seller_tail=2):
        """
        pairs: lista [(role, text)] em ordem cronológica.
        Retorna bloco de texto com últimas msgs do comprador + 1-2 do vendedor p/ contexto.
        """
        buyers = [t for r, t in pairs if r == "buyer"][-max_buyer:]
        sellers_tail = [t for r, t in pairs if r == "seller"][-max_seller_tail:]
        want = set(buyers + sellers_tail)
        merged = [t.strip() for _, t in pairs if t in want]
        return "\n\n".join(merged)

    async def _cycle(self, page, decide_reply_fn):
        """Executa um ciclo sobre as conversas visíveis."""
        # Se estiver aguardando 2FA, não tenta responder
        if self.awaiting_2fa:
            print("[DEBUG] Aguardando 2FA, ciclo pausado.")
            await asyncio.sleep(1)
            return

        # Garante que o filtro "precisa responder" seja removido quando queremos
        # responder mesmo que a última mensagem seja do vendedor
        if RESPONDER_MESMO_SE_ULTIMA_FOR_SELLER:
            await self.show_all_conversations(page)
        else:
            await self.apply_needs_reply_filter(page)

        max_convs = int(getattr(settings, "max_conversations", 0) or 0)
        i = -1
        async for _ in iterate_threads(page, max_threads=max_convs or 300):
            i += 1
            await self.pause_event.wait()
            try:
                if SEL.get("messages_container"):
                    await page.wait_for_selector(MESSAGES_CONTAINER_SEL, timeout=9000)
                await page.wait_for_function(
                    """() => {
                    const ul = document.querySelector('ul.message_main');
                    return ul && ul.children && ul.children.length > 0;
                }""",
                    timeout=9000,
                )
            except Exception as e:
                print(f"[DEBUG] falha ao abrir conversa {i}: {e}")
                continue

            await self.pause_event.wait()

            # ----- Order info + status consolidado -----
            try:
                order_info = await self.read_sidebar_order_info(page)
                fields = order_info.get("fields") or {}
                status_tag = (order_info.get("status") or "").strip()

                # Logistics Status (ex.: Delivered)
                logistics_status = ""
                for k, v in fields.items():
                    if k.lower().startswith("logistics status"):
                        logistics_status = (v or "").strip()
                        break

                # Última descrição logística (ex.: Pedido entregue)
                latest_desc = ""
                for k, v in fields.items():
                    if k.lower().startswith("latest logistics description"):
                        latest_desc = (v or "").strip()
                        break

                # (opcional) Tracking, caso venha nos fields
                for k, v in fields.items():
                    if k.lower().startswith("tracking number"):
                        order_info["tracking"] = (v or "").strip()
                        break

                order_info["status_consolidado"] = (
                    status_tag or logistics_status or latest_desc or "desconhecido"
                )
                order_info["logistics_latest_desc"] = latest_desc

                try:
                    order_info["status_consolidado"] = await get_status_consolidated(page)
                except Exception:
                    pass
                try:
                    order_info["items"] = await get_order_items(page)
                except Exception:
                    pass

                print("[DEBUG] Order info:", order_info)
            except Exception as e:
                order_info = {}
                print(f"[DEBUG] falha ao ler order_info: {e}")

            # ----- Mensagens + history -----
            buyer_msgs = await get_last_buyer_texts(page, limit=20)
            seller_msgs = await get_last_seller_texts(page, limit=30)
            timeline = await get_timeline(page, limit=60)

            offered = seller_offered_resolution(seller_msgs)
            buyer_after = buyer_after_offer(timeline)

            dbg("offered_resolution", offered)
            dbg("buyer_after_offer", buyer_after)

            snapshot = {
                "order_id": order_info.get("orderId"),
                "buyer_last_20": buyer_msgs,
                "seller_last_30": seller_msgs,
                "offered_resolution": offered,
            }
            if (
                PULAR_QUANDO_JA_HOUVE_OFERTA_SEM_RESPOSTA_DO_COMPRADOR
                and offered
                and not buyer_after
                and buyer_msgs
            ):
                snapshot["skipped_reason"] = "prior_offer_no_buyer_followup"
                save_conversation_snapshot(snapshot)
                mark_conversation_skipped(
                    order_info.get("orderId"),
                    reason="prior_offer_no_buyer_followup",
                )
                dbg("decision", "SKIP_CONVERSATION")
                continue
            else:
                save_conversation_snapshot(snapshot)

            depth = int(getattr(settings, "history_depth", 8) or 8)
            pairs = await self.read_messages_with_roles(page, depth * 2)
            print(f"[DEBUG] conversa {i}: {len(pairs)} msgs (com role)")
            if not pairs:
                continue

            print("[DEBUG] Mensagens lidas:")
            for role, msg in pairs:
                print(f"- {role}: {msg}")

            buyer_only = [t for r, t in pairs if r == "buyer"][-depth:]

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

            # Últimas N do comprador + 2 do vendedor para contexto
            history_block = self.build_history_from_pairs(
                pairs, max_buyer=depth, max_seller_tail=2
            )
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

            # ----- classificador / decisão -----
            should = False
            reply = ""
            try:
                params = inspect.signature(decide_reply_fn).parameters
                if len(params) >= 3:
                    result = decide_reply_fn(pairs, buyer_only, order_info)
                elif len(params) >= 2:
                    result = decide_reply_fn(buyer_only, order_info)
                else:
                    result = decide_reply_fn(buyer_only)
                if inspect.isawaitable(result):
                    result = await result
                should, reply = result
            except Exception as e:
                print(f"[DEBUG] erro no hook/classificador: {e}")
                should, reply = True, RESP_FALLBACK_CURTO

            print(f"[DEBUG] decide: should={should} | Resposta: {reply}")
            decision_txt = (reply or "").strip().lower()
            if (not should) or decision_txt == "ação: skip (pular)".lower():
                try:
                    log_case(order_info, buyer_only)
                except Exception as e:
                    print(f"[DEBUG] falha ao registrar atendimento: {e}")
                print("[DEBUG] conversa registrada (skip)")
                continue

            if buyer_only and buyer_wants_missing_parts(buyer_only[-1]):
                try:
                    log_case(order_info, buyer_only)
                except Exception as e:
                    print(f"[DEBUG] falha ao registrar atendimento: {e}")
                print("[DEBUG] conversa registrada (quer peças faltantes)")

            if order_info.get("orderId") and "{ORDER_ID}" in reply:
                reply = reply.replace("{ORDER_ID}", order_info["orderId"])

            await self.send_reply(page, reply)
            self.last_replied_at[conv_key] = now
            await page.wait_for_timeout(
                int(getattr(settings, "delay_between_actions", 1.0) * 1000)
            )

            # registra o atendimento no CSV
            try:
                log_case(order_info, buyer_only)
            except Exception as e:
                print(f"[DEBUG] falha ao registrar atendimento: {e}")

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
