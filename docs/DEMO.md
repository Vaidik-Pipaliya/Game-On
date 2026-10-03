# Demo script (5 minutes) and judge questions

Before the demo: `manage.py seed_demo` has run on the live database, Razorpay **test** keys are set in Vercel, and you have two browser windows side by side: one signed in as the **owner**, one (private window) signed in as a **member**. To make a member account: in `/admin/` → Members open "Aarav Sharma" and set **User** to the Google account you use in the second window. Keep a terminal ready in the project folder.

## The script (strongest technical moments first)

| Time | Show | Say |
|---|---|---|
| 0:00 | Home page | "The club ran on WhatsApp, Excel and paper. This is one system for courts, members, shop, bar, website and money, on one PostgreSQL database and one ledger." |
| 0:20 | Terminal: `python manage.py test courts.tests.ConcurrencyTests -v 2` | "50 threads try to book Court 2 at 18:00 at the same instant. Exactly one wins. That's not Python checking; it's a PostgreSQL exclusion constraint, so even two servers can't double-book." |
| 0:45 | **Member window:** `/book/` → pick a free slot → confirm page shows *their* price → **Pay online now** | "Members book themselves. The price comes from one pricing function: member discount or a free hour. While they pay, the slot is **held for 5 minutes**." Pay with UPI `success@razorpay`; "My bookings" shows **Paid**. |
| 1:25 | Owner window: court grid; try to book that slot, or **Member window** books another slot, leave it unpaid | *"…is taken at 18:30. Pick another court or time."* "A held slot is a real booking status inside the exclusion constraint, so nobody can steal it during payment. 20 parallel attempts to hold one slot: exactly one wins (tested). Unpaid holds expire by themselves." |
| 1:55 | Member window: tap a taken slot → **Join the waitlist** | "If the slot opens up, the first person in line gets a 30-minute hold and an email. The offer is just a held booking, so it reuses the same guarantee." |
| 2:20 | Owner or member cancels the paid booking (more than 24 h ahead) | "Full refund, sent back **through Razorpay's refund API**. The ledger refund is written in the cancellation's transaction, the Razorpay call happens after commit, and if it fails the owner sees 'refunds waiting' with a Send-now button." The waitlisted member now has a held slot. |
| 2:50 | Owner: **Audit log** | "Who did what for refunds, stock, payroll, leave and role changes. Append-only: rows can't be edited or deleted, even from admin." |
| 3:10 | Bar → table T1 (open tab with a Gold member) | "The Gold member discount is an automatic line; nobody has to remember it. Split the bill: ₹500 cash + rest UPI." Kitchen screen: "Orders route to kitchen or bar and refresh by themselves." |
| 3:40 | Shop stock → item at 0 | "Counter and online orders share one stock. The update is `stock = stock - 1 WHERE stock >= 1`; the last pair can only be sold once (tested with 10 parallel buyers)." |
| 4:00 | Owner dashboard | "Every number is a sum of an append-only ledger, so it always matches the daily close: revenue by courts, shop, bar, memberships × cash, card, UPI, online; what we're owed; utilisation and peak hours from pandas." |
| 4:30 | Public site → Contact, then Book a trial (signs in with Google first) | "An enquiry becomes a lead, auto-assigned and emailed after commit; a hidden honeypot and a rate limit stop spam. A trial booking needs a verified Google account and allows one per person, so nobody can anonymously block courts." |
| 4:50 | Close | "322 tests, including real concurrency tests. Simple tools, with the hard rules in the database." |

If time is short, drop the bar and public-site rows; keep the first six (booking, hold, waitlist, refund, audit).

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

**How does the 5-minute hold work, and what if the payment arrives late?**
A `held` booking is a real status that the exclusion constraint covers (`status IN ('held','confirmed')`), so nobody can book the slot while a member pays. Expired holds are released whenever availability is read, so no scheduled job is needed for correctness. If the payment arrives after the hold expired, we re-confirm the booking when the slot is still free; if someone else took it, the payment is reversed in the ledger and returned through Razorpay.

**How does the waitlist avoid someone jumping the queue?**
When a slot frees up, the first person waiting gets a 30-minute *held booking*, which the database protects like any booking. If they don't confirm, it expires and the next person is offered. People who can't take it (daily limit) are skipped. A conditional UNIQUE constraint stops anyone queueing twice for the same slot.

**What if Razorpay is down when a booking is cancelled?**
The cancellation never waits for it. The ledger refund and a "refund pending" mark are saved in the cancellation's own transaction, so the owed refund can't be forgotten even if the server crashes. Razorpay is asked after the commit; failures show on the owner dashboard and are retried by a button and a daily job. A row lock and the stored refund id make sure a refund is only ever sent once.

**What is audited?**
Refunds and cancellations, stock restocks, shop order cancellations, empty-tab voids, payroll payments, leave decisions, invoice payments and role changes. The entry is written inside the same transaction as the change, and rows cannot be edited or deleted.

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

Refunds are full refunds only; staff-created online bookings don't use the 5-minute hold (the member portal does); waitlist offers are emailed, not sent on WhatsApp; GST summary covers invoices only; payroll has no statutory deductions; the rate-limit cache is per server instance; the kitchen screen polls every 10 seconds; the free hosting plans (Vercel Hobby, Neon free) are fine for a demo but a real club should move to paid plans.
