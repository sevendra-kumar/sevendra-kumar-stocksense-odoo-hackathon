import sqlite3
import random
import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "stocksense-hackathon-secret")

# Store the database next to this file, not in the process's current working
# directory — that way it works the same whether you run `python app.py`
# locally or gunicorn starts it from a different working directory on Render.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "stocksense.db")


# ---------- Database ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS warehouses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sku TEXT UNIQUE NOT NULL,
            category_id INTEGER,
            uom TEXT NOT NULL DEFAULT 'pcs',
            reorder_level INTEGER NOT NULL DEFAULT 10,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            warehouse_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            UNIQUE(product_id, warehouse_id),
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
        );

        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            action_type TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            warehouse_id INTEGER,
            to_warehouse_id INTEGER,
            quantity_change INTEGER NOT NULL,
            note TEXT,
            username TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        """
    )
    # seed a default warehouse + category so the app isn't empty on first run
    cur = db.execute("SELECT COUNT(*) FROM warehouses")
    if cur.fetchone()[0] == 0:
        db.execute("INSERT INTO warehouses (name) VALUES ('Main Warehouse')")
        db.execute("INSERT INTO warehouses (name) VALUES ('Production Floor')")
        db.execute("INSERT INTO categories (name) VALUES ('General')")
    db.commit()
    db.close()


# Run once, at import time — this executes no matter how the app is
# started: `python app.py`, `flask run`, or gunicorn on Render. This is the
# fix for the single most common deployment failure with this app: tables
# never getting created because init_db() was hidden behind an
# `if __name__ == "__main__":` guard that gunicorn never triggers.
init_db()


# ---------- Auth helpers ----------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ---------- Auth routes ----------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, password),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username or email is already taken.")
            return redirect(url_for("signup"))
        flash("Account created. Please log in.")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ? AND password = ?",
            (username, password),
        ).fetchone()
        if user:
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Simplified OTP-style reset flow for a demo: generates a code
    and shows it on screen instead of emailing/texting it."""
    otp_display = None
    if request.method == "POST":
        step = request.form.get("step")
        if step == "request":
            code = str(random.randint(100000, 999999))
            session["reset_otp"] = code
            session["reset_username"] = request.form["username"].strip()
            otp_display = code
        elif step == "verify":
            if request.form.get("otp") == session.get("reset_otp"):
                db = get_db()
                db.execute(
                    "UPDATE users SET password = ? WHERE username = ?",
                    (request.form["new_password"], session.get("reset_username")),
                )
                db.commit()
                session.pop("reset_otp", None)
                flash("Password updated. Please log in.")
                return redirect(url_for("login"))
            flash("Incorrect code.")
            otp_display = session.get("reset_otp")
    return render_template("reset_password.html", otp_display=otp_display)


# ---------- Dashboard ----------

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    total_products = db.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    low_stock = db.execute(
        """SELECT p.name, p.reorder_level, COALESCE(SUM(s.quantity),0) qty
           FROM products p LEFT JOIN stock s ON s.product_id = p.id
           GROUP BY p.id HAVING qty <= p.reorder_level"""
    ).fetchall()
    recent = db.execute(
        """SELECT l.*, p.name as product_name FROM ledger l
           JOIN products p ON p.id = l.product_id
           ORDER BY l.id DESC LIMIT 8"""
    ).fetchall()
    counts = db.execute(
        """SELECT action_type, COUNT(*) c FROM ledger GROUP BY action_type"""
    ).fetchall()
    action_counts = {row["action_type"]: row["c"] for row in counts}
    return render_template(
        "dashboard.html",
        total_products=total_products,
        low_stock=low_stock,
        recent=recent,
        action_counts=action_counts,
    )


