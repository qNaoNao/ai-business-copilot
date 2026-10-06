"""Reproducible synthetic fixture, never overwrites the private business DB."""
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / ".demo" / "business.db"
SKU = "00000000000000000000000000000001"


def create_demo_database(path: Path = DEMO_PATH) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE customers(customer_id TEXT PRIMARY KEY, customer_unique_id TEXT);
        CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT, order_status TEXT, order_purchase_timestamp TEXT);
        CREATE TABLE products(product_id TEXT PRIMARY KEY, product_category_name TEXT, product_category_name_english TEXT);
        CREATE TABLE order_items(order_id TEXT, order_item_id INTEGER, product_id TEXT, price REAL, freight_value REAL);
        CREATE TABLE order_financials(order_id TEXT PRIMARY KEY, product_value REAL, item_count INTEGER, freight_value REAL);
        """)
        products = [(SKU, "stationery", "stationery"), ("00000000000000000000000000000002", "stationery", "stationery"),
                    ("00000000000000000000000000000003", "computers", "computers")]
        conn.executemany("INSERT INTO products VALUES(?,?,?)", products)
        # January's declining SKU stops after the third week; February retains another SKU.
        sales = [("2018-01-03", 0, 100, 2), ("2018-01-10", 0, 100, 3), ("2018-01-17", 0, 100, 1),
                 ("2018-01-20", 1, 60, 1), ("2018-02-06", 1, 50, 1), ("2018-02-28", 1, 50, 1),
                 ("2018-01-09", 2, 200, 1), ("2018-02-10", 2, 250, 2)]
        for i, (date, product, price, count) in enumerate(sales):
            order_id, customer_id = f"order-{i}", f"customer-{i}"
            conn.execute("INSERT INTO customers VALUES(?,?)", (customer_id, f"person-{i // 2}"))
            conn.execute("INSERT INTO orders VALUES(?,?,?,?)", (order_id, customer_id, "delivered", date + " 12:00:00"))
            conn.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)",
                             [(order_id, j + 1, products[product][0], price, 10) for j in range(count)])
        conn.execute("INSERT INTO orders VALUES('cancelled','customer-0','cancelled','2018-02-03 12:00:00')")
        conn.execute("INSERT INTO order_items VALUES('cancelled',1,?,99999,10)", (SKU,))
        conn.execute("INSERT INTO order_financials SELECT order_id,SUM(price),COUNT(*),SUM(freight_value) FROM order_items GROUP BY order_id")
    return path


if __name__ == "__main__":
    print(create_demo_database())
