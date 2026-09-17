---
type: capability
title: Coding guidance
category: coding-guidance
topic: lakehouse
community: frontier-data-engineer
capabilities: [LH-C02, LH-C08, LH-C09, LH-C12]
updated: 2026-09-16
fabric_release: 2026-09
status: current
evidence: mixed
sources:
  - 2_raw/gym-rep-reports/lakehouse/2026-09-16-AG-LAK-001-foundry.md
---

# Lakehouse — coding guidance

The call shapes. Traps that bite are in [gotchas](gotchas.md); the order they go in is [build-framework](build-framework.md).

## Notebook payload

```python
from notebook_client import NotebookClient, notebook_content_py
py = notebook_content_py([
    ("markdown", "Title"),                # (kind, text) tuple = markdown cell
    "CONFIG = ...",                       # plain string     = code cell
    "df = spark.read...",
], parameters_cell=1)                     # tags cell 1's delimiter as PARAMETERS CELL
nb = NotebookClient(ws).create(name, py_source=py, folder_id=folder_id)
src = NotebookClient(ws).get_source(nb["id"])         # assert on THIS, not on create's status
```

Parameters inject only into a cell whose *delimiter* is `# PARAMETERS CELL ***`. An untagged notebook runs silently on its defaults — so give defaults that raise rather than defaults that work.

- The delimiter grammar — `# Fabric notebook source` header, notebook-level METADATA block, then a CELL block per cell each followed by its own METADATA block — is what the notebook editor round-trips; get it wrong and the item is created but mis-parsed.
- `parameters_cell` must name a **code** cell. The tag is carried by the delimiter, not the METADATA block, and survives create → getDefinition.
- `update_definition()` sends the `.platform` part only with `?updateMetadata=true` — supply a display name to update item metadata; omit it and the item's metadata is left alone.

**Confirmed** (AG-LAK-001): one markdown cell + one code cell, no parameters cell, built via
`notebook_content_py()` — offline `--lint-source` matched the live `get_source()` check
exactly. Atom: [LH-C08](atoms/LH-C08.md).

## Run submission

```python
NotebookClient(ws).run_and_wait(nb_id,
    parameters={"env": {"value": "prod", "type": "string"}},   # injects into the tagged cell
    default_lakehouse={"name": lh_name, "id": lh_id, "workspaceId": ws},
    configuration={"environment": {"id": env_id, "name": env_name}})
```

- **Parameter wire shape is `{"name": {"value": v, "type": "string"}}`** — they land only in the tagged parameters cell.
- **`defaultLakehouse` is what makes relative paths resolve.** Omit it and a notebook that relies on an attached lakehouse fails at run time even though it runs fine interactively. Attach at submission, never in the item definition ([gotchas](gotchas.md)).
- **The `environment` block is the only route to a third-party library on an unattended run** — `%pip` is rejected on a Jobs-API submission ([gotchas](gotchas.md)). `EnvironmentClient.run_configuration()` builds it.

## Environment item — the third-party-package route

```python
env = EnvironmentClient(ws).ensure("My_Env", folder_id=fid)      # create is 201, synchronous
EnvironmentClient(ws).stage_public_libraries(env["id"], ENV_YML)  # multipart environment.yml, not JSON
EnvironmentClient(ws).publish(env["id"])
EnvironmentClient(ws).wait_published(env["id"])                   # ~300 s in practice
```

- **Staged libraries are inert until `publish()`.**
- **Publish is not a Location-header LRO.** It returns immediately; poll `properties.publishDetails.state` (`Running` → `Success`/`Failed`/`Cancelled`).
- **Never re-create an existing environment** — a second one with the same purpose orphans the first one's published libraries. `ensure()` is create-or-get.
- Display names take `^\w+$`, like a lakehouse — and a rejected create still reserves the name ([gotchas](gotchas.md)).

## Shortcuts

```python
ShortcutClient(ws).create_onelake(item_id, name, "Files/raw",
                                  target_item_id, "Files/exports",
                                  conflict_policy="CreateOrOverwrite")
```