# ---------- Products ----------

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    db = get_db()
    if request.method == "POST":
        name = request.form["name"].strip()
        sku = request.form["sku"].strip()
        uom = request.form["uom"].strip() or "pcs"
        reorder_level = int(request.form.get("reorder_level") or 10)
        category = request.form.get("category", "General").strip() or "General"
        cat_row = db.execute("SELECT id FROM categories WHERE name = ?", (category,)).fetchone()
        if not cat_row:
            db.execute("INSERT INTO categories (name) VALUES (?)", (category,))
            cat_id = db.execute("SELECT id FROM categories WHERE name = ?", (category,)).fetchone()["id"]
        else:
            cat_id = cat_row["id"]
        try:
            db.execute(
                "INSERT INTO products (name, sku, category_id, uom, reorder_level) VALUES (?,?,?,?,?)",
                (name, sku, cat_id, uom, reorder_level),
            )
            db.commit()
            flash(f"Product '{name}' added.")
        except sqlite3.IntegrityError:
            flash("That SKU already exists.")
        return redirect(url_for("products"))

    rows = db.execute(
        """SELECT p.*, c.name as category_name, COALESCE(SUM(s.quantity),0) as total_stock
           FROM products p
           LEFT JOIN categories c ON c.id = p.category_id
           LEFT JOIN stock s ON s.product_id = p.id
           GROUP BY p.id ORDER BY p.id DESC"""
    ).fetchall()
    return render_template("products.html", products=rows)


# ---------- Shared helpers for stock movement ----------

def get_or_create_stock_row(db, product_id, warehouse_id):
    row = db.execute(
        "SELECT * FROM stock WHERE product_id=? AND warehouse_id=?",
        (product_id, warehouse_id),
    ).fetchone()
    if not row:
        db.execute(
            "INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (?,?,0)",
            (product_id, warehouse_id),
        )
        row = db.execute(
            "SELECT * FROM stock WHERE product_id=? AND warehouse_id=?",
            (product_id, warehouse_id),
        ).fetchone()
    return row


