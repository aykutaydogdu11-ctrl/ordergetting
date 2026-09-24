import json
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Load menu once at startup
with open("menu.json", "r", encoding="utf-8") as f:
    MENU = json.load(f)

with open("abbreviations.json", "r", encoding="utf-8") as f:
    ABBREVIATIONS = json.load(f)["abbreviations"]

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
    out.append("Take Away" if table_no == "0" else f"Table {table_no}")
    return "\n".join(out)


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


@app.route("/order/new/<table_no>")
def order_new(table_no):
    existing = ORDERS.get(table_no, {"customers": []})
    return render_template(
        "order_new.html",
        table_no=table_no,
        menu=MENU,
        abbreviations=ABBREVIATIONS,
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
                bar_food_lines.append({"text": text, "notes": notes})
                # Any hot filling inside it needs the kitchen to cook it and send it up.
                text_lower = text.lower()
                for ing in hot_ingredients:
                    if ing in text_lower:
                        kitchen_lines.append({"text": f"{ing.title()} for bar", "notes": []})
            else:
                # A full cooked dish — kitchen makes and sends the whole thing.
                kitchen_lines.append({"text": text, "notes": notes})

    bar_ticket = format_ticket(
        "Bar", [("Drinks", drink_lines), ("Food", bar_food_lines)], table_no
    )
    kitchen_ticket = format_ticket("Kitchen", [("Food", kitchen_lines)], table_no)

    return jsonify({"bar_ticket": bar_ticket, "kitchen_ticket": kitchen_ticket})


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
