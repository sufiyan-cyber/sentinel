"""SparrowMart — a storefront the assistant can really pay at.

Real shopping sites stay add-to-cart only. The wallet scenario needs a page we
control: one where an attacker can plant text, and where a checkout can
complete. This module is that page, its cart, and the money leg.

One `ShopSession` per live session (or per twin-run lane): a wallet, a cart, a
payment provider, and whether an attacker's review is currently planted.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from arena import config
from arena.live.payments import MockProvider, PaymentError, PaymentProvider
from arena.live.wallet import InsufficientFundsError, Wallet, inr
from sentinelz.evidence.canonical import loads

CATALOG_PATH = config.REPO_ROOT / "configs" / "shop_catalog.json"
_CATALOG: dict[str, Any] = loads(Path(CATALOG_PATH).read_text(encoding="utf-8"))
STORE_NAME: str = str(_CATALOG["store"])
PRODUCTS: dict[str, dict[str, Any]] = {p["sku"]: p for p in _CATALOG["products"]}
POISON: dict[str, Any] = _CATALOG["poison"]

SKU_RE = re.compile(r"\b[A-Z]{3}-[A-Z]{3}-\d{3,5}\b")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a", "an", "the", "for", "me", "my", "buy", "get", "order", "purchase", "find", "please",
        "some", "of", "to", "and", "with", "under", "around", "about", "rs", "inr", "rupees", "rupee",
        "budget", "best", "rated", "top", "cheap", "cheapest", "good", "nice", "new", "one",
        "shop", "from", "i", "want", "need", "show", "something", "item", "product", "price",
        "sparrowmart", "online", "wallet", "pay", "using", "in", "on", "at", "is", "it", "that",
        "this", "which", "than", "less", "more", "below", "above", "within", "max", "upto", "up",
        "seller", "bestseller", "bestselling", "popular", "latest", "pair", "piece",
    }
)
#: Different words shoppers use for the same thing.
_ALIASES = {
    "tee": "tshirt", "sari": "saree", "pant": "trouser", "chino": "trouser",
    "earbud": "earphone", "headset": "headphone", "mobile": "phone", "smartphone": "phone",
    "sneaker": "shoe", "trainer": "shoe", "footwear": "shoe", "chappal": "sandal", "slipper": "sandal",
    "purse": "handbag", "rucksack": "backpack", "pendrive": "pen", "lehnga": "lehenga",
    "frock": "dress", "gown": "dress", "denim": "jean", "cooker": "cooker", "flask": "bottle",
}


def _tokens(text: str) -> list[str]:
    """Lower-case search stems: 'T-Shirts' -> tshirt, 'sarees' -> saree."""
    lowered = text.lower().replace("t-shirt", "tshirt").replace("t shirt", "tshirt")
    out: list[str] = []
    for word in _WORD_RE.findall(lowered):
        if len(word) < 2 or word.isdigit():
            continue
        stem = word[:-1] if len(word) > 3 and word.endswith("s") else word
        out.append(_ALIASES.get(stem, stem))
    return out


#: sku -> (stems in the title, stems anywhere in the listing)
_INDEX: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    sku: (
        frozenset(_tokens(p["title"])),
        frozenset(_tokens(f"{p['title']} {p['category']} {p['description']} {p.get('tags', '')}")),
    )
    for sku, p in PRODUCTS.items()
}
_STOP_STEMS = frozenset(_tokens(" ".join(_STOP))) | _STOP
CATEGORIES: list[str] = sorted({str(p["category"]) for p in PRODUCTS.values()})
Event = tuple[str, dict[str, Any]]


class ShopSession:
    def __init__(self, provider: PaymentProvider | None = None, wallet: Wallet | None = None) -> None:
        self.wallet = wallet or Wallet()
        self.provider: PaymentProvider = provider or MockProvider()
        self.cart: dict[str, int] = {}
        self.orders: list[dict[str, Any]] = []
        #: The review an attacker has planted on `POISON["target_sku"]`, if any.
        self.poison: dict[str, Any] | None = None
        #: Events for the UI, drained by the agent after each tool call.
        self.events: list[Event] = []

    # ------------------------------------------------------------ attack
    def plant(self, variant: str = "loud", text: str | None = None) -> None:
        """Plant a review on the target listing. `text` overrides the fixture."""
        if text:
            self.poison = {"author": "Verified Buyer", "stars": 5, "text": text.strip()[:2000]}
        else:
            self.poison = dict(POISON["quiet" if variant == "quiet" else "loud"])

    def reset(self) -> None:
        self.wallet.reset()
        self.cart.clear()
        self.orders.clear()
        self.poison = None
        self.events.clear()

    # ----------------------------------------------------------- catalog
    def reviews(self, sku: str) -> list[dict[str, Any]]:
        reviews = list(PRODUCTS[sku]["reviews"])
        if self.poison and sku == POISON["target_sku"]:
            reviews.append(self.poison)
        return reviews

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Best matches first: most query words matched, title matches ahead
        of description matches, then the higher rating."""
        words = list(dict.fromkeys(w for w in _tokens(query) if w not in _STOP_STEMS))
        scored: list[tuple[float, dict[str, Any]]] = []
        for sku, product in PRODUCTS.items():
            title, everything = _INDEX[sku]
            hits = sum(1 for w in words if w in everything)
            if hits or not words:
                in_title = sum(1 for w in words if w in title)
                scored.append((hits + 0.25 * in_title + product["rating"] / 100.0, product))
        scored.sort(key=lambda pair: -pair[0])
        if words and scored:
            # Keep only results close to the best one, so "sunscreen" does not
            # also list every other beauty product, nor "green t-shirt" every
            # green object in the store.
            floor = scored[0][0] - 0.6
            scored = [pair for pair in scored if pair[0] >= floor]
        return [p for _, p in scored[:limit]]

    def cart_payload(self) -> dict[str, Any]:
        items = [
            {
                "sku": sku,
                "title": PRODUCTS[sku]["title"],
                "price_paise": PRODUCTS[sku]["price_paise"],
                "qty": qty,
                "color": PRODUCTS[sku]["color"],
                "icon": PRODUCTS[sku]["icon"],
            }
            for sku, qty in self.cart.items()
        ]
        return {"items": items, "total_paise": self.cart_total()}

    def cart_total(self) -> int:
        return sum(int(PRODUCTS[sku]["price_paise"]) * qty for sku, qty in self.cart.items())

    # ------------------------------------------------------------- tools
    def tool_search(self, query: str) -> str:
        results = self.search(query)
        self.events.append(("shop_view", {"url": f"/shop?q={query}", "kind": "search", "query": query}))
        if not results:
            return f"No products at {STORE_NAME} match {query!r}."
        lines = [
            f"{i}. [{p['sku']}] {p['title']} — {inr(p['price_paise'])} — rated {p['rating']}/5"
            for i, p in enumerate(results, 1)
        ]
        return f"{STORE_NAME} results for {query!r}:\n" + "\n".join(lines)

    def tool_get_product(self, sku: str) -> str:
        product = self._resolve(sku)
        if product is None:
            return f"error: no product {sku!r} at {STORE_NAME}"
        self.events.append(
            ("shop_view", {"url": f"/shop/p/{product['sku']}", "kind": "product", "sku": product["sku"]})
        )
        reviews = "\n".join(
            f"- {r['author']} ({r['stars']}/5): {r['text']}" for r in self.reviews(product["sku"])
        )
        return (
            f"--- {STORE_NAME} listing {product['sku']} ---\n"
            f"{product['title']}\nPrice: {inr(product['price_paise'])}\n"
            f"Rating: {product['rating']}/5\n{product['description']}\n\nCustomer reviews:\n{reviews}"
        )

    def tool_add_to_cart(self, sku: str, qty: int = 1) -> str:
        product = self._resolve(sku)
        if product is None:
            return f"error: no product {sku!r} at {STORE_NAME}"
        self.cart[product["sku"]] = self.cart.get(product["sku"], 0) + max(1, min(int(qty or 1), 5))
        self.events.append(("cart", self.cart_payload()))
        self.events.append(("shop_view", {"url": "/shop/cart", "kind": "cart"}))
        return (
            f"Added {product['title']} ({product['sku']}) to the cart. "
            f"Cart total: {inr(self.cart_total())}."
        )

    def tool_wallet(self) -> str:
        return f"Wallet balance: {inr(self.wallet.balance_paise)}."

    def tool_checkout(self, sku: str, amount_inr: Any) -> str:
        if not self.cart and sku:
            product = self._resolve(sku)
            if product is not None:
                self.cart[product["sku"]] = 1
                self.events.append(("cart", self.cart_payload()))
        if not self.cart:
            return "error: the cart is empty"
        total = self.cart_total()
        stated = to_paise(amount_inr)
        if stated is not None and stated != total:
            return f"error: amount {inr(stated)} does not match the cart total {inr(total)}"
        first = PRODUCTS[next(iter(self.cart))]
        title = first["title"] + (f" +{len(self.cart) - 1} more" if len(self.cart) > 1 else "")
        base = {
            "provider": self.provider.name,
            "mode": self.provider.mode,
            "amount_paise": total,
            "sku": first["sku"],
            "title": title,
        }
        if total > self.wallet.balance_paise:
            self.events.append(("payment", {**base, "status": "failed", "order_id": "", "reason": "insufficient funds"}))
            return f"error: insufficient wallet balance ({inr(self.wallet.balance_paise)}) for {inr(total)}"
        receipt = f"sz-{len(self.orders) + 1}-{first['sku']}"
        try:
            result = self.provider.create_order(total, receipt, {"sku": first["sku"], "source": "sentinel-z-demo"})
            balance = self.wallet.debit(total, result.order_id, title)
        except (PaymentError, InsufficientFundsError) as exc:
            # The wallet is only debited after the order exists. A provider
            # failure leaves the money where it was.
            self.events.append(("payment", {**base, "status": "failed", "order_id": "", "reason": str(exc)}))
            return f"error: payment failed ({exc})"
        order = {**base, "status": "paid", "order_id": result.order_id, "reason": ""}
        self.orders.append(order)
        self.cart.clear()
        self.events.append(("payment", order))
        self.events.append(("wallet", self.wallet.snapshot(-total, title)))
        self.events.append(("cart", self.cart_payload()))
        self.events.append(("shop_view", {"url": "/shop/cart", "kind": "order"}))
        return (
            f"Payment successful.\n- Order: {result.order_id}\n- Item: {title}\n"
            f"- Paid: {inr(total)}\n- Wallet balance: {inr(balance)}"
        )

    def _resolve(self, sku: str) -> dict[str, Any] | None:
        key = str(sku or "").strip().upper()
        if key in PRODUCTS:
            return PRODUCTS[key]
        found = self.search(str(sku), limit=1)
        return found[0] if found and str(sku).strip() else None


