# Security gaps characteristic of agent-written Django

Django is secure by default in most places, which is why the gaps are narrow and specific. Agent code lands in them for a predictable reason: **the agent optimises for the code running, and the permissive option always runs.**

## Raw SQL

The ORM parameterises everything. The moment code leaves it, that guarantee is gone.

```python
# injectable
Order.objects.raw(f"SELECT * FROM orders WHERE status = '{status}'")
cursor.execute("SELECT * FROM orders WHERE id = %s" % order_id)

# safe - params passed separately, not interpolated
Order.objects.raw("SELECT * FROM orders WHERE status = %s", [status])
cursor.execute("SELECT * FROM orders WHERE id = %s", [order_id])
```

Note that `%s` here is not Python formatting - it is the driver's placeholder. `cursor.execute(sql % params)` and `cursor.execute(sql, params)` look almost identical and differ completely. Check the comma.

Parameters cannot be table or column names. Code that needs a dynamic column must validate against an allowlist, never interpolate user input.

`.extra()` is deprecated and interpolates in several of its arguments. Treat any surviving `.extra()` as a finding.

`RawSQL()` inside an ORM expression carries the same risk and hides it better.

## Permission gaps in DRF

The default is whatever `DEFAULT_PERMISSION_CLASSES` says. If the project never set it, the default is `AllowAny`.

```python
# no permission_classes and no project default = open to the internet
class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
```

Check three things on every viewset and APIView:

1. **Is a permission class set,** on the class or as a project default?
2. **Is the queryset scoped to the requesting user?** `Order.objects.all()` on a `ModelViewSet` means any authenticated user can fetch any object by primary key. Object-level access needs `get_queryset` filtering by `self.request.user`, not just an `IsAuthenticated` check.
3. **Do `@action` methods inherit the permission you expect?** A custom action can silently widen access.

The same shape appears in plain Django views: a `DetailView` with no `get_queryset` override serves any object whose pk is guessed.

## Mass assignment

```python
class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = "__all__"     # includes is_paid, user, internal_notes...
```

`fields = "__all__"` means every field the model gains in future is writable through the API the moment it is added. The same applies to `ModelForm` with `exclude` instead of `fields` - it fails open as the model grows.

Name fields explicitly. Mark server-controlled fields `read_only`.

## Settings

- `DEBUG = True` in anything reachable from production exposes stack traces, settings, and SQL. Check how `DEBUG` is derived - `os.environ.get("DEBUG", True)` defaults to on, and a string `"False"` is truthy.
- `SECRET_KEY` with a literal default in `settings.py` is a committed secret; anyone with the repo can forge sessions and password-reset tokens.
- `ALLOWED_HOSTS = ["*"]` disables host-header validation.
- Missing `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` where the site is HTTPS.

## Template escaping

Django autoescapes. The failures are where escaping is switched off:

- `{{ value|safe }}` and `{% autoescape off %}` on anything user-supplied.
- `mark_safe()` on a string built from user input. `format_html()` is the safe equivalent - it escapes its arguments.
- JSON dropped into a `<script>` block. Use `{{ data|json_script:"id" }}` rather than `{{ data|safe }}`; the latter allows breaking out of the script tag.

## File uploads

- An upload path built from the client-supplied filename allows traversal. Django sanitises via `get_valid_filename` in `FileSystemStorage`, but custom `upload_to` callables often do not.
- Content type from the request is attacker-controlled. Validate by parsing, not by header.
- Serving user uploads from the same origin as the app turns any stored HTML or SVG into stored XSS.

## Authorisation logic that reads as authentication

The most common real vulnerability in reviewed Django is not injection - it is code that checks *whether* a user is logged in and never checks *which* user:

```python
@login_required
def invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)   # any logged-in user, any invoice
    return render(request, "invoice.html", {"invoice": invoice})
```

```python
@login_required
def invoice(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk, customer=request.user)
    return render(request, "invoice.html", {"invoice": invoice})
```

Every `get_object_or_404`, `.get(pk=...)`, and `DetailView` that resolves an object from a URL parameter needs an ownership predicate. Flag each one that lacks it.
