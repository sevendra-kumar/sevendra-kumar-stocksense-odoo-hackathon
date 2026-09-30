# StockSense — Inventory Management System

A modular inventory system for the Odoo Hackathon problem statement: track products, receipts, deliveries, internal transfers, and stock adjustments, with every movement logged to an audit ledger.

## Run it locally

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`, click "Create an account," then log in.

## Deploy to Render (or any host that uses gunicorn)

This project already includes everything Render needs — you should NOT need to touch these:

- `requirements.txt` includes `gunicorn`, the production server Render uses to run Flask apps.
- `Procfile` tells Render the exact start command: `gunicorn app:app`.
- `app.py` calls `init_db()` at the top level (not inside `if __name__ == "__main__":`), so your database tables get created no matter how the app is started — this was the bug that broke the previous deploy.

Steps:
1. Push this whole folder to a GitHub repo (all files, including `templates/` and `static/`).
2. On Render: New → Web Service → connect your repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: leave blank (Render reads it from the `Procfile`), or set it explicitly to `gunicorn app:app`.
5. Deploy. Open the live URL, sign up, and test.

## How the data model works

Five tables:
- `users` — login accounts
- `warehouses` — physical locations (seeded with "Main Warehouse" and "Production Floor")
- `categories` — product groupings
- `products` — name, SKU, unit of measure, reorder level
- `stock` — current quantity of a product at a warehouse (this is the live snapshot)
- `ledger` — every stock movement ever made (this is the permanent history — never edited, only appended to)

## The four actions

| Action | Effect on `stock` | Effect on `ledger` |
|---|---|---|
| Receipt | increases | `+qty` |
| Delivery | decreases (blocked if insufficient) | `-qty` |
| Transfer | moves between warehouses, total unchanged | records both source and destination |
| Adjustment | set to the counted amount | records the difference |

## Judge Q&A cheat sheet

**Why SQLite, not Postgres/MySQL?** No separate database server to install — the whole app runs from one file, which matters for a live demo and for judges trying to run your code themselves.

**Why is the ledger a separate table from stock?** `stock` only answers "how much right now" and gets overwritten on every change. `ledger` answers "how did we get here" and is append-only — it's the audit trail.

**Why block negative stock on deliveries/transfers but not show a hard database-level constraint?** The check happens in the application logic (`if row["quantity"] < qty`) before the update runs, so a failed delivery never touches the database at all — simpler to reason about and to explain than a database trigger.

**What would you add with more time?** Multi-user roles (admin vs. staff), password hashing (currently plain-text for demo simplicity), barcode/QR lookup for products, and CSV export of the ledger.
