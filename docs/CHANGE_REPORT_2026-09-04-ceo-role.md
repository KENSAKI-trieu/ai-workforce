# Change Report - Roles Now Come From the Org Chart

**Date:** 4 September 2026
**Commits:** `e3efa1e`, `e4cd5af` - 19 files, +633 / -76

## Summary

Two things were out of step with each other. The person who creates a company was
stored as `"Owner"` in the database, but the org chart displayed their job as
`"CEO"` - the same person had two different names depending on which part of the
system you asked. Separately, the role dropdown on the **Employees & Permissions**
page was a fixed list written into the code, so job titles a company created for
itself never appeared there.

Both are fixed. The founder is now a CEO everywhere, and the role dropdown reads
the company's real org chart.

## What changed

**1. The founder's role is "CEO".** New sign-ups are stored as `CEO`, and a
database migration renames every existing `Owner` to `CEO`. Whenever someone is
placed in a position, their role label is now written from that position, so the
org chart and their real access can no longer drift apart.

**2. The "Role" dropdown reads the org chart.** The Employees & Permissions page
now loads positions from the company's own structure, including any the company
created itself. Changing someone's role there moves them in the org chart, which
means it changes their actual permissions - previously it only changed a label.

## Two bugs found along the way

These were not in the original request. Both were silent - nothing failed visibly,
people simply had no access and no explanation why.

- **`test@gmail.com` had no permissions at all.** The seed script created it with
  the role text `"CEO"` but never placed it in the org chart. Every permission
  check reads the org chart, so it answered "may do nothing" for everything. Fixed;
  re-running the seed script also repairs accounts created by the old version.

- **New employees were created with no position.** Adding someone through
  Employees & Permissions produced an account outside the org chart, so they had no
  permissions until an administrator placed them by hand. New accounts are now
  given a position when they are created.

## What people will notice

- The founder's job shows as **CEO** in the sidebar and account menu.
- The **Role** dropdown and filter list the company's own job titles, and update as
  soon as a title is added or renamed in Company Structure.
- Changing someone's role now genuinely changes what they can do.
- Requesting workspace deletion is the CEO's to do, as before - only the name of
  the role that may do it has changed.

## How to deploy

1. Deploy the new backend. It accepts both `Owner` and `CEO` during the changeover,
   so nobody loses access in the gap.
2. Run `alembic upgrade head`. This renames the role and, in the same transaction,
   grants `ceo` access to every document that was shared with `owner` - without
   that step the rename would quietly revoke documents from the founder.
3. Deploy the frontend.
4. Optional, for the demo workspace: run `python seed_test_company.py` to repair
   `test@gmail.com`.

The migration has a working `downgrade()`, so step 2 is reversible.

## Verification

Checked against the real database, not assumed:

- `test@gmail.com`: role `CEO`, position `CEO`, **30 of 30** permissions. All 41
  accounts in the demo workspace have a position; no `Owner` rows remain anywhere.
- End-to-end: created a new job title, assigned an employee to it, confirmed the
  title appeared in the dropdown and that the employee's access changed with it.
- Backend tests: **346 passed, 13 failed.** All 13 failures already existed on
  `main` before this work and were confirmed by checking out a clean tree. They are
  unrelated: one dashboard test compares against leftover development data, and
  twelve HR tests call an internal function with a missing argument.
- Frontend: type check clean, production build succeeds, no new lint warnings.

## Deliberately left alone

Roughly 25 permission checks accept `Owner` and `CEO` as equivalent and were not
touched. They already let a CEO through, and keeping `Owner` protects any workspace
whose data has not been migrated yet. Two pre-existing lint errors on the Company
Structure page are also outside this change.
