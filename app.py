import json
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Load menu once at startup
with open("menu.json", "r", encoding="utf-8") as f:
    MENU = json.load(f)

# In-memory table state: { "12": { "items": [ {name, price, qty, note}, ... ] } }
# No history is kept — this is intentionally not a database.
TABLES = {}


@app.route("/")
def home():
    return render_template("tables.html", tables=TABLES)


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
