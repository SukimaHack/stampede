---
name: stampede
description: Review Django code for the production failures that agent-written code reliably introduces - N+1 query explosions, unbounded querysets, deploy-breaking migrations, lost-update races, and tests that assert nothing. Use when reviewing, auditing, or before merging any Django diff, especially code an AI agent wrote. Triggers on Django models, views, serializers, querysets, migrations, or when asked why something is slow.
---

# stampede

One line of Django can fire a thousand queries. The line looks fine. The tests pass. The database melts under load.

This skill reviews Django code for failures that **only appear at scale or on deploy** - the class of bug that survives code review and test suites because nothing about the source text looks wrong.

## Why this skill exists

Agent-written Django is idiomatic, readable, and passes its tests. It fails differently from human-written Django:

- A human writes `for order in orders: print(order.customer.name)` and *knows* to check the query count, because they remember getting burned.
- An agent writes the same line having pattern-matched thousands of tutorials where `orders` had three rows.

The ORM's laziness is the trap. **Nothing at the call site indicates cost.** `order.customer` is an attribute access that may be a network round trip. Review that reads for correctness will not catch it - cost has to be a separate pass.

## How to run a review

Do these in order. Report at the end; do not fix unless asked.

**1. Establish the loop boundaries.** Find every `for` loop, comprehension, template `{% for %}`, and serializer with `many=True`. For each, list the model attributes touched in the body. Any attribute crossing a relation (`.author`, `.items.all()`, `.profile`) is a candidate query per iteration.

**2. Trace each queryset from creation to evaluation.** Note where it is built, what `select_related` / `prefetch_related` it carries, and where it is first evaluated. A queryset built in a view and evaluated in a template has crossed a boundary where those annotations are easy to lose.

**3. Check the four cost classes below**, in order - they are ranked by how often they take production down.

**4. Report findings** as: location, what fires, how many times, and the fix. Quantify. "N+1 in the loop" is weak; "one query per order, ~500 orders per dashboard load" is actionable.

---

## Class 1 - Query explosion

The dominant failure. Look for these specifically.

### Relation access inside iteration

```python
# 1 + N queries
for order in Order.objects.all():
    print(order.customer.name)

# 1 query
for order in Order.objects.select_related("customer"):
    print(order.customer.name)
```

**`select_related` vs `prefetch_related` is not a style choice.** The wrong one silently does nothing useful:

- `select_related` - SQL JOIN. **Only** ForeignKey and OneToOne, forward direction.
- `prefetch_related` - second query, joined in Python. Required for ManyToMany and every reverse relation (`order.items.all()`).

`select_related` on a reverse relation raises. `prefetch_related` on a forward FK works but costs an extra query. Verify the direction and cardinality of every relation named.

### Filtering inside a prefetch

The subtle one. A filtered related manager **discards the prefetch and re-queries per row**:

```python
# prefetch wasted - .filter() re-queries for every order
orders = Order.objects.prefetch_related("items")
for o in orders:
    for i in o.items.filter(active=True):   # N queries
        ...

# prefetch the filtered set explicitly
from django.db.models import Prefetch
orders = Order.objects.prefetch_related(
    Prefetch("items", queryset=Item.objects.filter(active=True), to_attr="active_items")
)
```

Flag any `.filter()`, `.exclude()`, `.order_by()`, or slicing applied to a prefetched manager inside a loop.

### Counting and existence

```python
qs.count()          # SELECT COUNT(*) - cheap, but a query every call
len(qs)             # loads every row into memory
if qs:              # loads every row
if qs.exists():     # SELECT 1 ... LIMIT 1
```

If the queryset is **already evaluated**, `len(qs)` is free and `qs.count()` fires a redundant query - the correct choice inverts. Check whether evaluation has already happened before recommending either.

### Aggregation done in Python

```python
total = sum(o.amount for o in Order.objects.all())             # loads every row
total = Order.objects.aggregate(Sum("amount"))["amount__sum"]  # one query, no rows
```

Flag any `sum()`, `max()`, `min()`, or manual counting over a queryset.

### Unbounded result sets

`Model.objects.all()` with no filter, no slice, and no pagination is a production incident waiting for the table to grow. It passes every test, because the fixture has four rows.

- API list endpoints without pagination
- `.all()` handed to a serializer or template
- Large exports without `.iterator(chunk_size=...)`

