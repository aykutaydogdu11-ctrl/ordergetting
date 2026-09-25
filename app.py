import json
import os
import requests
from flask import Flask, render_template, request, jsonify
import pricing

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"

# Load menu once at startup
with open("menu.json", "r", encoding="utf-8") as f:
    MENU = json.load(f)

with open("abbreviations.json", "r", encoding="utf-8") as f:
    ABBREVIATIONS = json.load(f)["abbreviations"]

with open("category_shortcuts.json", "r", encoding="utf-8") as f:
    CATEGORY_SHORTCUTS = json.load(f)["shortcuts"]

with open("station_rules.json", "r", encoding="utf-8") as f:
    STATION_RULES = json.load(f)

# In-memory table state for the old button-based flow (kept for backward compat).
# { "12": { "items": [ {name, price, qty, note}, ... ] } }
TABLES = {}

# In-memory state for the abbreviation-driven order screen.
# { "12": { "customers": [ {id, lines: [{text, notes, type, category}]}, ... ] } }
# No history is kept — finishing a table wipes it, by design.
ORDERS = {}


def save_abbreviations():
    with open("abbreviations.json", "w", encoding="utf-8") as f:
        json.dump({"abbreviations": ABBREVIATIONS}, f, indent=2, ensure_ascii=False)


def save_category_shortcuts():
    with open("category_shortcuts.json", "w", encoding="utf-8") as f:
        json.dump({"shortcuts": CATEGORY_SHORTCUTS}, f, indent=2, ensure_ascii=False)


def guess_category(text):
    """Best-effort match of a free-text order line to a menu category, by item name."""
    text_lower = text.lower()
    for cat in MENU["categories"]:
        for item in cat["items"]:
            item_name = item["name"].lower()
            if item_name in text_lower or text_lower in item_name:
                return cat["name"]
    return None


def format_ticket(title, sections, table_no):
    out = [title.upper()]
    for section_name, lines in sections:
        if not lines:
            continue
        out.append(section_name)
        for i, line in enumerate(lines, 1):
            out.append(f"  {i}-{line['text']}")
            for note in line.get("notes", []):
                out.append(f"        {note}")
            out.append("")  # blank line between items for easier reading
    out.append("Take Away" if table_no == "0" else f"Table {table_no}")
    return "\n".join(out)


