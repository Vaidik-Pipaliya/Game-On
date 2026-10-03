# PRD — Champions Club Platform

**Product:** Sports Club Management System
**Client:** The Champions Club
**Version:** 1.0 (draft for build)
**Date:** 3 Oct 2026

---

## 1. Summary

The Champions Club runs tennis and cricket courts, a gear shop, and a bar and cafeteria on WhatsApp, Excel and paper. Bookings collide, tabs get lost, stock is a guess, and the owner has no view of revenue. This product replaces all of it with one platform: a staff app, a member portal and a public website, all sharing one database, one stock shelf and one financial ledger.

## 2. Problem and goals

### Problems today
| Area | Today | Cost |
|---|---|---|
| Court bookings | WhatsApp and phone calls | Double bookings, lost time, frustrated members |
| Members | Excel sheets | No plan rules, no expiry tracking, no history |
| Bar | Paper receipts, lost tabs | Revenue leakage, slow service |
| Shop | No stock visibility | Missed sales, no reorder signal |
| Website | None | Lost enquiries and trial bookings |
| Finance | Nothing in one place | Owner cannot see earnings or obligations |

### Goals
1. **Zero double bookings** and a self-service booking flow that cuts front-desk phone time.
2. **Every member recognised in seconds** with plan, expiry and history visible to any staff member.
3. **One shelf of stock** for counter and online orders, with low-stock alerts.
4. **Bar service without paper:** orders, tabs, tables, kitchen tickets, discounts and closing totals.
5. **No lost enquiries:** website visitors can see the club and book a trial; every lead is tracked to a quote and a membership.
6. **One truth for money:** the owner sees earnings by source and method, and what is owed, for today, this week and this month.

### Non-goals (v1)
- Multi-club or franchise support.
- Native mobile apps (the portal is a responsive web app).
- Full statutory payroll filing and tax return submission (reports and exports only).
- Coaching academy management and tournament brackets.
- Hardware integrations beyond receipt printing and a QR scanner.

## 3. Users and personas

| Persona | Needs | Key moments |
|---|---|---|
| **Owner** (Rohan) | Revenue, costs, trends, shareable numbers | Month-end review |
| **Front-desk staff** | Fast member lookup, booking on behalf, walk-ins, phone calls | 6 pm rush |
| **Bar staff** | Quick orders, tabs, tables, payments | After matches |
| **Shop staff** | Sell, track stock, fulfil pickups | Pre-match gear emergency |
| **Manager** | Rota, leave approval, lead follow-up, reports | Weekly operations |
| **Accountant** | Invoices, tax, payroll exports | Month-end |
| **Member** (Gold, Silver, Junior) | Book, buy, order, see plan and expiry | From home on a phone |
| **Guardian** | Manage a Junior member's bookings and purchases | Weekends |
| **Visitor** | See plans, prices, availability, shop; book a trial | Searching online |

## 4. Scope and priority

**P0** = launch blocker, **P1** = needed within 60 days of launch, **P2** = later.

| Epic | P0 | P1 | P2 |
|---|---|---|---|
| Members and plans | Registration, plans, entitlements, expiry, history, search | Renewal reminders, guardian linking, QR card | Referral rewards |
| Court booking | Availability, booking, cancellation, pricing, daily limit, no double booking | Social sessions, waitlist, recurring bookings | Auto-release for no-shows |
| Shop and inventory | Catalog, stock, counter sales, low-stock alerts | Click and collect, delivery orders | Supplier purchase orders |
| Bar and cafeteria | Menu, tabs, tables, kitchen tickets, discounts, payments, daily takings | Member tabs, shifts | Recipe costing |
| Public website | Club info, plans, prices, availability, enquiry form | Trial booking, online shop | Blog and SEO tooling |
| Leads (CRM) | Capture, assign, follow-up, status | Quotes, convert to member | Campaign tracking |
| Finance | Payments, ledger, daily close, revenue by source | Invoices, corporate accounts, tax report | Accounting export |
| HR | Employees, shift rota | Leave requests and approval | Payroll runs and payslips |
| Reporting | Owner dashboard | Scheduled email and share links | Custom reports |

