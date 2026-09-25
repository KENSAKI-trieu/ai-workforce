# Permission System: From Fixed Roles to a Company-Defined Job Tree

**Status report — 4 September 2026**

## Summary

We are replacing the fixed list of six user roles with a job tree that each company
builds for itself. A company can now name its own positions, arrange them in a hierarchy,
and tick exactly which permissions each position gets.

Three of the five work stages are finished and working. One is in progress. One has not
started.

---

## The problem

The system had six roles written directly into the code: Owner, Admin, Manager, Employee,
CEO, Guest. A customer could not add "Head of Sales" or rename anything.

This was hard to change for three reasons:

1. **The list was written in three separate places** that all had to match. Miss one and
   the system breaks.
2. **About 60 places in the code check the role directly**, spread across more than 20
   files. Most of them were copy-pasted rather than shared.
3. **Role names were also stored inside documents.** Every document that was restricted to
   certain roles saved the role name as text. Renaming a role would have silently cut
   people off from documents, with no error message anywhere.

Point 3 was the dangerous one. It shaped the whole design.

---

## What we built

The key idea is to **separate the job name from the job's power**.

| | Who controls it | Can it change? | What it is used for |
|---|---|---|---|
| **Name** | The company | Yes, anytime | What people see on screen |
| **Internal code** | The system | Never | Stored data, document access |
| **Permissions** | The company | Yes, tick boxes | Every access check in the code |
| **Position in tree** | The company | Yes | Reporting lines |

Because the internal code never changes, **renaming a position is completely safe**.
Documents stay accessible. This is proven by an automated test that renames a position and
then checks the document is still readable.

There are now 30 permissions grouped into 8 areas: Organisation, Workspace, AI, Knowledge,
HR, Approvals, Specialist, and Reporting.

### Safety rules built in

A company has no external super-user to rescue it, so four rules are enforced:

- **The top position always keeps every permission**, including permissions we add in
  future releases. Without this, each new feature would lock companies out of it.
- **The last administrator cannot be removed.** Any change that would leave nobody able to
  manage the company is rejected before it is saved.
- **No loops in the tree.** A position cannot be placed under itself or under one of its
  own sub-positions.
- **Nobody can grant a permission they do not have themselves.**

### Reporting lines are automatic

When a person is assigned to a position, their manager is worked out from the tree. If a
level is empty, the system looks further up rather than leaving the person without a
manager. A manager set by hand is never overwritten.

---

## Progress

The work was split into five stages. We did stages 1, 4 and 5 first because they deliver
the visible feature; stages 2 and 3 are internal clean-up that can follow safely.

| Stage | What it covers | Status |
|---|---|---|
| **1** | Database, permission list, safety rules, data migration | **Done** |
| **4** | API for creating and editing positions (7 endpoints) | **Done** |
| **5** | Screen for building the tree and ticking permissions | **Done** |
| **3** | Moving HR data rules onto permissions | **In progress** |
| **2** | Moving the ~60 remaining role checks onto permissions | **Not started** |

### What customers can already do

There is a new **Company Structure** page. The left side shows the job tree. Clicking a job
opens its details on the right: its name, its position in the tree, tick boxes for all 30
permissions grouped by area, and the list of people holding that job.

People without management permission can still view the chart but cannot change it. The
edit buttons are hidden for them.

### Data migration

Every existing company was migrated automatically. All existing users were placed into the
matching job with the same access they had before. Verified on live development data:
3 companies, 49 users, nobody left without a job.

---

## Test results

| | Count |
|---|---|
| Passing | 342 |
| Failing | 13 |

The 13 failures break down as follows:

- **12** belong to a test file we are still writing for stage 3. They are the unfinished
  part of today's work, not a defect in shipped code.
- **1** is a dashboard test that was already failing before this project started. We
  confirmed this by temporarily reverting all our changes and seeing the same failure. It
  is caused by old leftover data in the development database.

All other permission, HR and security tests pass.

---

## What is left

1. **Finish stage 3.** Move the HR data rules fully onto permissions and complete the test
   file. This also fixes a bug we found on the way: the old rules had department names such
   as "FINANCE" written into the code, while departments are already something companies
   define themselves. A company using a different department name silently lost access to
   salary data. The new design removes that whole class of bug.
2. **Do stage 2.** Convert the remaining ~60 role checks. This is repetitive but low-risk
   work, done one file at a time.
3. **Connect the assignment screen.** The API to assign a person to a job already exists,
   but the staff management page still uses the old role dropdown. These two need joining.

---

## Notes for the decision-makers

- **Nothing is broken for existing customers.** The old role field is still filled in and
  still works. The new system runs alongside it during the transition.
- **The riskiest part is already behind us.** Document access surviving a rename was the
  thing most likely to cause silent data-access failures, and it is now covered by tests.
- **Stage 2 should not be skipped.** Until it is done, the job tree controls HR data but
  not yet things like billing screens or workspace settings, which still read the old role
  field.
