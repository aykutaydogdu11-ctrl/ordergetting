"""
pricing.py
============================================================
Prices order lines from the new abbreviation-driven order app,
using menu.json plus a persistent learned_prices.json — ported
from the original handwritten-ticket-reading app's pricing.py.

Design notes (carried over from the original)
----------------------------------------------
- Matching is fuzzy (difflib), tolerant of typos and reordered
  ingredient lists, never guesses between two close candidates —
  it flags those for a human instead (see get_suggestions).
- Once a human confirms a price for a given raw line of text via
  /order/learn-price, it's saved to learned_prices.json keyed by
  the normalized text, and never asked about again.
- Sandwich items already carry their bread size in the text as
  typed by the new app's sandwich modal, e.g. "Bacon (Crusty)" —
  detected here as a parenthesised size, falling back to the old
  shorthand tokens (bgt/crb/sand/roll) for anything typed as a
  raw combo instead.
- Jacket Potatoes are matched against preset names first, then
  against their ingredient descriptions (item "note" fields).
- A hand-built combo of Extra Topping ingredients (comma-joined,
  as the new app already formats them) is priced by summing each
  ingredient — capped at a Small Breakfast combo's price if that
  combo's exact ingredients are cheaper than buying them apart.
============================================================
"""

import json
import os
import re
import difflib

LEARNED_PRICES_FILE = os.environ.get("LEARNED_PRICES_FILE", "learned_prices.json")


