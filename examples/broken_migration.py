# A migration that passes CI and takes production down.
#
# CI runs this against an empty database with no traffic, so every one of these
# defects is invisible there. Tagged MIG-nn; expected findings in EXPECTED.md.

from django.db import migrations, models
import django.utils.timezone

# MIG-01: model imported at module level instead of via apps.get_model in RunPython.
#         This reflects today's schema, not the schema at this point in history,
#         so the migration breaks when replayed on a fresh database.
from shop.models import Order


def backfill_status(apps, schema_editor):
    # MIG-02: loads every row into memory - OOM on a large table
    # MIG-03: single transaction over the whole table - holds locks for the duration
    for order in Order.objects.all():
        order.status = "pending"
        order.save()


class Migration(migrations.Migration):

    dependencies = [("shop", "0007_auto")]

    # MIG-04: atomic left at the default True, so the long backfill below runs
    #         inside one transaction alongside the schema changes

    operations = [
        # MIG-05: NOT NULL column with no default on a populated table - rejected outright
        migrations.AddField(
            model_name="order",
            name="status",
            field=models.CharField(max_length=20, null=False),
        ),

        # MIG-06: non-constant default rewrites the entire table under ACCESS EXCLUSIVE
        migrations.AddField(
            model_name="order",
            name="synced_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),

        # MIG-07: schema change and a long data migration in the same file
        migrations.RunPython(backfill_status),  # MIG-08: no reverse_code - silently irreversible

        # MIG-09: db_index=True on a previously unindexed column generates a blocking
        #         CREATE INDEX, which takes ACCESS EXCLUSIVE for the length of the build
        #         and queues every query arriving behind it. Needs AddIndexConcurrently
        #         with atomic = False.
        #         (Note: the same operation on a ForeignKey would be a state-only no-op,
        #         because ForeignKey already defaults to db_index=True.)
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(max_length=20, null=True, db_index=True),
        ),

        # MIG-10: column dropped in the same deploy that stops using it. Old instances
        #         are still running during the rollout and still SELECT this column.
        migrations.RemoveField(model_name="order", name="legacy_ref"),
    ]
