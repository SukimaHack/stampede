# The queryset cost model

Everything in Class 1 follows from one fact: **a queryset is a promise, not a result.** Reviewing Django for performance means tracking when each promise is redeemed and how many round trips it costs.

## When a queryset actually hits the database

A queryset is lazy until something forces evaluation:

| Triggers a query | Does not |
|---|---|
| iteration (`for o in qs`) | `qs = Model.objects.filter(...)` |
| `list(qs)`, `len(qs)`, `bool(qs)` | chaining `.filter()`, `.exclude()`, `.order_by()` |
| slicing with a step (`qs[::2]`) | slicing without a step (`qs[:10]`) - returns a new lazy queryset |
| `repr(qs)` (bites you in the shell) | `.select_related()`, `.prefetch_related()`, `.only()`, `.defer()` |
| pickling | assigning to a template context |

After full iteration, results are cached on that queryset object. Two consequences reviewers miss:

1. **`exists()` and `count()` do not populate the cache.** Calling `exists()` then iterating costs two queries.
2. **Any new queryset starts empty.** `qs[:10]` is a *different* object; it re-queries even if `qs` was already evaluated. Same for `qs.filter(...)` on an evaluated queryset.

```python
qs = Order.objects.filter(active=True)
list(qs)        # query 1, now cached
list(qs)        # no query - cache hit
list(qs[:5])    # query 2 - new queryset, LIMIT 5
list(qs.filter(paid=True))  # query 3 - new queryset
```

## Choosing the right prefetch

| Relation | Direction | Use |
|---|---|---|
| ForeignKey | forward (`order.customer`) | `select_related` |
| OneToOne | forward (`user.profile`) | `select_related` |
| OneToOne | reverse (`profile.user` via related_name) | `select_related` works |
| ForeignKey | reverse (`customer.order_set`) | `prefetch_related` |
| ManyToMany | either | `prefetch_related` |
| GenericForeignKey | - | `prefetch_related` only |

`select_related` costs width: every joined column is fetched on every row. Joining four tables to read one field from each inflates row size. Deep chains (`select_related("a__b__c__d")`) can be slower than a prefetch.

`prefetch_related` costs a second query plus Python-side joining, but keeps rows narrow and handles multi-valued relations. Prefer it when the related set is large per parent.

## Prefetch() - the escape hatch

Use `Prefetch` whenever the related set needs filtering, ordering, or its own `select_related`:

```python
from django.db.models import Prefetch

Order.objects.prefetch_related(
    Prefetch(
        "items",
        queryset=Item.objects.filter(active=True).select_related("product"),
        to_attr="active_items",   # results land on order.active_items as a plain list
    )
)
```

Two review points:

- **Without `to_attr`**, the prefetched set replaces the default manager result, and calling `.filter()` on it still re-queries. With `to_attr`, you get a list - calling `.filter()` on it is an `AttributeError`, which fails loudly instead of silently costing queries. Prefer `to_attr` for this reason.
- A `Prefetch` queryset can carry its own `select_related`, which is how you fix an N+1 *inside* a prefetched set.

## only() and defer()

`only("a", "b")` fetches just those columns. Touching any other field on the instance fires **one query per instance** to load it - a fresh N+1, created by an optimization.

Flag `only()` / `defer()` whenever the code path afterwards is long enough that you cannot confirm which fields are read. It is a sharp tool and agent code applies it speculatively.

`only()` combined with `select_related` requires naming the related fields too: `.select_related("customer").only("id", "customer__name")`.

## Iterating large tables

```python
# loads everything into memory
for row in Model.objects.all():
    ...

# server-side cursor, constant memory
for row in Model.objects.all().iterator(chunk_size=2000):
    ...
```

`.iterator()` **disables the result cache** and, before Django 4.1, silently ignored `prefetch_related`. Since 4.1 prefetching works with `iterator()` only when `chunk_size` is given. Check the Django version before recommending it alongside a prefetch.

## Bulk operations

```python
Model.objects.bulk_create(objs, batch_size=1000)
Model.objects.bulk_update(objs, ["field_a", "field_b"], batch_size=1000)
Model.objects.filter(...).update(field=F("field") + 1)
```

All three bypass `save()`, `pre_save`, `post_save`, and (for `update()`) `refresh_from_db` on the in-memory instances. `bulk_create` does not set primary keys on the returned objects on every backend - Postgres does, others historically did not.

Without `batch_size`, a large `bulk_create` builds one enormous statement. Set it.

## F() and database-side expressions

`F()` moves the computation into SQL, which makes it atomic and avoids reading the row first:

```python
Product.objects.filter(pk=pk).update(stock=F("stock") - 1)
```

After an `F()` update, the **in-memory instance is stale** and holds a `CombinedExpression`, not a number. Calling `save()` on it again re-applies the expression. Call `refresh_from_db()` before reading.

## Annotations that multiply rows

Combining two `annotate(Count(...))` calls across different multi-valued relations produces a cartesian join and **wrong numbers**, not just slow ones:

```python
# counts are multiplied by each other - silently incorrect
Author.objects.annotate(Count("books"), Count("articles"))

# correct
Author.objects.annotate(
    n_books=Count("books", distinct=True),
    n_articles=Count("articles", distinct=True),
)
```

`distinct=True` fixes counts but not `Sum` or `Avg`. For those, use subqueries:

```python
from django.db.models import OuterRef, Subquery, Sum

Author.objects.annotate(
    total=Subquery(
        Book.objects.filter(author=OuterRef("pk"))
        .values("author")
        .annotate(s=Sum("price"))
        .values("s")[:1]
    )
)
```

This is a correctness bug that looks like a performance idiom. Flag every multi-aggregate `annotate`.

## Measuring instead of guessing

```python
from django.test.utils import CaptureQueriesContext
from django.db import connection

with CaptureQueriesContext(connection) as ctx:
    render_the_view()
print(len(ctx.captured_queries))
```

In tests, `assertNumQueries(n)` is the assertion form. When reporting a finding, prefer a measured count over an estimate; when you cannot measure, state the count as a function of row count ("1 + one per order").
