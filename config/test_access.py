"""Walks every URL the app defines and checks who can reach it. A regression guard: if someone adds a page and
forgets its permission check, one of these tests fails."""

import re

from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver

from accounts.models import User

# Pages that are public on purpose. Everything else must refuse a signed-out visitor.
PUBLIC_GET = {
    "/", "/plans/", "/cafe/", "/courts/availability/", "/enquiry/", "/thanks/", "/shop/", "/shop/cart/", "/login/",
}
# Public POST endpoints (forms and machine-to-machine calls that protect themselves another way).
PUBLIC_POST = {
    "/enquiry/", "/shop/cart/", "/shop/cart/add/", "/auth/firebase/", "/logout/",
    "/webhooks/razorpay/",  # protected by the HMAC signature
}
SKIP_PREFIXES = ("admin/", "static/")  # Django admin has its own login


def all_urls(patterns=None, prefix=""):
    """Every route as a concrete path, with dummy values filled in for <int:pk>, <str:action> and so on."""
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from all_urls(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            if route.startswith(SKIP_PREFIXES):
                continue
            yield "/" + re.sub(r"<(?:int):\w+>", "1", re.sub(r"<(?:str|path):\w+>", "x", route))


URLS = sorted(set(all_urls()))


def is_login_redirect(response):
    return response.status_code == 302 and response["Location"].startswith("/login/")


class SignedOutVisitorTests(TestCase):
    def test_we_found_the_urls(self):
        self.assertGreater(len(URLS), 60)

    def test_get_requests_either_ask_for_login_or_are_public_on_purpose(self):
        client = Client()
        for url in URLS:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertLess(response.status_code, 500, f"GET {url} crashed")
                if url in PUBLIC_GET or response.status_code == 405:
                    continue  # public page, or a POST-only endpoint
                self.assertIn(
                    response.status_code, (302, 403, 404),
                    f"GET {url} returned {response.status_code} to a signed-out visitor",
                )
                if response.status_code == 302:
                    self.assertTrue(is_login_redirect(response), f"GET {url} redirects to {response['Location']}, not the login page")

    def test_post_requests_never_change_anything_for_a_signed_out_visitor(self):
        client = Client(enforce_csrf_checks=False)
        for url in URLS:
            with self.subTest(url=url):
                response = client.post(url, {})
                self.assertLess(response.status_code, 500, f"POST {url} crashed")
                if url in PUBLIC_POST:
                    continue
                self.assertIn(response.status_code, (302, 403, 404, 405), f"POST {url} returned {response.status_code}")
                if response.status_code == 302:
                    self.assertTrue(is_login_redirect(response), f"POST {url} redirects to {response['Location']}")

    def test_the_trial_booking_page_needs_sign_in(self):
        client = Client()
        self.assertTrue(is_login_redirect(client.get("/trial/")))
        self.assertTrue(is_login_redirect(client.post("/trial/", {"name": "x"})))


# What each staff role must NOT be able to open. (Permission checks run before any lookup, so the
# dummy ids in these URLs never matter: a wrong role is refused with 403 first.)
FORBIDDEN_FOR = {
    "member": ["/bar/menu/", "/desk/", "/desk/book/", "/desk/members/", "/desk/leads/", "/desk/messages/", "/desk/shop/sale/",
               "/desk/shop/stock/", "/bar/", "/bar/kitchen/", "/owner/dashboard/", "/owner/invoices/",
               "/owner/payroll/", "/owner/audit/", "/staff/leave/"],
    "front_desk": ["/bar/menu/", "/bar/", "/bar/kitchen/", "/bar/shift/", "/bar/day-report/", "/owner/dashboard/", "/owner/invoices/",
                   "/owner/gst/", "/owner/payroll/", "/owner/leave/", "/owner/audit/", "/owner/export/ledger.csv"],
    "bar_staff": ["/desk/book/", "/desk/members/", "/desk/members/new/", "/desk/leads/", "/desk/messages/",
                  "/desk/social/", "/desk/shop/sale/", "/desk/shop/orders/", "/desk/shop/stock/", "/owner/dashboard/",
                  "/owner/invoices/", "/owner/audit/"],
    "shop_staff": ["/bar/menu/", "/desk/book/", "/desk/members/", "/desk/leads/", "/desk/messages/", "/desk/social/", "/bar/",
                   "/bar/kitchen/", "/owner/dashboard/", "/owner/invoices/", "/owner/audit/"],
}


class WrongRoleTests(TestCase):
    def test_each_role_is_refused_the_pages_that_are_not_theirs(self):
        for role, urls in FORBIDDEN_FOR.items():
            client = Client()
            client.force_login(User.objects.create(username=role, email=f"{role}@example.com", role=role))
            for url in urls:
                with self.subTest(role=role, url=url):
                    self.assertEqual(client.get(url).status_code, 403, f"{role} was allowed into {url}")

    def test_only_the_owner_can_use_owner_pages(self):
        owner_only = [u for u in URLS if u.startswith("/owner/")]
        self.assertGreater(len(owner_only), 8)
        for role in ("front_desk", "bar_staff", "shop_staff", "member"):
            client = Client()
            client.force_login(User.objects.create(username=role, email=f"{role}@example.com", role=role))
            for url in owner_only:
                with self.subTest(role=role, url=url):
                    if url == "/owner/payroll/1/pay/" or url.endswith("/retry/"):
                        self.assertEqual(client.post(url).status_code, 403)
                    else:
                        self.assertEqual(client.get(url).status_code, 403, f"{role} reached {url}")

    def test_a_member_can_not_reach_any_staff_or_bar_page(self):
        client = Client()
        client.force_login(User.objects.create(username="m", email="m@example.com", role="member"))
        for url in (u for u in URLS if u.startswith(("/desk/", "/bar/", "/owner/"))):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertIn(response.status_code, (403, 405), f"member got {response.status_code} on {url}")
