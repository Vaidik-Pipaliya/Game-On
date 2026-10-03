from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import role_required

from .forms import MemberForm
from .models import Member
from .services import register_member, renew_membership, search_members

# Front desk runs the members screens; the owner can do everything.
desk_only = role_required("owner", "front_desk")


@desk_only
def member_search(request):
    query = request.GET.get("q", "")
    return render(request, "members/search.html", {"members": search_members(query), "q": query})


@desk_only
def member_new(request):
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            member = register_member(
                full_name=data["full_name"], phone=data["phone"], email=data["email"],
                date_of_birth=data["date_of_birth"], plan=data["plan"], guardian=data["guardian_phone"],
                emergency_contact=data["emergency_contact"], whatsapp_opt_in=data["whatsapp_opt_in"],
            )
        except ValidationError as error:
            form.add_error(None, error.messages)  # rule broken, e.g. Junior without guardian
        else:
            messages.success(request, f"{member.full_name} is registered.")
            return redirect("member_detail", pk=member.pk)
    return render(request, "members/new.html", {"form": form})


@desk_only
def member_detail(request, pk):
    member = get_object_or_404(Member.objects.prefetch_related("memberships__plan"), pk=pk)
    return render(request, "members/detail.html", {
        "member": member,
        "memberships": sorted(member.memberships.all(), key=lambda m: m.end_date, reverse=True),
        "current": member.current_membership,
        # Reverse relations, so this app never imports the other apps' models.
        "bookings": member.bookings.select_related("court").order_by("-start")[:10],
        "orders": member.order_set.order_by("-created_at")[:10],
        "tabs": member.tab_set.order_by("-opened_at")[:10],
    })


@desk_only
@require_POST
def member_renew(request, pk):
    member = get_object_or_404(Member, pk=pk)
    try:
        membership = renew_membership(member)
    except ValidationError as error:
        messages.error(request, error.messages[0])
    else:
        messages.success(request, f"Renewed until {membership.end_date:%d %b %Y}.")
    return redirect("member_detail", pk=pk)