def log_ledger(db, action_type, product_id, warehouse_id, to_warehouse_id, qty_change, note):
    db.execute(
        """INSERT INTO ledger (timestamp, action_type, product_id, warehouse_id,
           to_warehouse_id, quantity_change, note, username)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            action_type,
            product_id,
            warehouse_id,
            to_warehouse_id,
            qty_change,
            note,
            session.get("username"),
        ),
    )


# ---------- Receipts (stock IN) ----------

@app.route("/receipts", methods=["GET", "POST"])
@login_required
def receipts():
    db = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        warehouse_id = int(request.form["warehouse_id"])
        qty = int(request.form["quantity"])
        note = request.form.get("note", "")
        row = get_or_create_stock_row(db, product_id, warehouse_id)
        db.execute("UPDATE stock SET quantity = ? WHERE id = ?", (row["quantity"] + qty, row["id"]))
        log_ledger(db, "receipt", product_id, warehouse_id, None, qty, note)
        db.commit()
        flash("Receipt recorded — stock increased.")
        return redirect(url_for("receipts"))

    products_list = db.execute("SELECT * FROM products ORDER BY name").fetchall()
    warehouses = db.execute("SELECT * FROM warehouses ORDER BY name").fetchall()
    history = db.execute(
        """SELECT l.*, p.name as product_name, w.name as warehouse_name FROM ledger l
           JOIN products p ON p.id = l.product_id
           LEFT JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.action_type = 'receipt' ORDER BY l.id DESC LIMIT 15"""
    ).fetchall()
    return render_template("receipts.html", products=products_list, warehouses=warehouses, history=history)


# ---------- Delivery Orders (stock OUT) ----------

@app.route("/deliveries", methods=["GET", "POST"])
@login_required
def deliveries():
    db = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        warehouse_id = int(request.form["warehouse_id"])
        qty = int(request.form["quantity"])
        note = request.form.get("note", "")
        row = get_or_create_stock_row(db, product_id, warehouse_id)
        if row["quantity"] < qty:
            flash(f"Not enough stock — only {row['quantity']} available.")
            return redirect(url_for("deliveries"))
        db.execute("UPDATE stock SET quantity = ? WHERE id = ?", (row["quantity"] - qty, row["id"]))
        log_ledger(db, "delivery", product_id, warehouse_id, None, -qty, note)
        db.commit()
        flash("Delivery recorded — stock decreased.")
        return redirect(url_for("deliveries"))

    products_list = db.execute("SELECT * FROM products ORDER BY name").fetchall()
    warehouses = db.execute("SELECT * FROM warehouses ORDER BY name").fetchall()
    history = db.execute(
        """SELECT l.*, p.name as product_name, w.name as warehouse_name FROM ledger l
           JOIN products p ON p.id = l.product_id
           LEFT JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.action_type = 'delivery' ORDER BY l.id DESC LIMIT 15"""
    ).fetchall()
    return render_template("deliveries.html", products=products_list, warehouses=warehouses, history=history)


# ---------- Internal Transfers ----------

@app.route("/transfers", methods=["GET", "POST"])
@login_required
def transfers():
    db = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        from_id = int(request.form["from_warehouse_id"])
        to_id = int(request.form["to_warehouse_id"])
        qty = int(request.form["quantity"])
        note = request.form.get("note", "")
        if from_id == to_id:
            flash("Source and destination can't be the same warehouse.")
            return redirect(url_for("transfers"))
        from_row = get_or_create_stock_row(db, product_id, from_id)
        if from_row["quantity"] < qty:
            flash(f"Not enough stock at source — only {from_row['quantity']} available.")
            return redirect(url_for("transfers"))
        to_row = get_or_create_stock_row(db, product_id, to_id)
        db.execute("UPDATE stock SET quantity = ? WHERE id = ?", (from_row["quantity"] - qty, from_row["id"]))
        db.execute("UPDATE stock SET quantity = ? WHERE id = ?", (to_row["quantity"] + qty, to_row["id"]))
        log_ledger(db, "transfer", product_id, from_id, to_id, qty, note)
        db.commit()
        flash("Transfer recorded — total stock unchanged, location updated.")
        return redirect(url_for("transfers"))

    products_list = db.execute("SELECT * FROM products ORDER BY name").fetchall()
    warehouses = db.execute("SELECT * FROM warehouses ORDER BY name").fetchall()
    history = db.execute(
        """SELECT l.*, p.name as product_name, w1.name as from_name, w2.name as to_name
           FROM ledger l JOIN products p ON p.id = l.product_id
           LEFT JOIN warehouses w1 ON w1.id = l.warehouse_id
           LEFT JOIN warehouses w2 ON w2.id = l.to_warehouse_id
           WHERE l.action_type = 'transfer' ORDER BY l.id DESC LIMIT 15"""
    ).fetchall()
    return render_template("transfers.html", products=products_list, warehouses=warehouses, history=history)


# ---------- Stock Adjustments ----------

@app.route("/adjustments", methods=["GET", "POST"])
@login_required
def adjustments():
    db = get_db()
    if request.method == "POST":
        product_id = int(request.form["product_id"])
        warehouse_id = int(request.form["warehouse_id"])
        counted_qty = int(request.form["counted_quantity"])
        note = request.form.get("note", "")
        row = get_or_create_stock_row(db, product_id, warehouse_id)
        diff = counted_qty - row["quantity"]
        db.execute("UPDATE stock SET quantity = ? WHERE id = ?", (counted_qty, row["id"]))
        log_ledger(db, "adjustment", product_id, warehouse_id, None, diff, note)
        db.commit()
        flash(f"Adjustment recorded — stock corrected by {diff:+d}.")
        return redirect(url_for("adjustments"))

    products_list = db.execute("SELECT * FROM products ORDER BY name").fetchall()
    warehouses = db.execute("SELECT * FROM warehouses ORDER BY name").fetchall()
    history = db.execute(
        """SELECT l.*, p.name as product_name, w.name as warehouse_name FROM ledger l
           JOIN products p ON p.id = l.product_id
           LEFT JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.action_type = 'adjustment' ORDER BY l.id DESC LIMIT 15"""
    ).fetchall()
    return render_template("adjustments.html", products=products_list, warehouses=warehouses, history=history)


# ---------- Full Ledger ----------

@app.route("/ledger")
@login_required
def ledger():
    db = get_db()
    rows = db.execute(
        """SELECT l.*, p.name as product_name, w1.name as from_name, w2.name as to_name
           FROM ledger l JOIN products p ON p.id = l.product_id
           LEFT JOIN warehouses w1 ON w1.id = l.warehouse_id
           LEFT JOIN warehouses w2 ON w2.id = l.to_warehouse_id
           ORDER BY l.id DESC"""
    ).fetchall()
    return render_template("ledger.html", rows=rows)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