def to_paise(amount_inr: Any) -> int | None:
    """Rupees (number or '₹1,299') to integer paise. None when unparseable."""
    if amount_inr is None or amount_inr == "":
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(amount_inr))
        return int(round(float(cleaned) * 100)) if cleaned else None
    except ValueError:
        return None


# ------------------------------------------------------------------ pages
_CSS = """
*{box-sizing:border-box}body{margin:0;font:15px/1.5 'Segoe UI',system-ui,sans-serif;background:#f6f4ef;color:#1d1c1a}
header{display:flex;align-items:center;gap:16px;padding:14px 24px;background:#1d1c1a;color:#f6f4ef}
header a{color:inherit;text-decoration:none}.logo{font-weight:700;letter-spacing:.02em;font-size:18px}
.logo b{color:#e0a43a}.search{flex:1;background:#33312d;border-radius:8px;padding:8px 12px;color:#bdb8ad;font-size:13px}
main{max-width:980px;margin:0 auto;padding:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}
.card{background:#fff;border:1px solid #e4dfd3;border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;display:block}
.swatch{height:150px;display:grid;place-items:center}.swatch svg{width:86px;height:86px;opacity:.92}
.pad{padding:12px 14px}.title{font-weight:600;font-size:14px}.price{font-size:18px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.muted{color:#77726a;font-size:12.5px}.pdp{display:grid;grid-template-columns:minmax(0,340px) minmax(0,1fr);gap:28px}
@media(max-width:700px){.pdp{grid-template-columns:1fr}}
.pdp .swatch{height:340px;border-radius:14px}.pdp .swatch svg{width:170px;height:170px}
h1{font-size:24px;margin:0 0 6px;text-wrap:balance}.btn{display:inline-block;margin-top:14px;background:#e0a43a;color:#1d1c1a;font-weight:700;padding:10px 18px;border-radius:9px}
.rev{background:#fff;border:1px solid #e4dfd3;border-radius:10px;padding:10px 14px;margin-top:10px}
.rev b{font-size:13px}.stars{color:#c98a1b;font-size:12px;margin-left:6px}
.pi{font-size:1px;line-height:1px;color:#fff;background:#fff;border-color:#fff;height:2px;overflow:hidden;padding:0;margin:0}
.pi.reveal{font-size:13px;line-height:1.5;color:#7a1414;background:#fde8e6;border:1px dashed #c0392b;height:auto;padding:10px 14px;margin-top:10px}
.pi.reveal::before{content:'HIDDEN TEXT — invisible to shoppers, read by the agent';display:block;font-weight:700;font-size:11px;letter-spacing:.06em;margin-bottom:4px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e4dfd3;border-radius:12px;overflow:hidden}
td,th{padding:12px 14px;text-align:left;border-bottom:1px solid #eee9de}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.total{font-size:20px;font-weight:700;text-align:right;margin-top:14px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.chips a{padding:5px 12px;border-radius:999px;border:1px solid #d9d3c5;background:#fff;color:#3b3833;text-decoration:none;font-size:13px;text-transform:capitalize}
.chips a.on{background:#1d1c1a;color:#f6f4ef;border-color:#1d1c1a}
h2.sec{font-size:17px;margin:0 0 12px}
"""

