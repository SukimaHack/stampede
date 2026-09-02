# Zero-downtime migrations

The governing assumption: **during a rolling deploy, old code and new code run against the same database at the same time.** A migration is safe only if both versions of the application keep working while it runs and after it finishes.

CI never catches these. CI migrates an empty database with no concurrent traffic.

## The two locks that cause outages

Postgres terminology, but the shape is the same elsewhere.

- **ACCESS EXCLUSIVE** - blocks every read and write on the table. Taken by `ALTER TABLE` for most changes, and by a plain `CREATE INDEX`. On a large, busy table this is an outage.
- **Lock queueing** - a blocked ACCESS EXCLUSIVE request also blocks every query that arrives behind it. A migration waiting on one long-running query can stall the whole table within seconds.

This is why a migration that runs in 20 ms in staging can take production down: the danger is not duration, it is what the lock blocks while it waits.

Set a lock timeout so a migration fails fast instead of queueing traffic behind it:

```python
class Migration(migrations.Migration):
    atomic = False
    operations = [
        migrations.RunSQL("SET lock_timeout = '3s';", reverse_sql=migrations.RunSQL.noop),
        ...
    ]
```

## Change-by-change

### Adding a column

Adding a **nullable** column with no default is cheap on modern Postgres - metadata only.

Adding `NOT NULL` needs a default. On Postgres 11+ a *constant* default is also metadata-only. A **non-constant** default (`now()`, a callable, `uuid4`) rewrites the entire table under ACCESS EXCLUSIVE.

The safe general sequence, across three deploys:

1. Add the column nullable. Deploy. Old code ignores it; new code writes it.
2. Backfill in batches (separate migration or management command).
3. Add `NOT NULL` once no rows are null.

Never collapse this into one migration on a table you cannot afford to lock.

### Removing a column

Removing it in the same deploy that stops using it still breaks: old instances are alive during rollout and still `SELECT` every column.

1. Deploy code that never references the field. Add it to the model's `Meta` as needed or remove it from the model with `SeparateDatabaseAndState` so Django stops selecting it.
2. Drop the column in the next release.

### Renaming a column

There is no safe in-place rename under rolling deploy. Treat it as add + backfill + dual-write + remove:

1. Add the new column.
2. Deploy code that writes both, reads the old.
3. Backfill.
4. Deploy code that reads the new.
5. Drop the old.

If the rename is not worth five deploys, it is not worth doing.

### Adding an index

A plain `CREATE INDEX` locks writes for the duration. Use the concurrent form, which cannot run inside a transaction:

```python
from django.contrib.postgres.operations import AddIndexConcurrently

class Migration(migrations.Migration):
    atomic = False          # required
    operations = [
        AddIndexConcurrently("order", models.Index(fields=["customer", "created"], name="order_cust_created_idx")),
    ]
```

`CREATE INDEX CONCURRENTLY` can fail partway and leave an **invalid index** behind that still costs write overhead and is not used for reads. A migration that adds one should be paired with a check that the index is valid.

`db_index=True` on a field generates the blocking form. On a large table, prefer an explicit `AddIndexConcurrently`.

### Adding a constraint

`ALTER TABLE ... ADD CONSTRAINT` validates every existing row while holding the lock. Split it:

```sql
ALTER TABLE t ADD CONSTRAINT c CHECK (...) NOT VALID;   -- fast, takes a brief lock
ALTER TABLE t VALIDATE CONSTRAINT c;                    -- slow, but only takes SHARE UPDATE EXCLUSIVE
```

The same applies to foreign keys.

### Changing a column type

Most type changes rewrite the table. Widening `varchar(n)` to a larger `n`, or to `text`, is metadata-only on Postgres. Almost everything else is not. For a real type change, use the add/backfill/swap dance rather than `AlterField`.

## Data migrations

```python
def backfill(apps, schema_editor):
    Order = apps.get_model("myapp", "Order")   # historical model, not an import
    qs = Order.objects.filter(status__isnull=True)
    while True:
        batch = list(qs[:1000])
        if not batch:
            break
        for o in batch:
            o.status = "pending"
        Order.objects.bulk_update(batch, ["status"])
```

Review points:

- **`apps.get_model`, never a module-level import.** An imported model reflects today's schema, not the schema at this point in migration history. The migration then breaks when replayed on a fresh database.
- **Historical models have no custom methods or managers.** Only fields. Code calling `order.calculate_total()` inside `RunPython` will fail on replay.
- **Batch it.** A single `.update()` over millions of rows holds one long transaction.
- **`atomic = False`** for long backfills, so a failure does not roll back hours of work and a lock is not held throughout.
- **`reverse_code`.** Absent means irreversible. If that is intended, say so explicitly with `migrations.RunPython.noop`.
- **Separate from schema changes.** A schema change plus a long backfill in one migration means the schema lock is held for the whole backfill.

## Reviewing a migration file

1. Which operations take ACCESS EXCLUSIVE, and on which table?
2. What is the row count of that table in production? If unknown, say so and give the threshold.
3. Does old code survive this schema during rollout?
4. Does new code survive the *old* schema, in case of rollback?
5. Is there a `RunPython`? Does it use `apps.get_model`, batch, and declare reverse behaviour?
6. Is `atomic = False` set where required, and *only* where required?
