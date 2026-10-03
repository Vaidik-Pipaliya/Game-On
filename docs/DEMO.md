# Demo script (4 minutes) and judge questions

Before the demo: `manage.py seed_demo` has run, you're signed in as **owner**, and two browser windows are open side by side. Keep a terminal ready in the project folder.

## The script (strongest technical moments first)

| Time | Show | Say |
|---|---|---|
| 0:00 | Home page | "The club ran on WhatsApp, Excel and paper. This is one system for courts, members, shop, bar, website and money, on one PostgreSQL database and one ledger." |
| 0:20 | Terminal: `python manage.py test courts.tests.ConcurrencyTests -v 2` | "50 threads try to book Court 2 at 18:00 at the same instant. Exactly one wins. That's not Python checking; it's a PostgreSQL exclusion constraint, so even two servers can't double-book." |
| 0:50 | Court grid `/desk/book/` → click a free **+**, enter member phone `9876500003`, Paid by UPI | "Front desk books in seconds. The price comes from one pricing function: Silver gets 15% off; Gold gets free hours." |
| 1:10 | Book the same court at :30 in the other window | Message: *"Tennis Court 1 is taken at 18:30. Pick another court or time."* "The database refused it; we turn that into a friendly message." |
| 1:25 | Book the same member twice more | *"…has reached the limit of 2 bookings"* "Row lock on the member, so even simultaneous requests can't sneak a third." |
| 1:40 | Bar → table T1 (open tab with a Gold member) | "The Gold member discount is an automatic line; nobody has to remember it. Split the bill: ₹500 cash + rest UPI." |
| 2:05 | Kitchen screen | "Orders route to kitchen or bar and refresh by themselves." |
| 2:20 | Shop stock → item at 0 | "Counter and online orders share one stock. The update is `stock = stock - 1 WHERE stock >= 1`; the last pair can only be sold once (tested with 10 parallel buyers)." |
| 2:40 | Owner dashboard | "Every number is a sum of an append-only ledger, so it always matches the daily close: revenue by courts, shop, bar, memberships × cash, card, UPI, online; what we're owed; utilisation and peak hours from pandas." |
| 3:10 | Public site → Book a trial / Contact | "A visitor books a trial; it becomes a lead, auto-assigned and emailed after commit. Hidden honeypot + rate limit stop spam without a CAPTCHA." |
| 3:30 | Messages log | "WhatsApp is sent only after the booking commits; every attempt is logged; failures fall back to email and can be retried." |
| 3:45 | Close | "322 tests, including real concurrency tests. Simple tools, with the hard rules in the database." |

If Razorpay test keys are set: book with **Paid by Online** → pay with UPI `success@razorpay` → the booking shows Paid and the ledger has one online row.

## Likely judge questions

### Technical depth

**How do you guarantee no double booking?**
A PostgreSQL `ExclusionConstraint` on `(court_id WITH =, TSTZRANGE(start, end) WITH &&)` for confirmed whole-court bookings (`btree_gist` lets a GiST index compare the court id with `=`). An application check (read, then write) can race; the constraint is checked at insert time inside the database. Proven by a test where 50 threads hit one slot.

**Why both a constraint and a row lock?**
They protect different rules. Overlap is about one court and time range → constraint. "Max 2 per member per day" is a count across rows → we lock the member row (`select_for_update`), so the second request waits and then sees the first one's booking.

**Why are ranges half-open?**
`[start, end)` means 18:00–19:00 and 19:00–20:00 touch but don't overlap, while 18:00 and 18:30 do.

**How can stock never go negative?**
One statement: `UPDATE variant SET stock = stock - q WHERE id = v AND stock >= q`. The check and the subtract are atomic in the database; if it updates 0 rows, the item is out of stock. Multi-line orders run in one transaction, in id order, so a failure rolls everything back and two orders can't deadlock.

**Why a ledger instead of adding up bookings, orders and tabs?**
One table, one meaning: money in or out, with source and method. Refunds are new negative rows, never edits (the model raises on update/delete). The dashboard, day reports and CSV all sum the same rows, so they can't disagree.

**How do you stop a payment being recorded twice?**
The Razorpay callback and the webhook (which Razorpay retries) both call one function. It locks the Payment row, checks it isn't already paid, and `razorpay_payment_id` is unique. Tested with 10 simultaneous captures → one ledger row. Wrong amounts are ignored.

**How do you know a payment is real?**
Razorpay signs `order_id|payment_id` with our secret key; the server recomputes HMAC-SHA256 and compares in constant time. Webhooks are signed over the raw body with a separate secret. The secret never reaches the browser.

**Why is the webhook CSRF-exempt? Isn't that unsafe?**
Razorpay's servers can't send our CSRF token. The HMAC signature replaces it: without the webhook secret nobody can produce a valid request.

**What happens if WhatsApp is down while booking?**
Nothing to the booking. Messages are sent in `transaction.on_commit`, after the booking is saved. The failure is logged, email is sent instead, and the message can be retried.

**Why integer paise?**
Floats can't represent 0.1 exactly; money must add up to the paisa. All money is integer paise, rounded half up once, when a percentage is applied.

**How does login work?**
Google sign-in gives a Google token → Firebase exchanges it for a Firebase ID token → Django verifies it with `firebase_admin.auth.verify_id_token` (signature, expiry, audience) → normal Django session. We never store passwords. New users are members; the owner promotes staff.

### Design

**Why Django and server-rendered pages instead of React + API?**
One codebase the team can explain line by line; Django gives ORM, transactions, admin, auth, CSRF and forms. The hard problems are in the database, not the UI.

**Why one pricing function?**
Courts, shop and bar all ask "what does this person pay?". One function and one test file means the rule can't drift between three places. It's pure (no database), so it's fast to test.

**How are roles enforced?**
`role_required(...)` runs on every request on the server; hiding buttons is only cosmetic. Tests check that each protected page returns 403 for other roles.

**Why compute membership status instead of storing it?**
A stored status goes stale at midnight unless a job runs. Computing it from `end_date` is always right, and expired members get walk-in prices with no job.

### Scalability

**What happens with 10,000 members or many bookings?**
The overlap check uses the constraint's GiST index; the daily-limit count is one indexed range query; locks are per member and per court slot, so unrelated bookings never wait for each other. Dashboard sums run in SQL with an index on `created_at`; pandas only sees 30 days.

**What would you change for several clubs or much more traffic?**
Add a `club` foreign key to courts and the ledger, a shared cache (Redis) for rate limits, a task queue for messages instead of `on_commit`, and websockets or SSE for the kitchen screen.

### Limitations (say them before they ask)

Online refunds are recorded but returned from the Razorpay dashboard; no 5-minute payment hold; members book through the desk (no self-service portal booking yet); GST summary covers invoices only; payroll has no statutory deductions; per-process rate-limit cache; the kitchen screen polls every 10 seconds.
