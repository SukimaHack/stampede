# Expected findings

Run the skill against the two example files and compare. Every defect below is
tagged in the source. If a run misses one, that is a gap in `SKILL.md`, not a
gap in the reviewer — file it.

```
Use stampede on examples/broken_shop.py and examples/broken_migration.py
```

Both files pass `flake8`, read like ordinary Django, and would pass a test suite
built on a three-row fixture. None of them is a syntax error or a style problem.

## broken_shop.py

| Tag | Defect | Covered by |
|---|---|---|
| BUG-01 | `Order.objects.all()` unbounded, no pagination | Class 1 — unbounded result sets |
| BUG-02 | `order.customer` in a loop, no `select_related` | Class 1 — relation access inside iteration |
| BUG-03 | `order.items.all()` in a loop, no `prefetch_related` | Class 1 — relation access inside iteration |
| BUG-04 | `sum()` over a queryset instead of `aggregate` | Class 1 — aggregation in Python |
| BUG-05 | `get_object_or_404(Order, pk=pk)` with no ownership predicate | security.md — authorisation that reads as authentication |
| BUG-06 | `.filter()` on a prefetched manager discards the prefetch | Class 1 — filtering inside a prefetch |
| BUG-07 | `exists()` → `count()` → iteration, three queries | Class 1 — repeated evaluation |
| BUG-08 | f-string interpolated into `.raw()` | security.md — raw SQL |
| BUG-09 | Read-modify-write on `stock`, no `select_for_update` | Class 3 — lost updates |
| BUG-10 | Email sent inside `transaction.atomic()` | Class 3 — side effects in atomic |
| BUG-11 | Instance read after an `F()` update without `refresh_from_db` | orm.md — F() expressions |
| BUG-12 | `fields = "__all__"` exposes `paid`, `internal_notes` | security.md — mass assignment |
| BUG-13 | ViewSet with no `permission_classes`, queryset not user-scoped | security.md — permission gaps in DRF |
| BUG-14 | List endpoint N+1s per row, no prefetching on the viewset queryset | Class 1 — serializer `many=True` |
| BUG-15 | Status-code-only assertion | Class 4 — tests that prove nothing |
| BUG-16 | No `assertNumQueries` on a view that touches relations | Class 4 — tests that prove nothing |

## broken_migration.py

| Tag | Defect | Covered by |
|---|---|---|
| MIG-01 | Model imported at module level instead of `apps.get_model` | Class 2 / migrations.md — data migrations |
| MIG-02 | `RunPython` loads every row | migrations.md — batch it |
| MIG-03 | Whole-table backfill in one transaction | migrations.md — `atomic = False` |
| MIG-04 | `atomic` left at default for a long backfill | migrations.md — data migrations |
| MIG-05 | `NOT NULL` column, no default, populated table | Class 2 table — add nullable, backfill, then constrain |
| MIG-06 | Non-constant default (`timezone.now`) rewrites the table | migrations.md — adding a column |
| MIG-07 | Schema change and data migration in one file | Class 2 table |
| MIG-08 | `RunPython` with no `reverse_code` | Class 2 — should be explicit `noop` |
| MIG-09 | `db_index=True` on a large table blocks writes | Class 2 — `AddIndexConcurrently`, `atomic = False` |
| MIG-10 | Column removed in the same deploy that stops using it | Class 2 — two-release removal |

## Notes on grading a run

- **Ordering matters.** A good run reports MIG-05 and MIG-10 before BUG-15. Findings
  are supposed to be sorted by production impact, not by file order.
- **Quantification matters.** "N+1 at line 47" is a weaker finding than "one query per
  order; at 500 orders that is 500 queries per dashboard load."
- **False positives count against a run.** Flagging `restock()` — which is already
  correct — is a defect in the review, not a bonus finding.
- **BUG-11 is the subtle one.** Most reviewers stop at "uses `F()`, good" and miss that
  the following line reads a stale value. Treat catching it as the sign of a genuinely
  careful run.