## 5. Scenarios and functional requirements

Requirement IDs are used in code, tests and PRs.

### 5.1 A new member walks in (Members)

**Story:** Front desk signs someone up in under two minutes. Anyone on staff can recognise them later and see their history.

| ID | Requirement | Priority |
|---|---|---|
| MB-01 | Register a member with name, phone, email, date of birth, photo (optional), emergency contact | P0 |
| MB-02 | Assign a plan: Gold, Silver or Junior. Junior requires age under 18 and a linked guardian | P0 |
| MB-03 | Each plan defines entitlements: court rate, free court hours per month, shop discount %, bar discount %, daily booking limit (default 2) | P0 |
| MB-04 | Membership has start and end dates. Status: active, expiring, expired, cancelled | P0 |
| MB-05 | System flags memberships expiring in 14, 7 and 1 days and notifies the member and staff. No one should need to remember expiry | P0 |
| MB-06 | Expired members fall back to walk-in pricing automatically. Data is never deleted | P0 |
| MB-07 | Global search by name, phone or member ID, reachable with `/` from any staff screen | P0 |
| MB-08 | Member profile shows bookings, purchases, bar tabs, payments, notes and visits in one timeline | P0 |
| MB-09 | Unique member ID with a QR code on a digital card, scannable at the desk | P1 |
| MB-10 | Plan change (upgrade, downgrade) effective immediately for future activity, with proration option | P1 |
| MB-11 | Renewal in one action with payment | P1 |

**Acceptance (MB-05):** A membership ending on 20 Oct creates reminders on 6, 13 and 19 Oct, and on 21 Oct the member's next booking is priced at the walk-in rate.

### 5.2 Booking a court on a busy evening (Booking)

**Story:** At 6 pm, front desk handles WhatsApp, a walk-in and a phone call. They see what is free and book in seconds. Members book themselves.

| ID | Requirement | Priority |
|---|---|---|
| BK-01 | Court grid: courts × half-hour slots for a chosen date and sport, with live status | P0 |
| BK-02 | A booking lasts one hour. New slots start every 30 minutes | P0 |
| BK-03 | No two bookings may overlap on the same court. Enforced at database level; conflicts return a clear error with alternatives | P0 |
| BK-04 | A member may hold at most two bookings per club-local day (counting held and confirmed, not cancelled). A staff override requires a permission and is audited | P0 |
| BK-05 | Price is calculated from sport, court, time and the member's plan. Gold and Silver pay less than walk-ins, and some hours can be free by plan entitlement | P0 |
| BK-06 | Walk-ins can be booked without a membership at the walk-in rate, with phone number captured | P0 |
| BK-07 | Booking by staff on behalf of a member, by member through the portal, or by visitor as a trial (P1) | P0 |
| BK-08 | Cancellation with a configurable window; refund or credit according to policy | P0 |
| BK-09 | Reschedule as cancel plus rebook in one action, keeping the original price if the same plan applies | P1 |
| BK-10 | Temporary hold of 5 minutes while payment completes, then auto-release | P0 |
| BK-11 | Block slots for maintenance, coaching, tournaments | P0 |
| BK-12 | **Friday social play:** on Friday evenings designated courts open for shared sessions with a capacity and per-person price. Participants join individually | P1 |
| BK-13 | Plan changes affect future bookings only. Past bookings keep their stored price | P0 |
| BK-14 | Booking confirmation by SMS, WhatsApp or email, plus a reminder 2 hours before | P1 |
| BK-15 | Waitlist for full slots with auto-offer on cancellation | P1 |
| BK-16 | Recurring weekly bookings, still obeying the daily limit | P2 |

**Acceptance (BK-03):** With 50 simultaneous requests for Court 2 at 18:00, exactly one succeeds and 49 receive a "slot taken" response.
**Acceptance (BK-04):** A member with two confirmed bookings today sees "You've reached the limit of 2 bookings today" on a third attempt; cancelling one allows a new booking.

