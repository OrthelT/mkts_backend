# Turso: joining a subquery on a mismatched-affinity key causes O(N×M) re-execution

Status: draft notes for a turso-core issue. Found 2026-07-15 while profiling
`mkts-backend update-markets` (the market-stats query took ~2 minutes on turso
vs 0.27s on stock SQLite, same file, same SQL).

## Summary

When a query joins a table onto a grouped subquery, and the join keys have
**different type affinities** (e.g. an `INTEGER` column joined against a
subquery grouped on a `VARCHAR` column), turso appears to abandon its normal
join strategy and re-executes the aggregation subquery for every row of the
outer table. Runtime becomes outer-rows × inner-rows instead of
outer-rows + inner-rows.

Stock SQLite handles the same query by materializing the subquery once
(`MATERIALIZE` in `EXPLAIN QUERY PLAN`) and applying numeric affinity
conversion to the comparison, so it stays fast.

When the join keys have the **same** affinity (INTEGER=INTEGER or TEXT=TEXT),
turso is fast too — the problem is specifically the mismatch.

## Measurements

Real-world query (2,354-row watchlist LEFT JOINed onto an aggregation over a
797,817-row history table whose key column is `VARCHAR(10)`):

| Engine | Time |
|---|---|
| stdlib `sqlite3` | 0.27 s |
| turso (pyturso) | 117 s |

Rewriting with a CTE (`WITH h AS (...)`) does not help — same ~117 s, so CTEs
are inlined the same way. Running the aggregation as a standalone query takes
0.06 s, confirming the aggregation itself is cheap and the blowup comes from
per-row re-execution.

Toy reproduction (1,000 outer rows, 500,000 history rows):

| Join keys | sqlite3 | turso |
|---|---|---|
| INTEGER = INTEGER | 0.14 s | 0.38 s |
| INTEGER = VARCHAR | 0.22 s | **>300 s (killed)** |
| VARCHAR = VARCHAR | fast | 0.45 s |

Adding `CAST(type_id AS INTEGER)` inside the subquery of the real-world query
(so both join keys are INTEGER) brings turso from 117 s to **0.10 s**.

## Steps to reproduce

```python
import os, random, sqlite3
from time import perf_counter
import turso

DB = "subquery_repro.db"
if os.path.exists(DB):
    os.remove(DB)

random.seed(42)
conn = sqlite3.connect(DB)
conn.execute("CREATE TABLE items (item_id INTEGER PRIMARY KEY)")
conn.execute("CREATE TABLE hist_int (item_id INTEGER, value REAL)")
conn.execute("CREATE TABLE hist_txt (item_id VARCHAR(10), value REAL)")
conn.executemany("INSERT INTO items VALUES (?)", [(i,) for i in range(1000)])
data = [(random.randrange(1000), random.random()) for _ in range(500_000)]
conn.executemany("INSERT INTO hist_int VALUES (?, ?)", data)
conn.executemany("INSERT INTO hist_txt VALUES (?, ?)", [(str(a), b) for a, b in data])
conn.commit()
conn.close()

QUERY = """
SELECT i.item_id, h.avg_value
FROM items i
LEFT JOIN (
    SELECT item_id, AVG(value) AS avg_value
    FROM {table}
    GROUP BY item_id
) AS h ON i.item_id = h.item_id
"""

for engine_name, connect in (("sqlite3", sqlite3.connect), ("turso", turso.connect)):
    for table in ("hist_int", "hist_txt"):
        c = connect(DB)
        t = perf_counter()
        rows = c.execute(QUERY.format(table=table)).fetchall()
        print(f"{engine_name} {table}: {perf_counter() - t:.2f}s ({len(rows)} rows)")
        c.close()
```

Expected: all four combinations complete in well under a second (sqlite3 does).
Actual: `turso hist_txt` takes minutes — cost scales with
outer_rows × inner_rows. Cut both row counts by 10× to make it finish if
needed; the asymmetry is obvious at any size.

## Why SQLite is fast

`EXPLAIN QUERY PLAN` on the real query in SQLite:

```
MATERIALIZE h
  SEARCH market_history ...
  USE TEMP B-TREE FOR GROUP BY
SCAN w
SEARCH h USING AUTOMATIC COVERING INDEX (type_id=?) LEFT-JOIN
```

SQLite materializes the subquery once and builds an automatic index over the
result; the cross-affinity comparison is handled by applying numeric affinity
to the text operand (SQLite comparison-affinity rules), so the index lookup
still works.

## Impact / workaround

Any ORM- or hand-written query that joins across a schema's INTEGER/VARCHAR
key inconsistency silently degrades from sub-second to minutes as tables grow.
Workarounds until turso materializes subqueries (or coerces affinity before
choosing a join strategy):

- `CAST` the mismatched key inside the subquery so both sides share affinity
  (what `mkts_backend.processing.data_processing.calculate_market_stats` now
  does — a one-line change restored full speed), or
- run the subqueries as standalone statements and join client-side.

## Follow-up ideas (turso-dev hat)

- Check whether the join planner has a hash/materialize path that bails on
  affinity mismatch and falls back to re-evaluating the RHS per outer row.
- Even without materialization, a nested-loop over an *aggregated* subquery
  should arguably cache the aggregation result — it is invariant across outer rows.
- Compare `EXPLAIN` output between turso and SQLite for the toy query.
