# Merging the `next-features` branch into `main`

`main` (the live site) is untouched. These features were built on the branch `next-features` while the owner was away:

| Feature | Where to see it |
|---|---|
| Member self-service booking with a 5-minute database-enforced hold | `/book/` (sign in as a member) |
| Waitlist for taken slots with automatic 30-minute offers | tap a taken slot on `/book/` |
| Refunds through Razorpay's refund API | cancel an online-paid booking or shop order; owner dashboard shows "refunds waiting" |
| Append-only audit log | `/owner/audit/` |
| Every-page smoke test | `config/test_smoke.py` |

322 tests pass on the branch. Details and reasoning are in `docs/NOTES.md` (the sections after "Next-features branch").

## The safe order: migrate first, then merge

The new migrations only **add** things (new tables, new nullable columns, new columns with a database-level default, and a wider booking constraint), so the *old* code keeps working on the *new* database. New code on an old database would break (it reads columns that don't exist). So update the database first.

New migrations: `accounts 0002`, `courts 0005`, `courts 0006`, `finance 0005`.

1. **Migrate the live (Neon) database** from your laptop, with the *direct* (non-pooled) URL:
   ```powershell
   cd D:\Github\HVYP; $env:DATABASE_URL = "<direct-url>"
   ```
   ```powershell
   git checkout next-features
   ```
   ```powershell
   .venv\Scripts\python manage.py migrate
   ```
   Expect: `accounts.0002`, `courts.0005`, `courts.0006`, `finance.0005` applied. Then close that PowerShell window. The live site keeps working throughout.
2. **Merge into `main`** (this makes Vercel redeploy, ~2 to 4 minutes). Either:
   - open https://github.com/Vaidik-Pipaliya/Game-On/compare/main...next-features, click **Create pull request**, then **Merge**; or
   - from the project folder: `git push origin next-features:main`
3. **Check the live site:**
   - `/book/` loads for a member (a Google user whose email matches a Member record; in `/admin/` → Members you can set a member's **User** to your Google user to try it as, say, "Aarav Sharma").
   - Court grid and owner dashboard still load; the dashboard now has an "Audit log" link in its menu.
   - Book a court from `/book/` with **Pay online now** (needs Razorpay test keys), then cancel it from "My bookings" more than 24 hours ahead: the refund should reach Razorpay and the booking shows as cancelled.

No new environment variables are needed. `vercel.json` gains one daily cron (`/cron/retry-refunds/`); the cron-job.org jobs don't change.

## If something goes wrong

Undo the merge on `main` (`git revert -m 1 <merge-commit>` and push, or revert in GitHub). Because the migrations only add things, the old code runs fine on the migrated database, so there is no need to roll the database back.