- **Both paths must begin with `Files` or `Tables`** — the two OneLake roots; the rule holds for the shortcut's own path and for the path inside the target item.
- **Create is synchronous** for a OneLake target: `201` created-new, `200` when `CreateOrOverwrite` replaced one — the status code is the only signal a re-run changed nothing. No LRO; `Location` is the shortcut's own resource URL, not an operation to poll.
- **The target is a single-key object naming the store**: `oneLake` · `adlsGen2` · `amazonS3` · `googleCloudStorage` · `s3Compatible` · `dataverse` · `externalDataShare` · `azureBlobStorage` · `oneDriveSharePoint`. Only `oneLake` needs no Fabric **connection object** — every external kind takes a `connectionId` that must exist first, which makes a OneLake-to-OneLake reference the cheapest way to exercise the boundary.
- **`shortcutConflictPolicy`** (query param): `Abort` (default — a second run fails `409`, verified) · `GenerateUniqueName` · `CreateOrOverwrite` (verified, `200`) · `OverwriteOnly` (unverified). A re-runnable build passes `CreateOrOverwrite` rather than deleting first.
- A shortcut may carry a **`transform`** (`csvToDelta`), projecting a CSV source as Delta at the shortcut boundary with no producer at all.
- Service principals and managed identities are supported — drivable unattended. Required scope: `OneLake.ReadWrite.All`. 429s carry `Retry-After`.

## T-SQL over pyodbc

`LakehouseClient.sql_endpoint_connection(server, endpoint_id)` encodes the whole shape:

- `server` is `sqlEndpointProperties.connectionString` with `,1433` appended; `Database` is `sqlEndpointProperties.id` — **you connect by endpoint GUID and land in a database named after the lakehouse**.
- Token audience is `https://database.windows.net/` (inside a Fabric notebook: `notebookutils.credentials.getToken("sql")`).
- Requires "ODBC Driver 18 for SQL Server" — the driver Fabric's own runtime carries. 18 defaults to `Encrypt=yes` and the endpoint's certificate is valid, so `TrustServerCertificate` stays off.
- The token is passed as `attrs_before={1256: …}` — `SQL_COPT_SS_ACCESS_TOKEN`, whose value is the UTF-16-LE token prefixed with its own length as a little-endian int32.

## Addressing by four-part name

```python
P = f"`{ws_name}`.{lh_name}."               # display names; workspace segment backticked
spark.sql(f"SELECT COUNT(*) FROM {P}core.instrument")
spark.sql(f"CREATE TABLE IF NOT EXISTS {P}core.t ({ddl}) USING DELTA")   # omit LOCATION
```

- **Display names on the leading segments; GUIDs are rejected** — the inverse of every other Fabric surface.
- **Backtick the workspace segment**: workspace names take hyphens and Spark's parser does not.
- A four-part name **cannot live in a `%%sql` cell**, because the workspace segment is always a variable. Compose it in Python.
- `SHOW TABLES` returns a **three-part** `namespace` (`workspace.lakehouse.schema`) — do not parse it as two.

## Addressing by absolute ABFS path

```python
ROOT  = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}"
TABLE = f"{ROOT}/Tables/{schema}/{table}"      # no trailing slash
FILE  = f"{ROOT}/Files/incoming/{name}.csv"
df.write.format("delta").mode("append").save(TABLE)
DeltaTable.forPath(spark, TABLE)               # takes the ABFS path directly
```

- **`.save(path)`, never `saveAsTable(name)`** — `saveAsTable` addresses the catalog, so it resolves against the attached default lakehouse, and an unqualified name lands in `dbo`.
- **`Tables/{schema}/{table}` auto-registers on a schema-enabled lakehouse** — no `CREATE SCHEMA`, no `CREATE TABLE … LOCATION`, no attachment. The directory *is* the schema.
- **Existence test is `DeltaTable.isDeltaTable(spark, path)`**, not `catalog.tableExists` — with no attachment there is no catalog to ask.
- **Convert the item API's `https://onelake.dfs…` form once at the boundary** (`LakehouseClient.abfss_paths()`); it is not Spark-writable, and the raw property must never reach Spark.

**Confirmed** (AG-LAK-001): `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Tables/retail/product`
written via `.save()`, auto-registered, no `CREATE SCHEMA`. Atom: [LH-C02](atoms/LH-C02.md).

## Livy — Spark without a notebook

```python
session = LivyClient(ws, lakehouse_id); session.create_session()          # ~40s, then seconds/stmt
out = session.execute("import pyodbc; print(pyodbc.drivers())", kind="pyspark")
session.close_session()
```

**Pass `kind="pyspark"` explicitly** — the default is `kind="spark"`, which is Livy's name for the Scala interpreter (the client's signature shows `spark`; that *is* Scala), and Python submitted there fails with an error naming neither Livy, Python, nor the kind. Use Livy to probe an unknown surface (inline stdout, no artefact left behind) and a notebook item to ship the re-runnable job.

## Landing a verdict

The job instance has no exit value, so write the outcome as data:

```python
notebookutils.fs.put(f"{ROOT}/Files/_diag/{run_id}.json", json.dumps(result), True)
```
