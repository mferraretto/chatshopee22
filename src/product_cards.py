import asyncio
import json
import random
import re
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import Page, TimeoutError as PWTimeoutError

from .gemini_client import send_product_payload

PROCESSED_ATTR = "data-product-processed"
API_URL = "https://shopee.com.br/api/v2/item/get?itemid={itemid}&shopid={shopid}"
CDN_PREFIX = "https://cf.shopee.com.br/file/"


def _norm_price(val: Any) -> float | None:
    try:
        if val is None:
            return None
        return float(val) / 100000 if isinstance(val, (int, float)) else None
    except Exception:
        return None


def _image_url(hash_: str | None) -> str | None:
    if not hash_:
        return None
    if hash_.startswith("http"):
        return hash_
    return f"{CDN_PREFIX}{hash_}"


async def detect_product_cards(page: Page, pairs: List[tuple[str, str]]) -> None:
    """Detecta e processa cards de produto enviados pelo comprador."""
    cards = page.locator(".msg_cont .msg_product.msg_card")
    count = await cards.count()
    for i in range(count):
        card = cards.nth(i)
        if await card.get_attribute(PROCESSED_ATTR):
            continue
        is_buyer = await card.evaluate("el => !!el.closest('.lt') && !el.closest('.rt')")
        if not is_buyer:
            continue
        await card.set_attribute(PROCESSED_ATTR, "1")
        try:
            await _process_card(page, card, pairs)
        except Exception as e:
            print(f"[DEBUG] falha processar card: {e}")
        await page.wait_for_timeout(int(random.uniform(200, 500)))


async def _process_card(chat_page: Page, card, pairs: List[tuple[str, str]]):
    new_page = await _open_product_page(chat_page, card)
    product = await extract_product_data(new_page)
    await new_page.close()

    contexto = "\n".join(f"{r}: {t}" for r, t in pairs[-8:])
    objetivo = (
        "validar se o produto bate com a dúvida do cliente, sugerir resposta curta e humana, "
        "checar preço/título"
    )
    resp = send_product_payload(product, contexto, objetivo)
    print(f"[DEBUG] Gemini: {resp}")


async def _open_product_page(page: Page, card) -> Page:
    link = None
    try:
        link = await card.locator("a").first.get_attribute("href")
    except Exception:
        pass
    if link:
        new_page = await page.context.new_page()
        await new_page.goto(link)
        return new_page
    try:
        async with page.expect_popup() as pop:
            await card.click()
        new_page = await pop.value
        return new_page
    except PWTimeoutError:
        raise RuntimeError("não foi possível abrir o anúncio")


async def extract_product_data(page: Page) -> Dict[str, Any]:
    url = page.url
    m = re.search(r"--i\.(\d+)\.(\d+)", url)
    shop_id = int(m.group(1)) if m else None
    item_id = int(m.group(2)) if m else None
    produto: Dict[str, Any] = {
        "shopId": shop_id,
        "itemId": item_id,
        "url": url,
    }
    source = None

    if shop_id and item_id:
        try:
            resp = await page.request.get(API_URL.format(itemid=item_id, shopid=shop_id))
            if resp.ok:
                data = await resp.json()
                item = data.get("item") or {}
                produto.update(_parse_api_item(item))
                source = "api"
        except Exception:
            pass

    if not produto.get("name"):
        ld = await _parse_ld_json(page)
        if ld:
            produto.update(ld)
            source = source or "ld+json"

    if not produto.get("name"):
        dom = await _parse_dom(page)
        produto.update(dom)
        source = source or "dom"

    produto["timestamp"] = datetime.utcnow().isoformat()
    produto["source"] = source
    return produto


def _parse_api_item(item: Dict[str, Any]) -> Dict[str, Any]:
    rating_info = item.get("item_rating") or {}
    return {
        "name": item.get("name"),
        "description": (item.get("description") or "")[:500],
        "brand": item.get("brand"),
        "sku": item.get("item_sku"),
        "priceMin": _norm_price(item.get("price_min")),
        "priceMax": _norm_price(item.get("price_max")),
        "priceBeforeDiscount": _norm_price(item.get("price_before_discount")),
        "stock": item.get("stock"),
        "historicalSold": item.get("historical_sold"),
        "soldRecent": item.get("sold"),
        "rating": rating_info.get("rating_average"),
        "ratingCount": rating_info.get("rating_count", [0])[-1]
        if isinstance(rating_info.get("rating_count"), list)
        else rating_info.get("rating_count"),
        "images": [
            img for img in ([_image_url(i) for i in item.get("images", [])] if item.get("images") else [])
            if img
        ],
        "variations": [
            {
                "name": m.get("name"),
                "sku": m.get("sku"),
                "price": _norm_price(m.get("price")),
                "stock": m.get("stock"),
                "image": _image_url(m.get("image")),
            }
            for m in item.get("models", [])
        ]
        if item.get("has_model")
        else [],
        "attributes": [
            {"name": a.get("name"), "value": a.get("value")} for a in item.get("attributes", [])
        ],
    }


async def _parse_ld_json(page: Page) -> Dict[str, Any]:
    try:
        scripts = page.locator('script[type="application/ld+json"]')
        count = await scripts.count()
        for i in range(count):
            raw = await scripts.nth(i).inner_text()
            data = json.loads(raw)
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if isinstance(obj, dict) and obj.get("@type") == "Product":
                    return {
                        "name": obj.get("name"),
                        "description": (obj.get("description") or "")[:500],
                        "brand": obj.get("brand", {}).get("name")
                        if isinstance(obj.get("brand"), dict)
                        else obj.get("brand"),
                        "images": obj.get("image")
                        if isinstance(obj.get("image"), list)
                        else [obj.get("image")]
                        if obj.get("image")
                        else [],
                    }
    except Exception:
        pass
    return {}


async def _parse_dom(page: Page) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        out["name"] = await page.locator("h1").first.inner_text()
    except Exception:
        pass
    try:
        price_txt = await page.locator("[class*='price']").first.inner_text()
        price_txt = price_txt.replace("R$", "").replace(".", "").replace(",", ".").strip()
        out["priceMin"] = float(price_txt)
    except Exception:
        pass
    return out