def log_ai_interaction(input_text, result):
    # Best-effort log so patterns can be reviewed later and turned into
    # proper rules/abbreviations — never let logging break the request.
    try:
        with open("ai_interactions.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"input": input_text, "result": result}, ensure_ascii=False) + "\n")
    except Exception:
        pass


@app.route("/order/ai-assist", methods=["POST"])
def ai_assist():
    if not OPENAI_API_KEY:
        return jsonify({"error": "AI is not set up yet — ask your developer to add the OPENAI_API_KEY."}), 400

    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Nothing typed."}), 400

    extra_topping = next(
        (c for c in MENU["categories"] if c["name"] == "Extra Topping"), {"items": []}
    )
    price_list = {item["name"]: item.get("price") for item in extra_topping["items"]}

    system_prompt = (
        "You are a POS assistant for a UK café. Staff type shorthand order codes, "
        "dot-separated (e.g. 'E.B.Chips'), where 'x2' after a code means quantity 2 "
        "(e.g. 'Ex2' means 2 Eggs). Here are the café's own abbreviation codes:\n"
        f"{json.dumps(ABBREVIATIONS)}\n\n"
        "Here is the Extra Topping price list in GBP, used to price custom combos:\n"
        f"{json.dumps(price_list)}\n\n"
        "Given the staff member's shorthand input, respond with ONLY a JSON object "
        "with these keys:\n"
        '- "breakdown": a short, clear description, e.g. "2x Egg, 2x Hash Brown, Chips, 2x Bacon"\n'
        '- "estimated_price": a number — the sum of the known Extra Topping prices '
        "(respecting quantities) for every ingredient you recognise\n"
        '- "unrecognised": an array of any words you could not match to a known item or code\n'
        '- "note": a short note for the waiter, or an empty string if nothing to flag'
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            },
            timeout=15,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
    except Exception as e:
        result = {"error": f"AI request failed: {e}"}

    log_ai_interaction(text, result)
    return jsonify(result)


@app.route("/")
def home():
    return render_template("tables.html", tables=ORDERS)


@app.route("/abbreviations", methods=["GET", "POST"])
def abbreviations_page():
    if request.method == "POST":
        code = request.form.get("code", "").strip().lower()
        full = request.form.get("full", "").strip()
        if code and full:
            ABBREVIATIONS[code] = full
            save_abbreviations()
    return render_template("abbreviations.html", abbreviations=ABBREVIATIONS)


@app.route("/abbreviations/delete", methods=["POST"])
def delete_abbreviation():
    code = request.json.get("code", "").strip().lower()
    ABBREVIATIONS.pop(code, None)
    save_abbreviations()
    return jsonify({"ok": True})


@app.route("/category-shortcuts", methods=["GET", "POST"])
def category_shortcuts_page():
    if request.method == "POST":
        code = request.form.get("code", "").strip().lower()
        category = request.form.get("category", "").strip()
        if code and category:
            CATEGORY_SHORTCUTS[code] = category
            save_category_shortcuts()
    category_names = [cat["name"] for cat in MENU["categories"]]
    return render_template(
        "category_shortcuts.html",
        shortcuts=CATEGORY_SHORTCUTS,
        category_names=category_names,
    )


@app.route("/category-shortcuts/delete", methods=["POST"])
def delete_category_shortcut():
    code = request.json.get("code", "").strip().lower()
    CATEGORY_SHORTCUTS.pop(code, None)
    save_category_shortcuts()
    return jsonify({"ok": True})


@app.route("/order/new/<table_no>")
def order_new(table_no):
    existing = ORDERS.get(table_no, {"customers": []})
    return render_template(
        "order_new.html",
        table_no=table_no,
        menu=MENU,
        abbreviations=ABBREVIATIONS,
        category_shortcuts=CATEGORY_SHORTCUTS,
        initial_customers=existing["customers"],
    )


@app.route("/order/<table_no>/save", methods=["POST"])
def save_order(table_no):
    data = request.json
    ORDERS[table_no] = {"customers": data.get("customers", [])}
    return jsonify({"ok": True})


@app.route("/order/<table_no>/finish", methods=["POST"])
def finish_order(table_no):
    # Closing/finishing a table wipes its data — no history kept, by design.
    ORDERS.pop(table_no, None)
    return jsonify({"ok": True})


@app.route("/order/generate", methods=["POST"])
def generate_tickets():
    data = request.json
    table_no = data["table_no"]
    customers = data["customers"]  # [{lines: [{text, notes, type, category}]}]

    hot_ingredients = STATION_RULES["hot_ingredients"]
    bar_categories = set(STATION_RULES["bar_categories"])

    drink_lines = []
    bar_food_lines = []
    kitchen_lines = []

    for cust in customers:
        for line in cust.get("lines", []):
            text = line["text"]
            notes = line.get("notes", [])
            kitchen_note = line.get("kitchenNote", "")
            line_type = line.get("type", "item")

            if line_type == "combo":
                # A hand-built combo of ingredients (e.g. "Egg, Bacon, Chips")
                # is always one complete plate made and sent by the kitchen.
                kitchen_lines.append({"text": text, "notes": notes})
                continue

            category = line.get("category") or guess_category(text)

            if category in ("Hot Drinks", "Cold Drinks", "Fresh Milkshakes"):
                drink_lines.append({"text": text, "notes": notes})
                continue

            if category in bar_categories or category is None:
                # Prepared at the front; full item goes on the bar ticket.
                # The full ingredient breakdown is kitchen-only info — never
                # shown here, only the customer's actual requested changes.
                bar_food_lines.append({"text": text, "notes": notes})
                # Any hot filling inside it needs the kitchen to cook it and send it up.
                text_lower = text.lower()
                for ing in hot_ingredients:
                    if ing in text_lower:
                        kitchen_lines.append({"text": f"{ing.title()} for bar", "notes": []})
            else:
                # A full cooked dish — kitchen makes and sends the whole thing.
                # Lead with the full ingredient breakdown so the chef never has
                # to guess what a named dish (e.g. "Hope 1") actually contains.
                kitchen_notes = ([kitchen_note] if kitchen_note else []) + notes
                kitchen_lines.append({"text": text, "notes": kitchen_notes})

    bar_ticket = format_ticket(
        "Bar", [("Drinks", drink_lines), ("Food", bar_food_lines)], table_no
    )
    kitchen_ticket = format_ticket("Kitchen", [("Food", kitchen_lines)], table_no)

    return jsonify({"bar_ticket": bar_ticket, "kitchen_ticket": kitchen_ticket})


@app.route("/order/price", methods=["POST"])
def price_order():
    data = request.json
    customers = data.get("customers", [])
    all_lines = []
    for cust in customers:
        for line in cust.get("lines", []):
            all_lines.append({
                "text": line["text"],
                "category": line.get("category"),
            })
    priced_lines, total, unresolved = pricing.price_order_lines(all_lines)
    return jsonify({
        "priced_lines": priced_lines,
        "total": round(total, 2),
        "unresolved": unresolved,
    })


@app.route("/order/learn-price", methods=["POST"])
def learn_price():
    data = request.json
    raw_text = data.get("text", "").strip()
    name = data.get("name", "").strip()
    price = data.get("price")
    if not raw_text or not name or price is None:
        return jsonify({"error": "Missing text, name, or price."}), 400
    try:
        pricing.save_learned_price(raw_text, name, float(price))
    except (TypeError, ValueError):
        return jsonify({"error": "Price must be a number."}), 400
    return jsonify({"ok": True})


@app.route("/table/<table_no>")
def table_view(table_no):
    order = TABLES.get(table_no, {"items": []})
    return render_template("order.html", table_no=table_no, menu=MENU, order=order)


@app.route("/table/<table_no>/add", methods=["POST"])
def add_item(table_no):
    data = request.json
    item = {
        "name": data["name"],
        "price": data["price"],
        "qty": data.get("qty", 1),
        "note": data.get("note", ""),
    }
    TABLES.setdefault(table_no, {"items": []})
    TABLES[table_no]["items"].append(item)
    return jsonify({"ok": True, "order": TABLES[table_no]})


@app.route("/table/<table_no>/remove", methods=["POST"])
def remove_item(table_no):
    index = request.json["index"]
    if table_no in TABLES and 0 <= index < len(TABLES[table_no]["items"]):
        TABLES[table_no]["items"].pop(index)
    return jsonify({"ok": True, "order": TABLES.get(table_no, {"items": []})})


@app.route("/table/<table_no>/close", methods=["POST"])
def close_table(table_no):
    # Paying/closing a table wipes its data — no history kept, per design.
    TABLES.pop(table_no, None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True)
