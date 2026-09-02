# A deliberately broken Django app, used to check that stampede actually finds things.
#
# Every defect is tagged BUG-nn. The expected findings are listed in examples/EXPECTED.md.
# Run the skill against this file and compare.
#
# Everything here passes flake8, passes a small-fixture test suite, and reads like
# ordinary Django. That is the point.

from django.db import models, transaction
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.db.models import F
from rest_framework import serializers, viewsets


# ---------------------------------------------------------------- models

class Customer(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()


class Product(models.Model):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    paid = models.BooleanField(default=False)
    status = models.CharField(max_length=20, null=True)
    legacy_ref = models.CharField(max_length=40, blank=True)
    internal_notes = models.TextField(blank=True)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.IntegerField(default=1)
    active = models.BooleanField(default=True)


# ---------------------------------------------------------------- views

@login_required
def dashboard(request):
    # BUG-01: unbounded .all() with no pagination
    orders = Order.objects.all()

    rows = []
    for order in orders:
        # BUG-02: N+1 - forward FK touched per iteration, no select_related
        customer_name = order.customer.name

        # BUG-03: N+1 - reverse FK, no prefetch_related
        # BUG-04: aggregation in Python instead of the database
        total = sum(item.quantity * item.product.price for item in order.items.all())

        rows.append({"customer": customer_name, "total": total})

    return render(request, "dashboard.html", {"rows": rows})


@login_required
def order_detail(request, pk):
    # BUG-05: authorisation gap - any logged-in user can read any order
    order = get_object_or_404(Order, pk=pk)
    return render(request, "order.html", {"order": order})


def active_item_report():
    orders = Order.objects.prefetch_related("items")
    out = []
    for o in orders:
        # BUG-06: .filter() on a prefetched manager discards the prefetch, re-queries per row
        out.append(len(o.items.filter(active=True)))
    return out


def summary():
    qs = Order.objects.filter(paid=True)
    # BUG-07: exists() then count() then iteration = three queries, no cache reuse
    if qs.exists():
        n = qs.count()
        for o in qs:
            _ = o.pk
        return n
    return 0


def search(request):
    status = request.GET.get("status", "")
    # BUG-08: SQL injection via f-string interpolation into raw()
    return list(Order.objects.raw(f"SELECT * FROM shop_order WHERE status = '{status}'"))


# ---------------------------------------------------------------- checkout

def checkout(request, product_id, qty):
    product = Product.objects.get(pk=product_id)

    with transaction.atomic():
        # BUG-09: read-modify-write on a shared row, no select_for_update - lost update
        product.stock = product.stock - qty
        product.save()

        order = Order.objects.create(customer=request.user.customer)

        # BUG-10: side effect inside atomic() - the email sends even if this rolls back
        send_confirmation_email(order)

    return order


def send_confirmation_email(order):
    ...


def restock(product_id, qty):
    # NOT A BUG. This is the correct atomic form, and re-reading from the database
    # is the right way to get the value back. Flagging this counts against a review.
    Product.objects.filter(pk=product_id).update(stock=F("stock") + qty)
    return Product.objects.get(pk=product_id).stock


def restock_and_report(product_id, qty):
    product = Product.objects.get(pk=product_id)
    product.stock = F("stock") + qty
    product.save()
    # BUG-11: after saving an F() expression the attribute holds a CombinedExpression,
    #         not a number. This returns something like <CombinedExpression> and any
    #         arithmetic on it raises. Needs refresh_from_db() first.
    return product.stock


# ---------------------------------------------------------------- API

class OrderSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()

    def get_customer_name(self, obj):
        # BUG-14: crosses a relation once per serialized row. With many=True on the
        #         list endpoint this is one query per order, and no queryset in the
        #         viewset can fix it without select_related.
        return obj.customer.name

    class Meta:
        model = Order
        # BUG-12: mass assignment - exposes paid and internal_notes as writable
        fields = "__all__"


class OrderViewSet(viewsets.ModelViewSet):
    # BUG-13: no permission_classes, and queryset is not scoped to request.user
    queryset = Order.objects.all()
    serializer_class = OrderSerializer


# ---------------------------------------------------------------- tests

class OrderTests:
    def test_dashboard_loads(self):
        # BUG-15: status-code-only assertion, proves nothing about the body
        # BUG-16: no assertNumQueries, so an N+1 can be introduced and no test notices
        response = self.client.get("/dashboard/")
        assert response.status_code == 200