def load_learned_prices():
    try:
        with open(LEARNED_PRICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_learned_price(raw_text, name, price):
    learned = load_learned_prices()
    learned[_normalize(raw_text)] = {"name": name, "price": float(price)}
    with open(LEARNED_PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(learned, f, indent=2, ensure_ascii=False)


# ============================================================
# TEXT NORMALIZATION + FUZZY MATCHING
# ============================================================

def _normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _word_tokens(text):
    return [w for w in _normalize(text).split(" ") if w]


def _best_word_ratio(word, other_words):
    if not other_words:
        return 0.0
    return max(difflib.SequenceMatcher(None, word, w).ratio() for w in other_words)


def _token_f1(target_words, candidate_words):
    if not target_words or not candidate_words:
        return 0.0
    recall = sum(_best_word_ratio(w, candidate_words) for w in target_words) / len(target_words)
    precision = sum(_best_word_ratio(w, target_words) for w in candidate_words) / len(candidate_words)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def best_fuzzy_match(raw_text, candidates, cutoff=0.6, ambiguity_margin=0.08):
    """candidates: list of (key, display_name). Returns (key, name, score)
    or None if nothing clears the cutoff, or the top two are too close to
    call automatically (flagged for a human instead of guessed)."""
    norm_target = _normalize(raw_text)
    target_words = _word_tokens(raw_text)
    if not norm_target:
        return None
    scored = []
    for key, name in candidates:
        whole_ratio = difflib.SequenceMatcher(None, norm_target, _normalize(name)).ratio()
        token_score = _token_f1(target_words, _word_tokens(name))
        scored.append((max(whole_ratio, token_score), key, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < cutoff:
        return None
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < ambiguity_margin:
        return None
    return scored[0][1], scored[0][2], scored[0][0]


def top_candidates(raw_text, candidates, n=3):
    target_words = _word_tokens(raw_text)
    norm_target = _normalize(raw_text)
    if not norm_target:
        return []
    scored = []
    for key, name in candidates:
        whole_ratio = difflib.SequenceMatcher(None, norm_target, _normalize(name)).ratio()
        token_score = _token_f1(target_words, _word_tokens(name))
        scored.append((max(whole_ratio, token_score), key, name))
    scored.sort(reverse=True)
    return scored[:n]


# ============================================================
# FLATTEN menu.json (categories: [{name, items: [...]}])
# ============================================================

def build_flat_products(menu):
    """
    flat_items: [{"name", "price", "category"}] for every flat-priced item
    sandwich_fillings: [{"name", "prices": {roll,sand,crusty,bag}}]
    jp_presets: [{"name", "desc", "price"}]
    """
    flat_items, sandwich_fillings, jp_presets = [], [], []
    for cat in menu["categories"]:
        for item in cat["items"]:
            if item.get("prices"):
                sandwich_fillings.append({"name": item["name"], "prices": item["prices"]})
                continue
            price = item.get("price")
            if cat["name"] == "Jumbo Jacket Potatoes":
                jp_presets.append({"name": item["name"], "desc": item.get("note", ""), "price": price})
            if price is not None:
                flat_items.append({"name": item["name"], "price": price, "category": cat["name"]})
    return flat_items, sandwich_fillings, jp_presets


def _normalize_ingredient(name):
    n = re.sub(r"[^a-z0-9 ]", " ", name.strip().lower())
    n = re.sub(r"\s+", " ", n).strip()
    if len(n) > 3 and n.endswith("s") and not n.endswith("ss"):
        n = n[:-1]
    return n


def _parse_combo_ingredients(display_name):
    parts = re.split(r",|&", display_name)
    result = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s+(.+)$", part)
        qty, ingredient = (int(m.group(1)), m.group(2)) if m else (1, part)
        key = _normalize_ingredient(ingredient)
        result[key] = result.get(key, 0) + qty
    return result


def build_small_breakfast_combos(menu):
    combos = []
    for cat in menu["categories"]:
        if cat["name"] != "Small Breakfast":
            continue
        for item in cat["items"]:
            price = item.get("price")
            if price is None:
                continue
            combos.append((item["name"], price, _parse_combo_ingredients(item["name"])))
    return combos


def load_menu():
    with open("menu.json", "r", encoding="utf-8") as f:
        return json.load(f)


_MENU = load_menu()
FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS = build_flat_products(_MENU)
SMALL_BREAKFAST_COMBOS = build_small_breakfast_combos(_MENU)

DRINK_CATEGORIES = ("Hot Drinks", "Cold Drinks", "Fresh Milkshakes")


def reload_menu():
    global _MENU, FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS, SMALL_BREAKFAST_COMBOS
    _MENU = load_menu()
    FLAT_ITEMS, SANDWICH_FILLINGS, JP_PRESETS = build_flat_products(_MENU)
    SMALL_BREAKFAST_COMBOS = build_small_breakfast_combos(_MENU)


# ============================================================
# SANDWICH SIZE DETECTION
# ============================================================

SIZE_TOKEN_TO_COLUMN = {
    "bgt": "baguette", "baguette": "baguette",
    "crb": "crusty", "crusty": "crusty",
    "sand": "sand", "sandwich": "sand", "sdvc": "sand",
    "roll": "roll", "soft": "roll", "seedy": "roll",
    "bag": "bag",
}
_SIZE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted(SIZE_TOKEN_TO_COLUMN.keys(), key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
# The new app writes the chosen bread as "(Crusty)" / "(Baguette)" etc.
_PAREN_SIZE_PATTERN = re.compile(r"\(([^)]+)\)\s*$")


def detect_size_column(text):
    m = _PAREN_SIZE_PATTERN.search(text)
    if m:
        word = m.group(1).strip().lower()
        if word in SIZE_TOKEN_TO_COLUMN:
            return SIZE_TOKEN_TO_COLUMN[word]
    match = _SIZE_TOKEN_PATTERN.search(text)
    return SIZE_TOKEN_TO_COLUMN[match.group(1).lower()] if match else None


# ============================================================
# PRICE A JACKET POTATO
# ============================================================

def price_jacket_potato(raw_text):
    text_after_jp = re.sub(r"(?i)^\s*jp\b", "", raw_text).strip()
    text_after_jp = re.sub(r"(?i)\s*jacket potato\s*$", "", text_after_jp).strip()
    search_text = text_after_jp or raw_text

    name_candidates = [(p["name"], p["name"]) for p in JP_PRESETS]
    match = best_fuzzy_match(search_text, name_candidates, cutoff=0.6)
    if match:
        key, name, _ = match
        preset = next(p for p in JP_PRESETS if p["name"] == key)
        return name, preset["price"], f"matched JP preset '{name}'"

    desc_candidates = [(p["name"], p["desc"]) for p in JP_PRESETS if p["desc"]]
    match = best_fuzzy_match(search_text, desc_candidates, cutoff=0.35)
    if match:
        key, desc, _ = match
        preset = next(p for p in JP_PRESETS if p["name"] == key)
        return preset["name"], preset["price"], f"matched by ingredients to '{preset['name']}' ({desc})"
    return None


# ============================================================
# PRICE A SANDWICH
# ============================================================

def price_sandwich(raw_text):
    size_column = detect_size_column(raw_text)
    if not size_column:
        return None
    filling_text = _PAREN_SIZE_PATTERN.sub("", raw_text)
    filling_text = _SIZE_TOKEN_PATTERN.sub(" ", filling_text)
    filling_candidates = [(f["name"], f["name"]) for f in SANDWICH_FILLINGS]
    match = best_fuzzy_match(filling_text, filling_candidates, cutoff=0.35, ambiguity_margin=0.15)
    if not match:
        return None
    key, name, _ = match
    filling = next(f for f in SANDWICH_FILLINGS if f["name"] == key)
    price = filling["prices"].get(size_column)
    if price is None:
        return None
    return f"{name} ({size_column})", price, f"sandwich: {name} / {size_column}"


# ============================================================
# PRICE A HAND-BUILT COMBO (comma-joined Extra Topping ingredients)
# ============================================================

def price_extra_topping_combo(raw_text):
    parts = [p.strip() for p in raw_text.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    topping_candidates = [
        (i["name"], i["name"]) for i in FLAT_ITEMS if i["category"] == "Extra Topping"
    ]
    total = 0.0
    labels = []
    ordered_ingredients = {}
    for part in parts:
        m = re.match(r"^(.*?)\s+x(\d+)$", part.strip(), re.IGNORECASE)
        base, qty = (m.group(1).strip(), int(m.group(2))) if m else (part.strip(), 1)
        match = best_fuzzy_match(base, topping_candidates, cutoff=0.6, ambiguity_margin=0.1)
        if not match:
            return None
        key, name, _ = match
        price = next(i["price"] for i in FLAT_ITEMS if i["name"] == key and i["category"] == "Extra Topping")
        total += price * qty
        labels.append(f"{name} x{qty}" if qty > 1 else name)
        ing_key = _normalize_ingredient(name)
        ordered_ingredients[ing_key] = ordered_ingredients.get(ing_key, 0) + qty

    best_combo = None
    for combo_name, combo_price, combo_ingredients in SMALL_BREAKFAST_COMBOS:
        if combo_ingredients == ordered_ingredients and combo_price < total:
            if best_combo is None or combo_price > best_combo[1]:
                best_combo = (combo_name, combo_price)
    if best_combo:
        return best_combo[0], best_combo[1], f"capped at Small Breakfast combo (individually £{total:.2f})"
    return " + ".join(labels), total, "sum of extra toppings"


# ============================================================
# MAIN ENTRY POINTS
# ============================================================

def price_line(raw_text, category=None):
    """Returns (matched_name, price, note) or None."""
    text = raw_text.strip()
    if not text:
        return None

    learned = load_learned_prices().get(_normalize(text))
    if learned:
        return learned["name"], learned["price"], "learned"

    if category in DRINK_CATEGORIES or re.match(r"(?i)^\s*jp\b", text) or "jacket potato" in text.lower():
        result = price_jacket_potato(text)
        if result:
            return result

    combo_result = price_extra_topping_combo(text)
    if combo_result:
        return combo_result

    sandwich_result = price_sandwich(text)
    if sandwich_result:
        return sandwich_result

    candidates = [(i["name"], i["name"]) for i in FLAT_ITEMS]
    match = best_fuzzy_match(text, candidates, cutoff=0.55)
    if not match:
        return None
    key, name, _ = match
    price = next(i["price"] for i in FLAT_ITEMS if i["name"] == key)
    return name, price, f"matched '{name}'"


def get_suggestions(raw_text, n=3):
    """Top candidates (regardless of confidence) for a Confirm Price screen."""
    text = raw_text.strip()
    if re.match(r"(?i)^\s*jp\b", text) or "jacket potato" in text.lower():
        text_after_jp = re.sub(r"(?i)^\s*jp\b", "", text).strip()
        name_candidates = [(p["name"], p["name"]) for p in JP_PRESETS]
        top = top_candidates(text_after_jp or text, name_candidates, n)
        return [{"name": name, "price": next(p["price"] for p in JP_PRESETS if p["name"] == key)}
                for score, key, name in top]

    size_column = detect_size_column(text)
    if size_column:
        filling_text = _SIZE_TOKEN_PATTERN.sub(" ", _PAREN_SIZE_PATTERN.sub("", text))
        filling_candidates = [(f["name"], f["name"]) for f in SANDWICH_FILLINGS]
        top = top_candidates(filling_text, filling_candidates, n)
        return [{"name": f"{name} ({size_column})",
                  "price": next(f["prices"].get(size_column, 0) for f in SANDWICH_FILLINGS if f["name"] == key)}
                for score, key, name in top]

    candidates = [(i["name"], i["name"]) for i in FLAT_ITEMS]
    top = top_candidates(text, candidates, n)
    return [{"name": name, "price": next(i["price"] for i in FLAT_ITEMS if i["name"] == key)}
            for score, key, name in top]


# ============================================================
# FREE BREAKFAST DRINK CREDIT
# ============================================================

FREE_BASE_DRINK_NAMES = {"tea", "black coffee", "flat white", "cappuccino", "latte"}


def price_order_lines(all_lines):
    """
    all_lines: [{"text": ..., "category": ... or None}, ...] in the order
    they were entered.
    Returns (priced_lines, total, unresolved) where:
      priced_lines: [{"text", "matched_name", "price", "note"}]
      unresolved:   [{"text", "suggestions": [...]}]  (needs Confirm Price)
    Every Breakfast-category line ordered grants one free basic hot drink
    (Tea/Black Coffee/Flat White/Cappuccino/Latte), applied in line order.
    """
    priced_lines = []
    unresolved = []
    total = 0.0
    credits_remaining = sum(1 for l in all_lines if l.get("category") == "Breakfast")

    for line in all_lines:
        text = line["text"]
        category = line.get("category")
        result = price_line(text, category)
        if not result:
            unresolved.append({"text": text, "suggestions": get_suggestions(text)})
            continue

        name, price, note = result
        if category in DRINK_CATEGORIES or name.lower() in FREE_BASE_DRINK_NAMES:
            if credits_remaining > 0 and name.lower() in FREE_BASE_DRINK_NAMES:
                credits_remaining -= 1
                price = 0.0
                note = "free with breakfast"

        total += price
        priced_lines.append({"text": text, "matched_name": name, "price": price, "note": note})

    return priced_lines, total, unresolved
