---
task_id: AG-LAK-001
---

# Task 01: Foundry

Difficulty: starter.

## Tasks (and validation)

1. Run `python provision.py` (local) — it writes `source-data/product.csv`.

2. Create a schema-enabled lakehouse named `AG_LAK_001_Foundry` (default schema `dbo`) in the workspace folder `Lakehouse`, with its SQL endpoint provisioned. Validate: [lakehouse-provisioned]

3. Create a notebook named `AG-LAK-001-Foundry` in the workspace folder `Lakehouse`. Validate: [notebook-exists]

4. The notebook creates a table:
   - schema: `retail`
   - table name: `product`
   - columns: `sku`, `name`, `category`, `unit_price`, `in_stock`
   Validate: [table-in-retail-schema, table-not-in-dbo]

5. Load the data from `source-data/product.csv` into `retail.product`, running the notebook successfully. Validate: [notebook-ran-green, q1-row-count, q2-category-count, q3-stock-total, q4-price-total, q5-known-row]