### 5.3 Gearing up before a match (Shop and inventory)

**Story:** A racket string snaps ten minutes before play. Another member orders shoes from home and collects them at the club, or has them delivered.

| ID | Requirement | Priority |
|---|---|---|
| SH-01 | Catalog with categories: rackets, balls, shoes, accessories, apparel. Variants for size and colour | P0 |
| SH-02 | Stock on hand per variant, from an append-only movement history | P0 |
| SH-03 | Counter sale: scan or search, member discount applied automatically, take payment | P0 |
| SH-04 | Low-stock alert when available stock hits the reorder level | P0 |
| SH-05 | Manual stock adjustments and restocking with reason and audit | P0 |
| SH-06 | Online ordering by members through the portal, drawing from the same stock as the counter | P1 |
| SH-07 | Fulfilment choice: pickup at the club or delivery to an address | P1 |
| SH-08 | Online orders reserve stock when placed and release it on cancellation or time-out | P1 |
| SH-09 | Order status: placed, packed, ready for pickup or out for delivery, collected or delivered, cancelled; member is notified at each change | P1 |
| SH-10 | Out-of-stock variants show as unavailable online and at the counter in real time | P0 |
| SH-11 | Returns and exchanges with stock and ledger reversal | P1 |
| SH-12 | Supplier and purchase order tracking | P2 |

**Acceptance (SH-06 and SH-03):** With 1 pair of size 9 shoes left, a counter sale and an online order placed at the same moment: one succeeds, the other is told it is out of stock.

### 5.4 After the match, at the bar (Bar and cafeteria)

**Story:** Twenty people arrive at once. Orders must reach the kitchen, tabs must not get lost, and members expect their discount without asking.

| ID | Requirement | Priority |
|---|---|---|
| BR-01 | Menu with categories, prices, availability toggle and station routing (kitchen or bar) | P0 |
| BR-02 | Table management: free, occupied, tab open, bill requested | P0 |
| BR-03 | Orders attach to a table or a customer, added from any staff device, appear on the tab instantly | P0 |
| BR-04 | Kitchen and bar ticket screens show who ordered what and for which table; staff mark items as ready | P0 |
| BR-05 | Attach a member to a tab by search or QR; their bar discount is applied automatically as a visible line | P0 |
| BR-06 | Payment by cash, card or UPI; split a bill across methods or people | P0 |
| BR-07 | Run a tab and settle before leaving, or charge to the member's account for later settlement | P1 |
| BR-08 | Staff shifts: who is on, opening and closing cash count, per-shift sales | P1 |
| BR-09 | Daily bar close: total takings by payment method, discounts given, voids, open tabs | P0 |
| BR-10 | Voids and discounts beyond the automatic ones require manager permission and are audited | P0 |
| BR-11 | Printable or digital receipts | P0 |
| BR-12 | Ingredient-level inventory and recipe costing | P2 |

**Acceptance (BR-05):** A Gold member's tab with ₹1,000 of items shows a "Gold member discount" line at the plan percentage without any staff action.

### 5.5 A stranger finds the club online (Public website and leads)

**Story:** Someone searches for a place to play. They see the club, plans, prices, availability and the shop, and book a trial. Enquiries never vanish.

