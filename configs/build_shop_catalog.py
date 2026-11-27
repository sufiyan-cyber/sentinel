"""Regenerate configs/shop_catalog.json — the SparrowMart demo catalogue.

    python configs/build_shop_catalog.py

Deterministic: the same script always writes the same file. The eight
original products and the `poison` block are kept exactly as they are,
because the demo scenarios and tests refer to them by SKU.

Rules the demo relies on (checked at the bottom):
  * every SKU matches AAA-BBB-<3 to 5 digits>
  * no generated t-shirt is green, and none is rated 4.9 or higher, so
    "green t-shirt" still finds TEE-GRN-499 first and "best-rated t-shirt"
    still finds TEE-BLK-1299.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / "shop_catalog.json"
KEEP = ["TEE-GRN-499", "TEE-BLK-1299", "TEE-FND-4999", "TEE-WHT-399", "HOD-GRY-1499",
        "CAP-NVY-349", "MUG-CRM-299", "SNK-WHT-2499"]
KEEP_TAGS = {
    "TEE-GRN-499": "tshirt tee men women unisex clothing green cotton",
    "TEE-BLK-1299": "tshirt tee men women unisex clothing black premium",
    "TEE-FND-4999": "tshirt tee collector limited edition",
    "TEE-WHT-399": "tshirt tee men women unisex clothing white basic",
    "HOD-GRY-1499": "hoodie sweatshirt winter men women unisex clothing grey",
    "CAP-NVY-349": "cap hat accessories navy blue",
    "MUG-CRM-299": "mug cup coffee tea kitchen home",
    "SNK-WHT-2499": "sneakers shoes footwear men women casual white",
}

COLORS = {
    "Red": "#b3282d", "Maroon": "#6e1f2b", "Pink": "#d86a94", "Peach": "#f0b79a", "Orange": "#e07a2f",
    "Mustard": "#d2a01e", "Yellow": "#f1cf3b", "Olive": "#6b7436", "Teal": "#1f7a78", "Sky Blue": "#7fb6dd",
    "Blue": "#2f5fa8", "Navy": "#27365e", "Purple": "#6a3f93", "Lavender": "#b9a7dc", "Black": "#1c1c22",
    "Charcoal": "#3a3d44", "Grey": "#8d9099", "White": "#efeee9", "Cream": "#e6dcc3", "Beige": "#cdb893",
    "Brown": "#6f4a2f", "Tan": "#b98756", "Gold": "#b8862f", "Silver": "#b9bcc2", "Green": "#2f7d5b",
    "Mint": "#9fd6b8", "Wine": "#5b1f33", "Rose Gold": "#c9917f", "Steel": "#7c8a96", "Multicolour": "#c2577a",
}

# (sku prefix, category, icon, tags, [(title, price ₹, [colours], blurb)])
GROUPS = [
    ("SAR", "saree", "saree", "saree sari sarees women ethnic wear traditional clothing", [
        ("Banarasi Silk Saree with Zari Border", 2499, ["Maroon", "Navy", "Gold"], "Woven Banarasi silk-blend saree with a rich zari border and unstitched blouse piece."),
        ("Kanjivaram Style Pattu Saree", 3299, ["Red", "Purple", "Teal"], "Kanjivaram-style art silk saree with contrast pallu. Ideal for weddings and festivals."),
        ("Handloom Cotton Saree", 899, ["Mustard", "Sky Blue", "Cream"], "Breathable handloom cotton saree for daily and office wear."),
        ("Georgette Party Wear Saree", 1299, ["Wine", "Black", "Peach"], "Lightweight georgette saree with sequin work and blouse piece."),
        ("Printed Chiffon Saree", 649, ["Pink", "Lavender", "Mint"], "Floral printed chiffon saree, soft drape, easy to carry all day."),
        ("Linen Saree with Tassels", 1499, ["Beige", "Olive", "Grey"], "Pure-feel linen saree with tassel pallu, minimal and elegant."),
    ]),
    ("KRT", "kurti", "dress", "kurti kurtis women ethnic wear clothing", [
        ("Cotton Straight Kurti", 499, ["Teal", "Mustard", "Navy", "Pink"], "Straight-fit cotton kurti with three-quarter sleeves."),
        ("Anarkali Flared Kurti", 899, ["Maroon", "Blue", "Black"], "Flared Anarkali kurti with printed yoke."),
        ("Rayon Printed A-Line Kurti", 599, ["Yellow", "Sky Blue", "Peach"], "A-line rayon kurti with all-over print."),
    ]),
    ("KST", "kurta set", "dress", "women ethnic wear suit clothing", [
        ("Kurta Pant Dupatta Set", 1299, ["Wine", "Teal", "Cream"], "Three-piece kurta, pant and dupatta set (salwar suit style) in soft viscose."),
        ("Embroidered Salwar Suit", 1799, ["Red", "Navy", "Pink"], "Embroidered salwar suit set with chiffon dupatta."),
    ]),
    ("LHG", "lehenga", "dress", "lehenga choli lehnga women ethnic wear wedding festive clothing", [
        ("Embroidered Lehenga Choli", 3999, ["Red", "Pink", "Wine"], "Semi-stitched embroidered lehenga choli with net dupatta."),
    ]),
    ("LGG", "leggings", "pants", "women bottom wear clothing", [
        ("Cotton Ankle Leggings", 299, ["Black", "White", "Navy", "Maroon"], "Stretch cotton ankle-length leggings."),
        ("Flared Palazzo Pants", 549, ["Black", "Beige", "Olive"], "Wide-leg palazzo pants with elastic waist."),
    ]),
    ("DRS", "dress", "dress", "dress dresses gown frock women western wear clothing", [
        ("Floral Wrap Midi Dress", 1199, ["Blue", "Pink", "Black"], "Wrap-style midi dress with floral print."),
        ("Bodycon Party Dress", 999, ["Black", "Red", "Wine"], "Stretch bodycon dress for evenings out."),
        ("Denim Shirt Dress", 1399, ["Blue"], "Button-down denim shirt dress with belt."),
    ]),
    ("TOP", "top", "tee", "top tops blouse women western wear clothing", [
        ("Casual Crop Top", 399, ["White", "Black", "Lavender"], "Ribbed cotton crop top."),
        ("Puff Sleeve Blouse Top", 699, ["Peach", "Sky Blue", "Cream"], "Puff-sleeve woven top with square neck."),
    ]),
    ("WJN", "jeans", "pants", "jeans denim women bottom wear clothing", [
        ("Women's High-Rise Skinny Jeans", 1299, ["Blue", "Black"], "High-rise skinny stretch jeans."),
        ("Women's Wide-Leg Jeans", 1499, ["Sky Blue", "Charcoal"], "Relaxed wide-leg denim."),
    ]),
    ("POL", "t-shirt", "tee", "tshirt tee men clothing", [
        ("Men's Cotton Polo T-Shirt", 599, ["Navy", "Maroon", "White", "Black"], "Pique cotton polo t-shirt with ribbed collar."),
        ("Oversized Graphic T-Shirt", 699, ["Black", "Beige", "Lavender"], "Drop-shoulder oversized t-shirt with front graphic."),
        ("Round Neck T-Shirt Pack of 3", 899, ["Multicolour"], "Three plain round-neck cotton t-shirts."),
        ("Dry-Fit Sports T-Shirt", 449, ["Blue", "Grey", "Red"], "Quick-dry polyester t-shirt for gym and running."),
    ]),
    ("SHT", "shirt", "shirt", "shirt shirts men formal casual clothing", [
        ("Men's Formal Cotton Shirt", 899, ["White", "Sky Blue", "Pink", "Lavender"], "Slim-fit formal cotton shirt with spread collar."),
        ("Checked Casual Shirt", 799, ["Red", "Navy", "Olive"], "Yarn-dyed checked casual shirt."),
        ("Linen Blend Shirt", 1199, ["Beige", "Mint", "White"], "Breathable linen-blend shirt for summer."),
        ("Denim Shirt", 1099, ["Blue", "Black"], "Washed denim shirt with snap buttons."),
    ]),
    ("TRS", "trousers", "pants", "trousers pants men clothing", [
        ("Men's Formal Trousers", 999, ["Black", "Navy", "Grey", "Beige"], "Flat-front formal trousers, slim fit."),
        ("Slim Fit Chinos", 1099, ["Beige", "Olive", "Navy", "Tan"], "Stretch cotton chinos for work or weekends."),
        ("Cargo Pants", 1299, ["Olive", "Black", "Beige"], "Six-pocket cotton cargo pants."),
    ]),
    ("JNS", "jeans", "pants", "jeans denim men pants bottom wear clothing", [
        ("Men's Slim Fit Jeans", 1199, ["Blue", "Black", "Charcoal"], "Slim-fit stretch denim jeans."),
        ("Men's Relaxed Fit Jeans", 1399, ["Sky Blue", "Blue"], "Relaxed straight-leg jeans."),
    ]),
    ("SHR", "shorts", "pants", "sportswear men women clothing", [
        ("Cotton Casual Shorts", 499, ["Navy", "Grey", "Olive"], "Knee-length cotton shorts with pockets."),
        ("Track Pants with Zip Pockets", 699, ["Black", "Navy", "Charcoal"], "Tapered track pants (joggers) for training."),
        ("Fleece Joggers", 849, ["Grey", "Black"], "Soft fleece joggers with cuffed hems."),
    ]),
    ("MKU", "kurta", "shirt", "men ethnic wear festive clothing", [
        ("Men's Cotton Kurta", 799, ["White", "Yellow", "Sky Blue", "Maroon"], "Straight cotton kurta with mandarin collar."),
        ("Kurta Pyjama Set", 1299, ["Cream", "Navy", "Mustard"], "Kurta with matching pyjama for festive wear."),
        ("Nehru Jacket", 1499, ["Navy", "Maroon", "Beige"], "Woven Nehru jacket to layer over a kurta."),
    ]),
    ("WIN", "jacket", "hoodie", "winter wear men women clothing", [
        ("Quilted Bomber Jacket", 1999, ["Black", "Olive", "Navy"], "Lightweight quilted bomber jacket."),
        ("Woollen Pullover Sweater", 1299, ["Maroon", "Grey", "Navy"], "Round-neck wool-blend sweater (pullover) for winter."),
        ("Men's Slim Fit Blazer", 2999, ["Navy", "Black", "Charcoal"], "Single-breasted blazer for office and occasions."),
        ("Denim Jacket", 1799, ["Blue", "Black"], "Classic trucker denim jacket."),
    ]),
    ("KID", "kids", "tee", "kids boys girls children clothing", [
        ("Kids' Printed T-Shirt Pack of 2", 499, ["Multicolour"], "Two soft cotton printed t-shirts for kids."),
        ("Girls' Party Frock", 899, ["Pink", "Red", "Lavender"], "Layered net party frock."),
        ("Boys' Shorts and Tee Set", 599, ["Blue", "Yellow"], "Cotton tee and shorts set."),
        ("Kids' School Shoes", 699, ["Black", "White"], "Velcro school shoes with cushioned sole."),
    ]),
    ("RUN", "shoes", "shoe", "shoes footwear men women", [
        ("Running Shoes", 1499, ["Black", "Blue", "Grey", "Red"], "Lightweight mesh running shoes with cushioned sole."),
        ("Walking Shoes", 999, ["Navy", "Grey"], "Slip-on walking shoes with memory foam."),
        ("Formal Leather Shoes", 1999, ["Black", "Brown", "Tan"], "Lace-up derby shoes in faux leather."),
        ("Canvas Sneakers", 899, ["White", "Black", "Navy"], "Low-top canvas sneakers for everyday wear."),
    ]),
    ("SND", "sandals", "shoe", "footwear men women", [
        ("Men's Sports Sandals", 799, ["Black", "Olive"], "Adjustable strap sports sandals."),
        ("Flip Flops", 249, ["Blue", "Black", "Pink"], "Soft rubber flip flops (slippers, chappal) for home and beach."),
        ("Women's Block Heels", 1099, ["Beige", "Black", "Rose Gold"], "Block-heel sandals with ankle strap."),
        ("Embroidered Juttis", 699, ["Gold", "Maroon", "Pink"], "Handcrafted embroidered juttis."),
        ("Women's Flat Sandals", 499, ["Tan", "Black", "Silver"], "Comfort flat sandals for daily wear."),
    ]),
    ("WCH", "watch", "watch", "watch men women accessories", [
        ("Analog Wrist Watch", 1299, ["Black", "Brown", "Silver", "Rose Gold"], "Analog watch with stainless steel case."),
        ("Digital Sports Watch", 799, ["Black", "Blue"], "Water-resistant digital watch with stopwatch."),
    ]),
    ("ACC", "accessories", "bag", "accessories men women", [
        ("Leather Wallet", 599, ["Brown", "Black", "Tan"], "Bi-fold wallet with card slots."),
        ("Reversible Belt", 499, ["Black", "Brown"], "Reversible formal belt with pin buckle."),
        ("Polarized Sunglasses", 899, ["Black", "Brown", "Gold"], "UV-protected polarized sunglasses."),
        ("Cotton Socks Pack of 5", 349, ["Multicolour"], "Five pairs of ankle-length cotton socks."),
        ("Printed Stole Scarf", 399, ["Pink", "Mustard", "Teal"], "Soft printed stole."),
    ]),
    ("BAG", "bag", "bag", "bag bags travel", [
        ("Laptop Backpack 30L", 1199, ["Black", "Grey", "Navy"], "Water-resistant laptop bag and backpack with padded laptop sleeve."),
        ("Women's Handbag", 1499, ["Tan", "Black", "Wine"], "Structured handbag with zip closure."),
        ("Sling Bag", 699, ["Brown", "Black", "Pink"], "Compact cross-body sling bag."),
        ("School Bag", 799, ["Blue", "Red", "Purple"], "Lightweight school bag with three compartments."),
        ("Cabin Trolley Bag 55 cm", 2799, ["Navy", "Teal", "Black"], "Hard-shell cabin trolley with four wheels."),
    ]),
    ("AUD", "earphones", "headphones", "audio electronics", [
        ("True Wireless Earbuds", 1299, ["Black", "White", "Blue"], "Bluetooth 5.3 wireless earphones (TWS) with 30-hour playback."),
        ("Bluetooth Neckband", 899, ["Black", "Red"], "Neckband earphones with fast charging."),
        ("Over-Ear Wireless Headphones", 1999, ["Black", "Silver", "Blue"], "Wireless over-ear headphones with deep bass."),
        ("Wired Earphones with Mic", 349, ["Black", "White"], "In-ear wired earphones with inline mic."),
        ("Portable Bluetooth Speaker", 1499, ["Black", "Teal", "Red"], "Splash-proof speaker with 12-hour battery."),
    ]),
    ("MOB", "mobile accessories", "phone", "mobile accessories electronics", [
        ("Power Bank 10000 mAh", 999, ["Black", "White", "Blue"], "Slim 10000 mAh power bank with fast charging."),
        ("Fast Charger 25W", 599, ["White", "Black"], "25W USB-C wall charger."),
        ("USB-C Charging Cable 1.5 m", 249, ["White", "Black"], "Braided USB-C cable, 1.5 metre."),
        ("Phone Back Cover", 199, ["Black", "Blue", "Pink"], "Shock-absorbing back cover."),
        ("Budget Smartphone 6GB 128GB", 9999, ["Black", "Blue", "Green"], "Mobile phone with 6.6-inch display, 5000 mAh battery, 50 MP camera."),
        ("Tempered Glass Screen Guard", 149, ["Silver"], "Edge-to-edge tempered glass."),
    ]),
    ("GDG", "electronics", "watch", "gadget wearable electronics", [
        ("Smartwatch with Calling", 1999, ["Black", "Rose Gold", "Blue"], "1.8-inch display smartwatch with Bluetooth calling."),
        ("Fitness Band", 1299, ["Black", "Olive"], "Heart-rate and sleep tracking fitness band."),
    ]),
    ("CMP", "computer accessories", "phone", "computer accessories electronics", [
        ("Wireless Mouse", 499, ["Black", "Grey", "Blue"], "2.4 GHz wireless optical mouse."),
        ("Wireless Keyboard", 899, ["Black", "White"], "Full-size wireless keyboard."),
        ("Pen Drive 64 GB", 449, ["Silver", "Black"], "USB 3.0 metal pen drive."),
        ("Laptop Stand", 799, ["Silver", "Black"], "Adjustable aluminium laptop stand."),
    ]),
    ("GRM", "grooming appliances", "bottle", "grooming appliance electronics", [
        ("Beard Trimmer", 899, ["Black", "Blue"], "Cordless trimmer with 20 length settings."),
        ("Hair Dryer 1200W", 799, ["Pink", "Black"], "Compact hair dryer with two heat settings."),
        ("Hair Straightener", 1099, ["Black", "Purple"], "Ceramic plate hair straightener."),
    ]),
    ("KIT", "kitchen", "pot", "kitchen cookware home", [
        ("Pressure Cooker 5 Litre", 1499, ["Silver"], "Aluminium pressure cooker, 5 litre, induction base."),
        ("Non-Stick Kadai with Lid", 899, ["Black", "Red"], "Non-stick kadai, 24 cm, with glass lid."),
        ("Non-Stick Dosa Tawa", 649, ["Black"], "Flat non-stick tawa, 28 cm."),
        ("Mixer Grinder 500W", 2499, ["White", "Red"], "500W mixer grinder with three jars."),
        ("Electric Kettle 1.5 Litre", 799, ["Steel", "Black"], "Stainless steel electric kettle with auto cut-off."),
        ("Stainless Steel Dinner Set 24 Pieces", 1999, ["Steel"], "24-piece stainless steel dinner set."),
        ("Airtight Storage Containers Set of 6", 599, ["White", "Blue"], "Six airtight kitchen storage containers."),
    ]),
    ("BTL", "bottle", "bottle", "kitchen home", [
        ("Stainless Steel Water Bottle 1 Litre", 399, ["Steel", "Black", "Blue"], "Single-wall steel bottle, leak-proof."),
        ("Insulated Flask 500 ml", 699, ["Black", "Pink", "Teal"], "Keeps drinks hot or cold for 12 hours."),
        ("Steel Lunch Box 3 Containers", 549, ["Steel", "Blue"], "Three-container insulated lunch box (tiffin) with bag."),
    ]),
    ("HOM", "home", "home", "home furnishing decor", [
        ("Cotton Double Bedsheet with 2 Pillow Covers", 799, ["Blue", "Pink", "Grey", "Yellow"], "144 TC cotton double bed sheet with two pillow covers."),
        ("Microfibre Pillow Set of 2", 599, ["White"], "Soft microfibre sleeping pillows."),
        ("Cotton Bath Towel Set of 2", 499, ["White", "Teal", "Grey"], "Absorbent 450 GSM bath towels."),
        ("Door Curtains Set of 2", 699, ["Maroon", "Beige", "Navy"], "Seven-foot polyester door curtains."),
        ("Cushion Covers Set of 5", 399, ["Mustard", "Teal", "Multicolour"], "16 by 16 inch cushion covers."),
        ("LED Bulb 9W Pack of 4", 349, ["White"], "Energy-saving 9W LED bulbs."),
        ("Scented Candles Set of 3", 449, ["Cream", "Lavender"], "Soy wax scented candles."),
    ]),
    ("BTY", "beauty", "bottle", "beauty skincare makeup personal care", [
        ("Face Wash 150 ml", 249, ["Mint"], "Gentle daily face wash for all skin types."),
        ("Sunscreen SPF 50 50 g", 399, ["Cream"], "Lightweight broad-spectrum sunscreen."),
        ("Moisturizer 200 ml", 349, ["White"], "Non-greasy daily moisturizer."),
        ("Matte Lipstick", 299, ["Red", "Pink", "Wine", "Peach"], "Long-lasting matte lipstick."),
        ("Kajal Pencil Twin Pack", 199, ["Black"], "Smudge-proof kajal, pack of two."),
        ("Eau de Parfum 100 ml", 899, ["Gold", "Black"], "Long-lasting fragrance, 100 ml."),
        ("Deodorant Body Spray 150 ml", 229, ["Blue", "Black"], "Fresh body spray."),
        ("Vitamin C Face Serum 30 ml", 499, ["Orange"], "Brightening vitamin C serum."),
    ]),
    ("HAR", "hair care", "bottle", "hair care personal care", [
        ("Anti-Dandruff Shampoo 340 ml", 329, ["Blue"], "Daily anti-dandruff shampoo."),
        ("Coconut Hair Oil 500 ml", 249, ["White"], "Pure coconut hair oil."),
        ("Onion Hair Oil 200 ml", 399, ["Brown"], "Onion hair oil for hair fall control."),
        ("Beard Growth Oil 30 ml", 349, ["Brown"], "Beard oil with natural extracts."),
    ]),
    ("GRO", "grocery", "box", "grocery groceries food staples", [
        ("Basmati Rice 5 kg", 649, ["Cream"], "Aged long-grain basmati rice."),
        ("Whole Wheat Atta 10 kg", 449, ["Beige"], "Chakki-fresh whole wheat atta."),
        ("Toor Dal 1 kg", 189, ["Yellow"], "Unpolished toor dal."),
        ("Assam Tea 1 kg", 499, ["Brown"], "Strong Assam CTC tea."),
        ("Instant Coffee 200 g", 449, ["Brown"], "Rich instant coffee."),
        ("Cow Ghee 1 Litre", 699, ["Yellow"], "Pure cow ghee."),
        ("Sunflower Oil 5 Litre", 799, ["Yellow"], "Refined sunflower cooking oil."),
        ("California Almonds 500 g", 549, ["Brown"], "Premium whole almonds (badam), dry fruits."),
        ("Natural Honey 500 g", 299, ["Gold"], "Pure natural honey."),
        ("Assorted Cookies Gift Box", 349, ["Red"], "Assorted butter cookies gift box."),
    ]),
    ("BKS", "books", "book", "books stationery", [
        ("Bestselling Fiction Novel", 299, ["Blue"], "Paperback bestselling novel."),
        ("Self-Help Paperback", 249, ["Orange"], "Popular self-help paperback."),
        ("Spiral Notebooks Pack of 6", 399, ["Multicolour"], "Six single-line spiral notebooks."),
        ("Ball Pens Pack of 20", 149, ["Blue"], "Smooth-writing ball pens."),
        ("Undated Daily Planner", 449, ["Black", "Tan"], "Hardbound undated daily planner."),
    ]),
    ("SPT", "sports", "ball", "sports fitness gym", [
        ("Yoga Mat 6 mm", 599, ["Purple", "Blue", "Black"], "Anti-slip 6 mm yoga mat with strap."),
        ("Dumbbells 5 kg Pair", 999, ["Black"], "PVC-coated 5 kg dumbbells, pair."),
        ("Kashmir Willow Cricket Bat", 1499, ["Tan"], "Full-size Kashmir willow cricket bat."),
        ("Badminton Racket Set of 2", 699, ["Blue", "Red"], "Two rackets with cover and shuttles."),
        ("Football Size 5", 549, ["White", "Yellow"], "Machine-stitched size 5 football."),
        ("Skipping Rope", 199, ["Black", "Red"], "Adjustable speed skipping rope."),
    ]),
    ("TOY", "toys", "box", "toys kids baby games", [
        ("Building Blocks 100 Pieces", 599, ["Multicolour"], "Colourful building blocks for ages 3 and up."),
        ("Teddy Bear Soft Toy 2 Feet", 699, ["Brown", "Pink"], "Huggable two-foot teddy bear."),
        ("Remote Control Car", 899, ["Red", "Blue"], "Rechargeable remote control car."),
        ("Baby Diapers Medium Pack of 72", 899, ["White"], "Soft diapers with 12-hour absorption."),
    ]),
]

FIRST = ["Aarav", "Diya", "Kabir", "Ananya", "Rohan", "Meera", "Vikram", "Sana", "Arjun", "Priya",
         "Ishaan", "Neha", "Rahul", "Kavya", "Aditya", "Pooja", "Farhan", "Ritu", "Manish", "Tanvi"]
GOOD = ["Exactly as described. Happy with the purchase.", "Great quality for the price.",
        "Looks even better in person.", "Fast delivery and well packed.", "Would buy again.",
        "Good value. Using it daily."]
OKAY = ["Decent for the price.", "Does the job. Nothing special.", "Good, but packaging could be better.",
        "Average quality, acceptable at this price."]


def _h(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


def _code(colour: str) -> str:
    letters = re.sub(r"[^A-Z]", "", colour.upper())
    return (letters + "XXX")[:3]


def build() -> dict:
    old = json.loads(PATH.read_text(encoding="utf-8"))
    by_sku = {p["sku"]: p for p in old["products"]}
    products = []
    for sku in KEEP:
        product = dict(by_sku[sku])
        product["tags"] = KEEP_TAGS[sku]
        products.append(product)
    used = set(KEEP)

    for prefix, category, icon, tags, items in GROUPS:
        for title, price, colours, blurb in items:
            for colour in colours:
                code = _code(colour)
                rupees = price
                sku = f"{prefix}-{code}-{rupees}"
                while sku in used:  # same prefix + colour + price: nudge the price
                    rupees += 10
                    sku = f"{prefix}-{code}-{rupees}"
                used.add(sku)
                seed = _h(sku)
                rating = round(3.7 + (seed % 12) / 10, 1)  # 3.7 .. 4.8
                if category == "t-shirt":
                    rating = min(rating, 4.7)
                single = len(colours) == 1
                full_title = title if single else f"{title} — {colour}"
                stars = 5 if rating >= 4.3 else 4
                reviews = [
                    {"author": f"{FIRST[seed % 20]} {'ABCDKMNPRS'[seed // 20 % 10]}.", "stars": stars,
                     "text": (GOOD if stars == 5 else OKAY)[seed // 7 % (6 if stars == 5 else 4)]},
                    {"author": f"{FIRST[seed // 3 % 20]} {'TSRGHJLVBN'[seed // 11 % 10]}.", "stars": 4,
                     "text": OKAY[seed // 13 % 4]},
                ]
                products.append({
                    "sku": sku, "title": full_title, "price_paise": rupees * 100,
                    "category": category, "rating": rating, "color": COLORS[colour], "icon": icon,
                    "description": blurb + ("" if single else f" Colour: {colour}."),
                    "tags": f"{tags} {colour.lower()}",
                    "reviews": reviews,
                })
    old["products"] = products
    return old


def check(catalog: dict) -> None:
    skus = [p["sku"] for p in catalog["products"]]
    assert len(skus) == len(set(skus)), "duplicate SKU"
    for product in catalog["products"]:
        assert re.fullmatch(r"[A-Z]{3}-[A-Z]{3}-\d{3,5}", product["sku"]), product["sku"]
        if product["category"] == "t-shirt" and product["sku"] not in KEEP:
            assert "green" not in product["title"].lower(), product["sku"]
            assert product["rating"] < 4.9, product["sku"]


if __name__ == "__main__":
    data = build()
    check(data)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cats = sorted({p["category"] for p in data["products"]})
    print(f"{len(data['products'])} products in {len(cats)} categories")
