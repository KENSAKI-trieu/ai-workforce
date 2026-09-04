# Change Report - The HR Assistant Answers the Question It Was Asked

**Date:** 4 September 2026
**Scope:** 6 files - HR intent routing, directory filtering, the manager role set, and their tests

## Summary

With permissions settled, the next question was whether somebody who is allowed to read
employee records can actually get them out of the HR assistant. Asking it as a real user
would - list the directors, list the IT department, show me employee number 40 - showed
that the permission layer was doing its job and the layer above it was not.

Three requests came back wrong. One of them came back wrong quietly, which was the worst
of the three.

## What was broken

**Asking for one department listed the whole company.** "Danh sách nhân viên phòng IT"
returned all 41 employees under a sentence that read "Tôi tìm thấy 41 nhân viên". The
underlying query has always accepted a department filter; the routing layer simply never
passed one. Nothing failed, so nothing showed up as an error - the answer was just wrong,
and confidently so. The same phrasing for a department the company does not have
("phòng kế toán") also returned the whole company.

**Asking for an employee by name or number was not understood.** "Thông tin nhân viên số
40" was answered with "Tôi chưa xác định rõ nghiệp vụ HR cần thực hiện". Only six fixed
phrasings ("tìm nhân viên", "hồ sơ của", ...) were recognised, and "thông tin nhân viên"
was not one of them.

**The list of directors left out the director.** The manager directory filtered on a
hardcoded pair of role strings, `["Admin", "Manager"]`, written before the founder's role
became `CEO`. Asking the CEO for "danh sách quản lý" returned 5 people and silently
omitted the sixth - themselves. The word "giám đốc" was not recognised at all.

## What changed

**Departments are read from the question and passed to the query.** The assistant matches
what the user typed against the company's own department list - the code (`IT`), the full
name ("Phòng tuyển dụng"), and the name without its leading "Phòng" when the sentence
actually says "phòng". The reply now names the filter it applied, so the count and the
description can no longer disagree. A department the company does not have is reported as
such, together with the list of departments that do exist, instead of falling back to
everybody.

**More ways of naming a person are understood.** "Thông tin nhân viên", "chi tiết nhân
viên" and "xem thông tin nhân viên" now reach the employee lookup, and an identifier
introduced by "số" or "mã" is read as the identifier rather than as part of a name.
"Thông tin của tôi" stays a self-service request rather than becoming a search for
somebody called "tôi".

**The management list is read from the company's own org chart.** Instead of two
hardcoded role strings, the assistant asks which positions in this tenant carry
supervisory permissions and lists everyone holding them - the founder included, and any
job title the company created for itself. "Giám đốc", "ban giám đốc", "lãnh đạo" and
"trưởng phòng" now route to that list.

Scope is untouched by all of this. A filter can only narrow what a person was already
allowed to see: an employee asking for their department still sees exactly themselves.

## Measured before and after

Ten questions asked as three different people (founder, department manager, employee),
against a 41-person test company, driving the real assistant with its LLM router live.

| Question (asked by the founder) | Before | After |
|---|---|---|
| danh sách nhân viên phòng IT | 41 people | 8, all IT |
| liệt kê nhân viên phòng tuyển dụng | 41 people | 8, all HR |
| công ty có bao nhiêu nhân viên phòng Sale? | 41 people | 8, all SALE |
| danh sách nhân viên phòng kế toán | 41 people | reports the department does not exist |
| thông tin nhân viên số 40 | not understood | the record for Nhân Viên Test 40 |
| thông tin nhân viên Nhân Viên Test 40 | not understood | the same record |
| danh sách giám đốc | 5, founder missing | 6, founder included |

## Validation

- 33 new tests covering routing, department resolution, the supervisory role set, and the
  scope guarantee. All pass.
- Full backend suite: 13 failed / 379 passed, repeated three times with identical results.
  The same 13 fail on the unchanged branch (`test_dashboard` and 12 `test_hr_section_matrix`
  cases); they are unrelated to this work and predate it.
- One pre-existing source of flakiness was fixed along the way: a capability test rewrote
  the shared HR agent row and left it narrowed, so every later chat test in that module
  could fail with a permission error depending on run order. It now restores the row. That
  module went from 5 failures to none when run on its own.

## Still open, not changed here

**Reporting-tree scope is empty in practice.** `manager_id` is null for 40 of the 41 test
users, so a department manager holding `hr.scope.reports` sees only themselves. The cause
is in `backfill_tenant_user_positions`: it writes `position_id` directly instead of going
through `assign_position`, so the manager is never derived from the position tree. Fixing
the function will not repair the rows that already exist - those need a separate one-off
pass - so both are left for a decision rather than done silently.

**`hr.directory.view` is never checked on the directory path.** Access there is decided by
`hr.scope.company` / `hr.scope.reports` alone. The default tree always grants these
together so nothing is exposed today, but a company building its own positions could grant
scope without the directory permission and still get the full list.
