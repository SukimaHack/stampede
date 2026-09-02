# stampede

**Your agent's Django code passes every test and melts the database.**

An agent skill that reviews Django for the failures that only show up at scale or on deploy — N+1 explosions, unbounded querysets, migrations that lock a live table, lost updates, and tests that assert nothing.

Works with Claude Code, Cursor, Codex CLI, Gemini CLI, and anything else that reads `SKILL.md`.

---

## The problem

```python
for order in Order.objects.all():
    print(order.customer.name)
```

Two lines. Reads fine. Passes review. Passes its test, because the fixture has three rows.

In production it fires **one query per order**.

Nothing about the source text is wrong. `order.customer` is an attribute access that happens to be a network round trip, and the ORM gives you no signal at the call site. This is the entire category: code that is correct, readable, idiomatic — and quadratic.

Agent-written Django lands here more than human-written Django does. Not because the model is worse, but because it pattern-matched thousands of tutorials where the table had four rows. A human who has been paged at 3am for this checks the query count out of scar tissue. An agent has no scar tissue.

## What it checks

**Query explosion** — relation access inside loops, `select_related` where `prefetch_related` was needed, filtered prefetches that silently re-query, aggregation done in Python, `.all()` with no pagination, multi-aggregate `annotate` that returns *wrong numbers* rather than slow ones.

**Migrations that break the deploy** — `NOT NULL` on a populated table, indexes created without `CONCURRENTLY`, column drops that break the old code still running mid-rollout, `RunPython` importing models instead of `apps.get_model`.

**Races** — read-modify-write without `select_for_update`, `get_or_create` assumed atomic without the unique constraint that makes it so, emails and webhooks fired inside `transaction.atomic()` that still send when the transaction rolls back.

**Tests that prove nothing** — no `assertNumQueries`, fixtures too small to expose an N+1, the ORM mocked out so the test exercises the mock.

## Install

```bash
npx skills add SukimaHack/stampede
```

Or copy it in:

```bash
mkdir -p .claude/skills/stampede
cp -r skills/stampede/* .claude/skills/stampede/
```

## Use

```
Use stampede on the diff you just wrote.
```

```
Use stampede to review apps/orders/ before I merge this.
```

It reports and stops. It does not rewrite your code unless you ask.

## What a finding looks like

Not this:

> Possible N+1 query in the loop. Consider using select_related.

This:

> `apps/orders/views.py:34` — `order.customer` inside the dashboard loop fires one query per order. At the current row count that is ~500 queries per page load. Add `.select_related("customer")` at line 31.
>
> `apps/orders/views.py:41` — `order.items.filter(active=True)` discards the `prefetch_related("items")` from line 31 and re-queries per order. Use `Prefetch("items", queryset=Item.objects.filter(active=True), to_attr="active_items")`.

Findings are ordered by production impact — breaks on deploy, then breaks under concurrency, then breaks under load — not by file order. If a finding depends on data volume it cannot observe, it says so and gives the threshold where it starts to matter.

## Scope

This reviews **Django-specific production risk**. It is deliberately not a general code reviewer, a linter replacement, or a style guide. `ruff` and `django-upgrade` already handle what they handle; there is no value in a skill that repeats them.

It reads your code. It does not run it, does not connect to your database, and does not measure anything at runtime. Where a real query count matters and cannot be inferred, it will tell you to measure with `assertNumQueries` rather than guess on your behalf.

## Contents

```
skills/stampede/
├── SKILL.md                    the review procedure
└── references/
    ├── orm.md                  queryset cost model, Prefetch, only/defer, bulk ops
    ├── migrations.md           zero-downtime patterns per change type
    └── security.md             raw SQL, permission gaps, mass assignment
```

## License

MIT.