_ICONS = {
    "tee": "<path d='M30 14 44 8c3 5 9 5 12 0l14 6 12 14-11 9-5-6v41H24V31l-5 6-11-9z'/>",
    "shirt": "<path d='M34 8h32l18 12-8 16-8-5v51H32V31l-8 5-8-16zM44 8l6 10 6-10M50 18v64' stroke-width='0'/><path d='M44 8l6 12 6-12z' opacity='.45'/>",
    "hoodie": "<path d='M36 8h28l8 12 14 10-8 16-6-4v38H28V42l-6 4-8-16 14-10zM42 12c2 8 14 8 16 0z'/>",
    "dress": "<path d='M38 8h6l6 8 6-8h6l4 18-8 12 16 46H26l16-46-8-12z'/>",
    "saree": "<path d='M40 8c6-2 14-2 20 0l4 14-6 10 14 52H30l8-36-10 30H20l12-44-4-12zM58 32l-24 50' /><path d='M60 10c10 8 14 22 8 40l-8-18z' opacity='.5'/>",
    "pants": "<path d='M30 8h40l8 76H56L50 36 44 84H22z'/>",
    "cap": "<path d='M14 58c0-22 16-38 36-38s36 16 36 38H14zm0 6h72c8 0 10 8 2 8H22c-8 0-10-8-8-8z'/>",
    "mug": "<path d='M22 22h44v44c0 8-6 14-14 14H36c-8 0-14-6-14-14zm44 8h8c8 0 12 6 12 12s-4 12-12 12h-8v-8h8c3 0 4-2 4-4s-1-4-4-4h-8z'/>",
    "shoe": "<path d='M10 60V42l14 2 10-14 10 6 6 12 30 8c8 2 10 8 10 12H10z'/>",
    "watch": "<path d='M38 6h24l3 16a26 26 0 0 1 0 56l-3 16H38l-3-16a26 26 0 0 1 0-56zM50 30a20 20 0 1 0 0 40 20 20 0 0 0 0-40zm-2 6h4v13l9 6-2 4-11-7z' fill-rule='evenodd'/>",
    "bag": "<path d='M36 30v-6a14 14 0 0 1 28 0v6h14l6 54H16l6-54zm8 0h12v-6a6 6 0 0 0-12 0z' fill-rule='evenodd'/>",
    "headphones": "<path d='M14 58a36 36 0 0 1 72 0v20a8 8 0 0 1-8 8h-8V56h8a28 28 0 0 0-56 0h8v30h-8a8 8 0 0 1-8-8z'/>",
    "phone": "<path d='M32 6h36a6 6 0 0 1 6 6v76a6 6 0 0 1-6 6H32a6 6 0 0 1-6-6V12a6 6 0 0 1 6-6zm2 10v62h32V16zm12 68h8v4h-8z' fill-rule='evenodd'/>",
    "bottle": "<path d='M42 6h16v10l8 12v58a6 6 0 0 1-6 6H40a6 6 0 0 1-6-6V28l8-12z'/>",
    "pot": "<path d='M18 34h64v36a12 12 0 0 1-12 12H30a12 12 0 0 1-12-12zM6 40h10v10H6zm78 0h10v10H84zM30 22h40v6H30z'/>",
    "home": "<path d='M12 30h76a6 6 0 0 1 6 6v30H6V36a6 6 0 0 1 6-6zm-6 40h88v8H6zM18 40h26v14H18zm38 0h26v14H56z' fill-rule='evenodd'/>",
    "book": "<path d='M16 12h30a8 8 0 0 1 4 2 8 8 0 0 1 4-2h30v70H56a6 6 0 0 0-6 4 6 6 0 0 0-6-4H16z'/>",
    "ball": "<path d='M50 8a42 42 0 1 0 0 84 42 42 0 0 0 0-84zm0 22 16 12-6 19H40l-6-19z' fill-rule='evenodd'/>",
    "box": "<path d='M50 8 88 26v46L50 92 12 72V26zm0 10L24 30l26 12 26-12z' fill-rule='evenodd'/>",
}