### Repeated evaluation

```python
qs = Order.objects.filter(active=True)
if qs.exists():        # query 1
    total = qs.count() # query 2
    for o in qs:       # query 3
        ...
```

A queryset caches results only after full evaluation by iteration. `exists()` and `count()` do not populate that cache. Slicing (`qs[:10]`) creates a **new** queryset that does not reuse the parent's cache.

---

## Class 2 - Migrations that break the deploy

These do not fail in CI. CI runs migrations against an empty database with no traffic.

**Assume a rolling deploy: old code and new code hit the same database simultaneously.** Every schema change must be safe for both.

| Change | Why it breaks | Safe form |
|---|---|---|
| Add `NOT NULL` column, no default | Rejected when rows exist | Add nullable, backfill in batches, set `NOT NULL` in a *later* deploy |
| Drop or rename a column | Old code still selects it during rollout | Ship code that stops using it, drop in the next release |
| `db_index=True` on a big table | `CREATE INDEX` takes an ACCESS EXCLUSIVE lock | `AddIndexConcurrently` with `atomic = False` (Postgres) |
| Schema change plus data migration in one file | Long transaction holds locks | Separate migrations |
| `RunPython` loading all rows | OOM on large tables | `.iterator()` plus `bulk_update` in batches |

Check every `RunPython`:

```python
# wrong - imports the *current* model, which may not match this point in history
from myapp.models import Order

def backfill(apps, schema_editor):
    Order.objects.update(...)

# right - the historical model as it existed at this migration
def backfill(apps, schema_editor):
    Order = apps.get_model("myapp", "Order")
```

A `RunPython` with no `reverse_code` is irreversible. Sometimes correct, but it should be an explicit `migrations.RunPython.noop`, not absent by accident.

---

## Class 3 - Races and lost updates

Agent code reads, computes in Python, and writes back. Under concurrency that loses writes.

```python
# lost update: two requests both read 5, both write 6
account.balance = account.balance + 1
account.save()

# atomic in the database
Account.objects.filter(pk=pk).update(balance=F("balance") + 1)
```

Check for:

- **Read-modify-write on a shared row** without `select_for_update()` inside `transaction.atomic()`.
- **`get_or_create` / `update_or_create` treated as atomic.** Without a unique constraint on the lookup fields, concurrent callers can both create. The constraint is what makes it safe; the method only catches the resulting `IntegrityError`.
- **Side effects inside `transaction.atomic()`.** Emails, webhooks, queue jobs, and payment calls fire even if the transaction later rolls back. Move them to `transaction.on_commit(...)`.
- **`.update()` and `bulk_create()` skip `save()` and `pre_save`/`post_save`.** Flag this both directions: bulk operations that skip logic they needed, *and* business logic buried in `save()` or a signal where the next reader will not find it.

---

## Class 4 - Tests that prove nothing

An agent asked for tests produces tests that pass. That is a different goal from tests that fail when the code is wrong.

- **No query-count assertion.** Any view or serializer touching relations should assert `with self.assertNumQueries(n):`. Without it, an N+1 can be introduced later and nothing notices. This is the highest-value test in a Django suite.
- **Fixtures too small to expose N+1.** Two rows pass whether the code fires 2 queries or 200. Seed enough rows that the count assertion means something.
- **The ORM mocked out.** Mocking `Model.objects` tests the mock. Use the database.
- **Status-code-only assertions.** `assertEqual(response.status_code, 200)` passes for an empty or wrong body.
- **Migrations untested.** Nothing runs the backfill against realistic data.

---

## Reporting

Order findings by production impact, not by file order:

1. **Breaks on deploy** - migration safety
2. **Breaks under concurrency** - lost updates, races
3. **Breaks under load** - N+1, unbounded querysets
4. **Hides future breakage** - missing query-count assertions

For each: location, the concrete failure ("~500 queries per dashboard load at current row counts"), and the minimal fix. If a finding depends on data volume you cannot observe, say so and state the threshold at which it starts to matter.

Do not pad the report. Four real problems beat forty findings of which four are real.

## Deeper references

- `references/orm.md` - queryset cost model, `Prefetch`, `only`/`defer`, `iterator`, `bulk_*`
- `references/migrations.md` - zero-downtime patterns per change type
- `references/security.md` - raw SQL, permission gaps, mass assignment
