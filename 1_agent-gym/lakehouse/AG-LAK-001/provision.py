"""
AG-LAK-001 (Foundry) — source-system fixture.

Writes the retailer's product extract to `source-data/product.csv`. Deterministic: the same
eight rows, byte for byte, on every run, because `validate.json`'s expected values are
computed from them.

This task's fixture stays OUTSIDE Fabric on purpose. The lakehouse does not exist yet when
you run this — creating it is the agent's first job. The build phase uploads this file into
the lakehouse's `Files/raw/` area over the OneLake DFS endpoint, then reads it from there.

Run:  python provision.py          (from this folder)
Then: /execute-gym-rep AG-LAK-001
"""
from pathlib import Path

OUT_DIR = Path(__file__).parent / "source-data"

# sku, name, category, unit_price, in_stock
PRODUCTS = [
    ("FS-1001", "Ball-pein hammer", "Tools", "18.50", "42"),
    ("FS-1002", "Claw hammer", "Tools", "16.75", "30"),
    ("FS-1003", "Socket set 24pc", "Tools", "64.00", "12"),
    ("FS-2001", "Safety goggles", "Safety", "9.25", "120"),
    ("FS-2002", "Work gloves L", "Safety", "12.40", "85"),
    ("FS-2003", "Hi-vis vest", "Safety", "14.00", "60"),
    ("FS-3001", "Steel tape 8m", "Measuring", "11.95", "55"),
    ("FS-3002", "Digital caliper", "Measuring", "39.99", "8"),
]
HEADER = ("sku", "name", "category", "unit_price", "in_stock")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    target = OUT_DIR / "product.csv"
    lines = [",".join(HEADER)] + [",".join(row) for row in PRODUCTS]
    # newline="\n" explicitly: a CRLF file read by Spark on Linux executors leaves a stray
    # \r on the last column of every row, which turns in_stock into a string silently.
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    total_price = sum(float(row[3]) for row in PRODUCTS)
    total_stock = sum(int(row[4]) for row in PRODUCTS)
    print(f"wrote {target} ({len(PRODUCTS)} products)")
    print(f"  categories        : {len({row[2] for row in PRODUCTS})}")
    print(f"  SUM(unit_price)   : {total_price:.2f}")
    print(f"  SUM(in_stock)     : {total_stock}")
    print("\nNext: create the lakehouse, upload this file to Files/raw/product.csv, "
          "and build the notebook. See task.md.")


if __name__ == "__main__":
    main()
