import json
import random
import re
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import Page, TimeoutError as PWTimeoutError

from .gemini_client import send_product_payload

PROCESSED_ATTR = "data-product-processed"


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

    ld = await _parse_ld_json(page)
    if ld:
        produto.update(ld)
        source = source or "ld+json"

    meta = await _parse_meta(page)
    if meta:
        for k, v in meta.items():
            produto.setdefault(k, v)
        source = source or "metatags"

    dom = await _parse_dom(page)
    if dom:
        for k, v in dom.items():
            produto.setdefault(k, v)
        source = source or "dom"

    if produto.get("images"):
        produto["images"] = list(dict.fromkeys(filter(None, produto["images"])))
    if "variations" not in produto:
        produto["variations"] = []

    produto["timestamp"] = datetime.utcnow().isoformat()
    produto["source"] = source
    return produto


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
                    offers = obj.get("offers") or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    rating_obj = obj.get("aggregateRating") or offers.get("aggregateRating") or {}
                    low = offers.get("lowPrice") or offers.get("price")
                    high = offers.get("highPrice") or offers.get("price")
                    images = obj.get("image")
                    images = images if isinstance(images, list) else [images] if images else []
                    return {
                        "name": obj.get("name"),
                        "description": (obj.get("description") or "")[:500],
                        "brand": obj.get("brand", {}).get("name")
                        if isinstance(obj.get("brand"), dict)
                        else obj.get("brand"),
                        "sku": obj.get("sku"),
                        "images": images,
                        "priceMin": float(low) if low else None,
                        "priceMax": float(high) if high else None,
                        "rating": float(rating_obj.get("ratingValue"))
                        if rating_obj.get("ratingValue")
                        else None,
                        "ratingCount": int(
                            rating_obj.get("reviewCount") or rating_obj.get("ratingCount")
                        )
                        if rating_obj.get("reviewCount") or rating_obj.get("ratingCount")
                        else None,
                    }
    except Exception:
        pass
    return {}


async def _parse_meta(page: Page) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        title = await page.locator('meta[property="og:title"]').get_attribute("content")
        if title:
            out["name"] = title
    except Exception:
        pass
    try:
        desc = await page.locator('meta[name="twitter:description"]').get_attribute("content")
        if not desc:
            desc = await page.locator('meta[property="og:description"]').get_attribute("content")
        if desc:
            out["description"] = desc
    except Exception:
        pass
    try:
        img = await page.locator('meta[property="og:image"]').get_attribute("content")
        if img:
            out["images"] = [img]
    except Exception:
        pass
    price = None
    try:
        price = await page.locator('meta[property="og:price:amount"]').get_attribute("content")
    except Exception:
        pass
    if not price:
        try:
            price = await page.locator('meta[property="product:price:amount"]').get_attribute("content")
        except Exception:
            pass
    if price:
        try:
            val = float(price.replace(",", "."))
            out["priceMin"] = val
            out["priceMax"] = val
        except Exception:
            pass
    return out


async def _parse_dom(page: Page) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        name = await page.locator("h1, .attM6y, .VCNVHn").first.text_content()
        if name:
            out["name"] = name.strip()
    except Exception:
        pass
    try:
        price_block = await page.locator(".pro-price, .V5H2d6").first.text_content()
        if price_block:
            prices = re.findall(r"\d+[.,]\d+", price_block.replace("\n", " "))
            nums = [float(p.replace(".", "").replace(",", ".")) for p in prices]
            if nums:
                out["priceMin"] = min(nums)
                out["priceMax"] = max(nums)
    except Exception:
        pass
    try:
        variations = await page.locator(
            ".product-variation, .product-variation-item"
        ).all_inner_texts()
        out["variations"] = [v.strip() for v in variations if v.strip()]
    except Exception:
        pass
    return out
