from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import role_required
from accounts.templatetags.money import rupees
from finance.models import Method, Source
from finance.services import DESK_METHODS, OnlinePaymentUnavailable, start_online_payment
from members.pricing import price_for

from .forms import CheckoutForm, CounterLineFormSet, CounterSaleForm
from .models import Order, Product, Variant
from .services import cancel_order, complete_order, low_stock_variants, mark_ready, place_order, restock

shop_staff = role_required("owner", "shop_staff", "front_desk")
MAX_PER_ITEM = 10


def _member_of(user):
    # Member.user is a OneToOne; the reverse accessor raises if missing, getattr's default handles that.
    return getattr(user, "member", None) if user.is_authenticated else None


def _cart(request):
    return request.session.setdefault("cart", {})  # {"variant_id": quantity}, kept in the session


def _cart_items(request):
    cart = _cart(request)
    variants = Variant.objects.filter(pk__in=cart.keys()).select_related("product")
    return [(v, cart[str(v.pk)]) for v in variants]


def _low_stock_message(request, variants):
    if variants:
        names = ", ".join(str(v) for v in variants)
        messages.warning(request, f"Low stock: {names}. Reorder soon.")


def _pay_online(request, order, fallback):
    try:
        payment = start_online_payment(source=Source.SHOP, reference_id=order.pk, amount_paise=order.total_paise)
    except OnlinePaymentUnavailable as error:
        messages.error(request, f"{error} Your order is saved; pay when you collect it.")
        return redirect(fallback)
    return redirect("pay_page", pk=payment.pk)


# ---- Public shop and member orders ----

def catalog(request):
    membership = _member_of(request.user).current_membership if _member_of(request.user) else None
    category = request.GET.get("category", "")
    products = Product.objects.filter(is_active=True).prefetch_related("variants").order_by("category", "name")
    if category in Product.Category.values:
        products = products.filter(category=category)
    items = [
        {"product": p, "variants": list(p.variants.all()), "price": price_for("shop", p.price_paise, membership)}
        for p in products
    ]
    return render(request, "shop/catalog.html", {
        "items": items, "categories": Product.Category.choices, "category": category,
    })


@require_POST
def cart_add(request):
    variant = Variant.objects.filter(pk=request.POST.get("variant") or None, product__is_active=True).first()
    try:
        quantity = int(request.POST.get("quantity", 1))
    except ValueError:
        quantity = 0
    if variant is None or not 1 <= quantity <= MAX_PER_ITEM:
        messages.error(request, "Choose a size and a quantity from 1 to 10.")
        return redirect("shop_catalog")
    cart = _cart(request)
    cart[str(variant.pk)] = min(cart.get(str(variant.pk), 0) + quantity, MAX_PER_ITEM)
    request.session.modified = True  # tell Django the dict inside the session changed
    messages.success(request, f"Added {variant} to your cart.")
    return redirect("shop_catalog")


def cart_view(request):
    if request.method == "POST":
        _cart(request).pop(request.POST.get("remove", ""), None)
        request.session.modified = True
        return redirect("shop_cart")
    membership = _member_of(request.user).current_membership if _member_of(request.user) else None
    lines = []
    for variant, quantity in _cart_items(request):
        unit = price_for("shop", variant.product.price_paise, membership).amount_paise
        lines.append({"variant": variant, "quantity": quantity, "unit": unit, "total": unit * quantity})
    return render(request, "shop/cart.html", {"lines": lines, "total": sum(l["total"] for l in lines)})


@login_required
def checkout(request):
    items = _cart_items(request)
    if not items:
        messages.info(request, "Your cart is empty. Add something first.")
        return redirect("shop_catalog")
    member = _member_of(request.user)
    initial = {"customer_name": member.full_name, "customer_phone": member.phone} if member else {}
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            order, low = place_order(
                items=items, channel=Order.Channel.ONLINE, member=member, placed_by=request.user,
                customer_name=data["customer_name"], customer_phone=data["customer_phone"],
                fulfilment=data["fulfilment"], delivery_address=data["delivery_address"],
                payment_method=data["payment_method"] or None,
            )
        except ValidationError as error:  # e.g. someone bought the last pair a moment ago
            form.add_error(None, error.messages)
        else:
            request.session["cart"] = {}
            messages.success(request, f"Order #{order.pk} placed: {rupees(order.total_paise)}.")
            if data["payment_method"] == Method.ONLINE:
                return _pay_online(request, order, "shop_my_orders")
            return redirect("shop_my_orders")
    return render(request, "shop/checkout.html", {"form": form, "items": items})


@login_required
def my_orders(request):
    orders = Order.objects.filter(placed_by=request.user, channel=Order.Channel.ONLINE).prefetch_related(
        "lines__variant__product").order_by("-created_at")
    return render(request, "shop/my_orders.html", {"orders": orders})


# ---- Staff screens ----

@shop_staff
def counter_sale(request):
    form = CounterSaleForm(request.POST or None)
    lines = CounterLineFormSet(request.POST or None, prefix="lines")
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        items = [(row["variant"], row["quantity"] or 1) for row in lines.cleaned_data if row.get("variant")]
        try:
            order, low = place_order(
                items=items, channel=Order.Channel.COUNTER, member=form.cleaned_data["member_phone"],
                payment_method=form.cleaned_data["payment_method"], placed_by=request.user,
            )
        except ValidationError as error:
            form.add_error(None, error.messages)
        else:
            messages.success(request, f"Sale #{order.pk} done: {rupees(order.total_paise)} ({order.payment_method}).")
            _low_stock_message(request, low)
            return redirect("shop_counter")
    return render(request, "shop/counter.html", {"form": form, "lines": lines})


@shop_staff
def orders_list(request):
    open_orders = Order.objects.filter(
        channel=Order.Channel.ONLINE, status__in=[Order.Status.PLACED, Order.Status.READY]
    ).select_related("member").prefetch_related("lines__variant__product").order_by("created_at")
    recent = Order.objects.exclude(pk__in=open_orders).select_related("member").order_by("-created_at")[:20]
    return render(request, "shop/orders.html", {"open_orders": open_orders, "recent": recent, "methods": DESK_METHODS})


@shop_staff
@require_POST
def order_action(request, pk, action):
    order = get_object_or_404(Order, pk=pk)
    try:
        if action == "ready":
            mark_ready(order)
            messages.success(request, f"Order #{order.pk} is ready for pickup.")
        elif action == "complete":
            complete_order(order, request.POST.get("payment_method") or None)
            messages.success(request, f"Order #{order.pk} completed.")
        elif action == "cancel":
            cancel_order(order, by=request.user)
            messages.success(request, f"Order #{order.pk} cancelled. Stock is back on the shelf.")
        else:
            messages.error(request, "Unknown action.")
    except ValidationError as error:
        messages.error(request, error.messages[0])
    return redirect("shop_orders")


@shop_staff
def stock(request):
    if request.method == "POST":
        variant = get_object_or_404(Variant, pk=request.POST.get("variant"))
        try:
            restock(variant, int(request.POST.get("quantity") or 0), by=request.user)
        except (ValueError, ValidationError):
            messages.error(request, "Enter how many items arrived (a whole number above 0).")
        else:
            messages.success(request, f"{variant} now has {variant.stock} in stock.")
        return redirect("shop_stock")
    variants = Variant.objects.filter(product__is_active=True).select_related("product").order_by("product__category", "product__name", "size")
    return render(request, "shop/stock.html", {"variants": variants, "low_count": low_stock_variants().count()})