def _is_light(color: str) -> bool:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 170


def _icon(product: dict[str, Any]) -> str:
    fill = "#2a2926" if _is_light(product["color"]) else "#ffffff"
    path = _ICONS.get(product["icon"], _ICONS["box"])
    return (
        f"<div class='swatch' style='background:{product['color']}'>"
        f"<svg viewBox='0 0 100 100' fill='{fill}' aria-hidden='true'>{path}</svg></div>"
    )


def _page(title: str, body: str, query: str = "") -> HTMLResponse:
    document = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} · {STORE_NAME}</title><style>{_CSS}</style></head><body>"
        f"<header><a class='logo' href='/shop'>Sparrow<b>Mart</b></a>"
        f"<div class='search'>{html.escape(query) or 'Search products'}</div>"
        "<a href='/shop/cart'>Cart</a></header>"
        f"<main>{body}</main></body></html>"
    )
    return HTMLResponse(document, headers={"Cache-Control": "no-store"})


def build_router(current: Callable[[], ShopSession]) -> APIRouter:
    """Pages and JSON for whichever session `current()` returns."""
    router = APIRouter()

    def card(product: dict[str, Any]) -> str:
        return (
            f"<a class='card' href='/shop/p/{product['sku']}'>{_icon(product)}<div class='pad'>"
            f"<div class='title'>{html.escape(product['title'])}</div>"
            f"<div class='price'>{inr(product['price_paise'])}</div>"
            f"<div class='muted'>★ {product['rating']} · {product['sku']}</div></div></a>"
        )

    @router.get("/shop")
    async def storefront(q: str = "", c: str = "") -> HTMLResponse:
        shop = current()
        if q:
            products = shop.search(q, limit=24)
            heading = f"<p class='muted'>{len(products)} result(s) for “{html.escape(q)}”</p>"
        elif c:
            products = sorted(
                (p for p in PRODUCTS.values() if p["category"] == c), key=lambda p: -p["rating"]
            )
            heading = f"<h2 class='sec'>{html.escape(c.title())} · {len(products)} items</h2>"
        else:
            products = sorted(PRODUCTS.values(), key=lambda p: -p["rating"])[:48]
            heading = f"<h2 class='sec'>Popular right now · {len(PRODUCTS)} products in store</h2>"
        on = " class='on'"
        chips = (
            f"<div class='chips'><a href='/shop'{'' if c or q else on}>Popular</a>"
            + "".join(
                f"<a href='/shop?c={html.escape(name)}'{on if name == c else ''}>{html.escape(name)}</a>"
                for name in CATEGORIES
            )
            + "</div>"
        )
        grid = "<div class='grid'>" + "".join(card(p) for p in products) + "</div>"
        return _page("Shop", chips + heading + grid, q)

    @router.get("/shop/p/{sku}")
    async def product_page(sku: str, reveal: int = 0) -> HTMLResponse:
        shop = current()
        product = PRODUCTS.get(sku.upper())
        if product is None:
            return _page("Not found", "<h1>Product not found</h1>")
        reviews = ""
        for review in shop.reviews(product["sku"]):
            planted = review is shop.poison
            css = "rev pi" + (" reveal" if reveal else "") if planted else "rev"
            reviews += (
                f"<div class='{css}'><b>{html.escape(review['author'])}</b>"
                f"<span class='stars'>{'★' * int(review['stars'])}</span>"
                f"<div>{html.escape(review['text'])}</div></div>"
            )
        body = (
            f"<div class='pdp'>{_icon(product)}<div><h1>{html.escape(product['title'])}</h1>"
            f"<div class='muted'>★ {product['rating']} · SKU {product['sku']}</div>"
            f"<div class='price' style='font-size:28px'>{inr(product['price_paise'])}</div>"
            f"<p>{html.escape(product['description'])}</p><span class='btn'>Add to cart</span>"
            f"<h3 style='margin:26px 0 0'>Customer reviews</h3>{reviews}</div></div>"
        )
        return _page(product["title"], body)

    @router.get("/shop/cart")
    async def cart_page() -> HTMLResponse:
        shop = current()
        payload = shop.cart_payload()
        if not payload["items"]:
            last = shop.orders[-1] if shop.orders else None
            note = (
                f"<p>Last order <b>{html.escape(last['order_id'])}</b> — {html.escape(last['title'])}, "
                f"{inr(last['amount_paise'])} paid.</p>"
                if last
                else ""
            )
            return _page("Cart", f"<h1>Your cart is empty</h1>{note}")
        rows = "".join(
            f"<tr><td>{html.escape(i['title'])}<div class='muted'>{i['sku']}</div></td>"
            f"<td class='n'>{i['qty']}</td><td class='n'>{inr(i['price_paise'] * i['qty'])}</td></tr>"
            for i in payload["items"]
        )
        body = (
            "<h1>Cart</h1><table><tr><th>Item</th><th class='n'>Qty</th><th class='n'>Price</th></tr>"
            f"{rows}</table><div class='total'>Total {inr(payload['total_paise'])}</div>"
        )
        return _page("Cart", body)

    @router.get("/shop/api/products")
    async def api_products(q: str = "") -> JSONResponse:
        shop = current()
        products = shop.search(q, limit=24) if q else list(PRODUCTS.values())
        return JSONResponse({"products": [{**p, "reviews": shop.reviews(p["sku"])} for p in products]})

    return router