| ID | Requirement | Priority |
|---|---|---|
| WB-01 | Public website: club description, sports, location, opening hours, photos, contact | P0 |
| WB-02 | Plans page comparing Gold, Silver and Junior with prices and entitlements | P0 |
| WB-03 | Court pricing and a live view of free slots for the coming week | P0 |
| WB-04 | Shop browsing with live stock status | P1 |
| WB-05 | Enquiry form (name, phone, email, sport, message) with spam protection | P0 |
| WB-06 | Book a trial session online, with payment or pay-at-club | P1 |
| WB-07 | SEO basics: server rendered pages, structured data, fast mobile load, local-business markup | P0 |
| CR-01 | Every enquiry creates a lead with source and timestamp. No lead is lost | P0 |
| CR-02 | Lead is auto-assigned to a staff member who is notified immediately | P0 |
| CR-03 | Lead status pipeline: new, contacted, quoted, trial, won, lost with reason | P0 |
| CR-04 | Follow-up reminders and a task list per staff member; overdue leads highlighted | P0 |
| CR-05 | Generate and email a quote (membership plan, price, validity) | P1 |
| CR-06 | Convert a won lead into a member and membership in one action, carrying over details | P1 |
| CR-07 | Phone and walk-in enquiries can be logged manually into the same pipeline | P0 |

**Acceptance (CR-01 and CR-02):** A submitted enquiry shows as a new lead on the assigned manager's list within 10 seconds and triggers a notification.

### 5.6 The owner, at the end of the month (Finance, HR, Reporting)

**Story:** "How much did we earn, from where, and what do we owe?"

| ID | Requirement | Priority |
|---|---|---|
| FN-01 | Every payment from courts, shop, bar and memberships is recorded in one ledger with source and method (cash, card, UPI, online) | P0 |
| FN-02 | Owner dashboard: revenue today, this week, this month, compared with the previous period | P0 |
| FN-03 | Revenue breakdown by source (courts, shop, bar, memberships) and by payment method | P0 |
| FN-04 | "What we owe": payroll due, tax payable, outstanding supplier amounts | P1 |
| FN-05 | Daily close report per source with cash, card and UPI totals and reconciliation difference | P0 |
| FN-06 | Invoices for memberships and corporate (business) clients, with tax lines and PDF export | P1 |
| FN-07 | Corporate accounts with credit terms, consolidated monthly invoice, payment tracking | P1 |
| FN-08 | Tax report (GST summary by rate) exportable to CSV and PDF | P1 |
| FN-09 | Refunds and cancellations reflected in revenue with reversing entries | P0 |
| FN-10 | Share numbers: scheduled email summary and expiring read-only share link of a dashboard snapshot | P1 |
| FN-11 | Gateway settlement reconciliation with mismatch flags | P1 |
| HR-01 | Employee records with role, contact, joining date, pay basis | P1 |
| HR-02 | Shift rota by week with conflicts shown | P1 |
| HR-03 | Leave requests by employees, approval by manager, balance tracking; approved leave updates the rota | P1 |
| HR-04 | Monthly payroll run: gross, deductions, net, payslip PDF | P2 |
| HR-05 | Payroll expense appears in finance as an obligation and an expense | P2 |

**Acceptance (FN-03):** The owner opens the dashboard on the 31st and sees month revenue split into courts, shop, bar and memberships, each further split by cash, card, UPI and online, with the totals matching the daily close reports.

## 6. Cross-cutting requirements

| ID | Requirement |
|---|---|
| XC-01 | Role-based access: owner, manager, front desk, bar staff, shop staff, accountant, member, guest |
| XC-02 | Audit log for refunds, price overrides, stock adjustments, limit overrides, payroll, leave decisions |
| XC-03 | Notifications by email and SMS or WhatsApp, with templates and a delivery log |
| XC-04 | Live updates on the court grid, kitchen tickets and stock without page refresh |
| XC-05 | Export of key tables to CSV |
| XC-06 | Works on desktop and tablet for staff, on mobile for members and the public site |
| XC-07 | Indian context: ₹, GST, UPI, +91 phone default, IST timezone |
| XC-08 | Data backup and restore, with a documented recovery drill |
| XC-09 | WCAG 2.1 AA on member and public surfaces |

## 7. Business rules (summary)

1. A session is exactly one hour; start times are on :00 or :30.
2. Two bookings never overlap on the same court (Friday social sessions count as occupying the court).
3. A member has at most two bookings per club-local day.
4. Member rates apply only while the membership is active; otherwise the walk-in rate applies.
5. The price at confirmation is final; plan changes affect future bookings only.
6. Shop and online orders share one stock; online orders reserve stock until collected or cancelled.
7. Member discounts on shop and bar apply automatically.
8. Junior members need a linked guardian.
9. All money movements are recorded in the ledger through one service; records are never deleted, only reversed.

## 8. Success metrics

| Metric | Baseline | Target (90 days after launch) |
|---|---|---|
| Double bookings per month | Unknown, frequent | 0 |
| Share of bookings made without staff | 0% | 50% or more |
| Front-desk phone enquiries about availability | Constant | Down 60% |
| Bar tabs lost or unpaid | Unknown | Under 1% |
| Time to register a new member | Several minutes | Under 2 minutes |
| Enquiries logged vs received | Unknown | 100% logged, 90% followed up within 24 hours |
| Website enquiries per month | 0 | 30 or more |
| Stock-outs on top 20 SKUs | Unknown | Down 50% |
| Owner can state monthly revenue by source | No | Yes, within one minute |

## 9. Release plan

| Phase | Weeks | Contents |
|---|---|---|
| **0. Foundations** | 1–2 | Repo, auth, roles, design system tokens, CI, deployment |
| **1. Members and booking** | 3–6 | MB P0, BK P0, court grid, pricing, daily limit, payments basics |
| **2. Shop and bar** | 7–10 | SH P0, BR P0, stock ledger, POS, kitchen tickets, daily close |
| **3. Website and leads** | 11–12 | WB P0, CR P0, enquiry flow, notifications |
| **4. Owner view** | 13–14 | FN P0, owner dashboard, ledger reports |
| **Launch** | 15 | Data import from Excel, staff training, soft launch |
| **P1 wave** | 16–22 | Social sessions, online orders and delivery, invoices, corporate accounts, quotes, leave, tax report, share links |
| **P2 wave** | After | Payroll runs, recipe costing, recurring bookings |

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Staff keep using WhatsApp and paper | Low adoption, data gaps | Train at launch, make staff screens faster than the old way, run both for two weeks only |
| Double booking under load | Trust loss | Database exclusion constraint, concurrency tests |
| Stock mismatches from manual sales | Wrong availability | Counter sales through the system only, regular count with adjustment flow |
| Payment gateway outages | Lost sales | Cash and manual card/UPI recording as fallback, reconcile later |
| Bar rush with poor connectivity | Slow service | Optimistic UI, retry queue, tolerate a short offline moment |
| Scope creep from the broad brief | Late launch | Strict P0/P1/P2 and requirement IDs |
| Personal data of minors | Compliance risk | Guardian linking, minimal data, role-based access |

## 11. Assumptions and open questions

**Assumptions made:**
- One club, one location, club timezone Asia/Kolkata.
- Sports are configurable. The brief mentions both tennis and cricket and also padel and badminton, so the system supports any sport as a court type (cricket nets are modelled as bookable courts).
- Payment gateway is Razorpay, with cash and manual card or UPI recorded at the counter.
- Member discount percentages and court rates are settings the owner configures, not fixed in code.
- Delivery is by the club's own staff within a configurable radius, with a flat fee.

**Open questions for the owner:**
1. Which sports and how many courts of each are bookable at launch?
2. Exact plan prices, court rates, free hours per plan, and shop and bar discount percentages?
3. Cancellation window and refund policy?
4. Which Friday courts open for social play, capacity, and price per person?
5. Does the daily limit of two apply to social sessions and trial bookings? (Assumed yes for social, trial is exempt.)
6. Is delivery needed at launch, or pickup only?
7. GST registration details, invoice numbering format, and whether payroll needs statutory compliance (PF, ESI, TDS) in-system?
8. Number of staff devices and whether a receipt printer or kitchen display is available?
9. Can existing member data from Excel be provided for import, and in what condition?

## 12. Out of scope for v1

Native mobile apps, multi-club management, coaching and tournament modules, accounting-software integrations, loyalty points, and automated payroll statutory filing.
