"""The next round of a contract review: a revised contract checked against the round before.

Reviewed afresh, a revised contract came back with medium and low findings that faulted
the very wording the previous round had proposed, and the rounds never settled: each
reading judges "is this clause as good as it could be", and a clause written as a
compromise the other side could sign never is. So the next round asks another question:

* A clause that did not change keeps what was found in it, and the decision taken on it.
* A risk the reviewer accepted (rejected the suggestion) is not raised again.
* For a clause that changed, the model is shown the old and the new text and each problem
  found in it, and says whether the change fixed it -- RESOLVED, PARTIAL or UNRESOLVED.
  It raises something new only where the change brought it in.

Which clause is which is the code's to work out (text matching); whether a change fixed a
problem is the model's call. When the model cannot be reached the caller reviews the
contract in full instead, so a round is never judged by the matching alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.agents.llm_json import UsageReporter, report_usage
from app.clients.ai_service_client import AIServiceClient
from app.domains.legal.contract_privacy import Pseudonymizer
from app.domains.legal.contract_review.llm_assessment import (
    _DATA_ONLY,
    _JUDGING,
    FAVORS,
    FINDING_TYPES,
    IMPACTS,
    MAX_FINDINGS,
    PERSPECTIVE_BRIEF,
    SEVERITIES,
    ProgressReporter,
    _Budget,
    _call,
    _choice,
    _code,
    _parse_findings,
    _progress,
    _reply,
    _revise,
    _text,
)

_VERIFY_TIMEOUT = 40.0
# A clause is the same clause, changed, when this much of its wording survives.
_SAME_CLAUSE_RATIO = 0.5
# Below this share of the previous round's clauses found again, the file is not a revision
# of that contract -- a mark copied into another document, or a rewrite -- and is reviewed
# in full.
MIN_MATCHED_SHARE = 0.5

VERDICTS = ("RESOLVED", "PARTIAL", "UNRESOLVED")
# What became of each finding of the previous round, and where each finding of this one
# came from; the reviewer sees it on every finding.
ROUND_STATUSES = ("CARRIED", "ACCEPTED_RISK", "PARTIAL", "UNRESOLVED", "UNVERIFIED", "NEW")

VERIFY_SYSTEM_PROMPT = f"""You are a Vietnamese commercial lawyer checking the next round of a
contract review for the side the user represents. {_DATA_ONLY} The contract was reviewed, the
reviewer revised it, and this is the revised text. The message holds the represented side, the
contract type and parties, every clause of the revised contract marked UNCHANGED, CHANGED or
ADDED since the last round, what changed (old and new wording; a removed clause has no new
wording), the problems the last round found that the change bears on, and the problems already
reported or accepted by the reviewer.

Reply with one JSON object and nothing else:
{{
  "verdicts": [
    {{
      "id": <the problem's id>,
      "status": "{'" | "'.join(VERDICTS)}",
      "severity": "{'" | "'.join(SEVERITIES)} -- of what is left; the problem's own when UNRESOLVED",
      "clause_id": "<id of the revised clause the problem is now in, or was fixed in; null when none>",
      "note": "<one sentence: what the change did about it, and what is left if anything>"
    }}
  ],
  "new_findings": [
    {{
      "clause_id": "<id of a CHANGED or ADDED clause; null for a missing clause>",
      "category": "<CODE>",
      "finding_type": "{'" | "'.join(FINDING_TYPES)}",
      "severity": "{'" | "'.join(SEVERITIES)}",
      "impact": "{'" | "'.join(IMPACTS)}",
      "favors": "{'" | "'.join(FAVORS)}",
      "confidence": <0.0-1.0>,
      "issue": "<one line>",
      "evidence": "<the exact words of the clause that carry the problem, copied verbatim>",
      "reason": "<why this matters for the represented side, 1-3 sentences>",
      "legal_basis": "<or null>",
      "recommendation": "<what to negotiate or change>"
    }}
  ]
}}

How to judge:
- One verdict for every problem given. The question is whether the revised contract still
  carries the risk the problem describes -- not whether the clause could be worded better
  still. RESOLVED: that risk is gone, even when the wording is not the one proposed.
  PARTIAL: the change reduced it and part of it is left; the note names that part and the
  severity is that of what is left. UNRESOLVED: the risk is still there.
- A problem whose proposed fix the reviewer applied (fix_applied) is RESOLVED unless the new
  wording still carries the risk; a fix is not faulted for being a compromise the other side
  could sign.
- A missing-clause problem is RESOLVED when a clause now covers it; give that clause's id.
- new_findings are only problems the change brought in: in words of a CHANGED or ADDED clause
  that were not in its old wording, a CHANGED or ADDED clause that now contradicts another
  clause (INTERNAL_CONFLICT), or a removed clause the contract needs (MISSING_CLAUSE,
  clause_id null). Never raise a problem in an UNCHANGED clause, never raise again -- in the
  same or other words -- a problem listed in problems or already_reported, and never raise a
  preference for other wording. An empty list when the change brought in nothing.
{_JUDGING}"""


# --------------------------------------------------------------------------- matching


_LEADING_NUMBER = re.compile(
    r"^\s*(?:(?:điều|article|clause|mục|khoản)\s+)?[0-9ivxlc]+(?:\.[0-9]+)*\s*[.:)\-–]?\s*",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    """Clause wording as compared across rounds: its number dropped, spacing and case ignored.

    The number goes because inserting a clause renumbers every clause after it without
    changing a word of them.
    """
    return re.sub(r"\s+", " ", _LEADING_NUMBER.sub("", str(text or ""), count=1)).strip().casefold()


def _similarity(first: str, second: str) -> float:
    a, b = first.split(), second.split()
    if not a or not b:
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    # quick_ratio bounds ratio from above and is cheap; most pairs stop here.
    if matcher.quick_ratio() < _SAME_CLAUSE_RATIO * 0.6:
        return 0.0
    return matcher.ratio()


@dataclass
class ClauseMatch:
    """How the previous round's clauses map onto the revised contract's."""

    new_for_old: dict[str, dict[str, Any]] = field(default_factory=dict)
    changed_old: dict[str, dict[str, Any]] = field(default_factory=dict)  # old id -> old clause, for changed ones
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed_new_ids(self) -> set[str]:
        return {self.new_for_old[old_id]["id"] for old_id in self.changed_old}

    def status_of(self, new_id: str) -> str:
        if new_id in self.changed_new_ids:
            return "CHANGED"
        if any(clause["id"] == new_id for clause in self.added):
            return "ADDED"
        return "UNCHANGED"


def match_clauses(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> ClauseMatch:
    """Pair each clause of the previous round with its clause in the revised contract.

    Identical wording first (in order, so repeated boilerplate pairs up in sequence), then the
    most similar remaining pairs, best first; a title that did not change counts for a lot.
    """
    match = ClauseMatch()
    old_text = {clause["id"]: _normalized(clause.get("text", "")) for clause in old}
    new_text = {clause["id"]: _normalized(clause.get("text", "")) for clause in new}
    unmatched_old = [clause["id"] for clause in old]
    by_text: dict[str, list[str]] = {}
    for old_id in unmatched_old:
        by_text.setdefault(old_text[old_id], []).append(old_id)
    unmatched_new: list[dict[str, Any]] = []
    for clause in new:
        candidates = by_text.get(new_text[clause["id"]]) or []
        if candidates and new_text[clause["id"]]:
            old_id = candidates.pop(0)
            unmatched_old.remove(old_id)
            match.new_for_old[old_id] = clause
        else:
            unmatched_new.append(clause)

    old_by_id = {clause["id"]: clause for clause in old}
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for clause in unmatched_new:
        for old_id in unmatched_old:
            score = _similarity(old_text[old_id], new_text[clause["id"]])
            same_title = _normalized(old_by_id[old_id].get("title", "")) == _normalized(clause.get("title", ""))
            if same_title and score:
                score = min(1.0, score + 0.25)
            if score >= _SAME_CLAUSE_RATIO:
                scored.append((score, old_id, clause))
    taken_new: set[str] = set()
    for _, old_id, clause in sorted(scored, key=lambda item: -item[0]):
        if old_id in match.new_for_old or clause["id"] in taken_new:
            continue
        match.new_for_old[old_id] = clause
        match.changed_old[old_id] = old_by_id[old_id]
        taken_new.add(clause["id"])
    match.added = [clause for clause in unmatched_new if clause["id"] not in taken_new]
    match.removed = [old_by_id[old_id] for old_id in unmatched_old if old_id not in match.new_for_old]
    return match


def revision_share(previous_result: dict[str, Any], clauses: list[dict[str, Any]]) -> float:
    """How much of the previous round's contract is found again in ``clauses``, 0 to 1."""
    old_clauses = list(previous_result.get("clauses") or [])
    if not old_clauses or not clauses:
        return 0.0
    return len(match_clauses(old_clauses, clauses).new_for_old) / len(old_clauses)


# --------------------------------------------------------------------------- the round


@dataclass
class _Problem:
    """A finding of the previous round the change bears on, to be judged by the model."""

    finding: dict[str, Any]
    new_clause: dict[str, Any] | None
    old_clause: dict[str, Any] | None
    fix_applied: bool
    proposed_fix: str


def _parties(previous: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """The parties as the previous round read them; rebuilt from its metadata for an older one."""
    if isinstance(previous.get("parties"), dict):
        return previous["parties"]
    metadata = previous.get("metadata") or {}
    parties: dict[str, dict[str, str | None]] = {}
    for key, slot in (("PARTY_A", "party_a"), ("PARTY_B", "party_b")):
        label = str(metadata.get(f"{slot}_label") or "")
        parties[key] = {
            "name": metadata.get(slot) if metadata.get(f"{slot}_source") == "DOCUMENT" else None,
            "role": label.split(" · ", 1)[1] if " · " in label else None,
        }
    return parties


def _taken_over(finding: dict[str, Any], clause: dict[str, Any] | None, status: str) -> dict[str, Any]:
    """A finding of the previous round, as this round's reading of the same clause."""
    evidence = str(finding.get("evidence") or "")
    if clause is None or evidence.casefold() not in str(clause.get("text", "")).casefold():
        evidence = ""
    return {
        "clause_id": clause["id"] if clause else None,
        "category": finding["category"],
        "finding_type": finding["finding_type"] if clause else "MISSING_CLAUSE",
        "severity": finding["severity"],
        "impact": finding.get("impact") or "SHARED",
        "favors": finding.get("favors"),
        "confidence": finding.get("confidence"),
        "issue": finding["issue"],
        "evidence": evidence,
        "reason": finding.get("reason") or "",
        "legal_basis": None,
        "sources": list(finding.get("sources") or []),
        "recommendation": finding.get("recommendation") or "",
        "suggested_revision": finding.get("suggested_revision") or "",
        "round_status": status,
        "parent_finding_key": finding.get("finding_key"),
    }


def reassess_contract(
    clauses: list[dict[str, Any]],
    *,
    previous: dict[str, Any],
    represented_party: str,
    client: AIServiceClient,
    budget: _Budget,
    on_usage: UsageReporter | None = None,
    on_progress: ProgressReporter | None = None,
) -> dict[str, Any] | None:
    """The revised contract read against the previous round, or None to review it in full.

    ``previous`` is the earlier review: ``result`` (its stored result) and ``decisions``
    (the reviewer's decisions on it); the caller has checked with ``revision_share`` that the
    text is a revision of it. None when the model could not judge what changed. The reading
    has the shape ``assess_contract`` returns, with ``round`` added: the summary of what
    became of each finding, and the findings the change resolved.
    """
    earlier = previous["result"] or {}
    old_clauses = list(earlier.get("clauses") or [])
    if not old_clauses or not clauses:
        return None
    match = match_clauses(old_clauses, clauses)
    decisions = {item["finding_key"]: item for item in previous.get("decisions") or []}

    taken_over: list[dict[str, Any]] = []
    problems: list[_Problem] = []
    touched = bool(match.changed_old or match.added or match.removed)
    for finding in earlier.get("findings") or []:
        decision = decisions.get(str(finding.get("finding_key") or ""), {})
        verb = decision.get("decision")
        old_id = finding.get("clause_id")
        new_clause = match.new_for_old.get(old_id) if old_id else None
        if verb == "REJECTED" and (old_id is None or new_clause is not None):
            # The reviewer saw it and chose to live with it: not raised again as a problem.
            taken_over.append(_taken_over(finding, new_clause, "ACCEPTED_RISK"))
            continue
        unchanged = old_id is not None and new_clause is not None and old_id not in match.changed_old
        if unchanged or (old_id is None and not touched):
            taken_over.append(_taken_over(finding, new_clause, "CARRIED"))
            continue
        problems.append(_Problem(
            finding=finding,
            new_clause=new_clause,
            old_clause=next((clause for clause in old_clauses if clause["id"] == old_id), None),
            fix_applied=verb in {"ACCEPTED", "EDITED"},
            proposed_fix=str(decision.get("revised_text") or finding.get("suggested_revision") or ""),
        ))

    changed_or_added = [*[match.new_for_old[old_id] for old_id in match.changed_old], *match.added]
    if not problems and not changed_or_added and not match.removed:
        _progress(on_progress, "VERIFY", "done", "Không có điều khoản nào thay đổi so với vòng trước")
        return _round_assessment(earlier, match, taken_over, [], [], unverified=False)

    hider = Pseudonymizer()
    hider.hide("\n".join(
        [clause["text"] for clause in clauses] + [clause["text"] for clause in old_clauses]
    ))  # learn every name first
    hide = hider.hide
    side = PERSPECTIVE_BRIEF.get(represented_party.upper(), PERSPECTIVE_BRIEF["NEUTRAL"])
    parties = _parties(earlier)
    payload = {
        "represented_side": side,
        "contract_type": earlier.get("contract_type_label") or earlier.get("contract_type"),
        "parties": {
            key: {field_name: hide(value) if isinstance(value, str) else value for field_name, value in party.items()}
            for key, party in parties.items()
        },
        "clauses": [
            {"id": clause["id"], "number": clause["number"], "title": hide(clause["title"]),
             "text": hide(clause["text"]), "status": match.status_of(clause["id"])}
            for clause in clauses
        ],
        "changes": [
            *[
                {"clause_id": match.new_for_old[old_id]["id"], "number": match.new_for_old[old_id]["number"],
                 "old_text": hide(old_clause["text"]), "new_text": hide(match.new_for_old[old_id]["text"])}
                for old_id, old_clause in match.changed_old.items()
            ],
            *[
                {"clause_id": None, "number": clause["number"], "old_text": hide(clause["text"]), "new_text": ""}
                for clause in match.removed
            ],
        ],
        "problems": [
            {
                "id": index,
                "clause_id": problem.new_clause["id"] if problem.new_clause else None,
                "old_clause_number": problem.old_clause["number"] if problem.old_clause else None,
                "category": problem.finding["category"],
                "finding_type": problem.finding["finding_type"],
                "severity": problem.finding["severity"],
                "issue": hide(str(problem.finding["issue"])),
                "reason": hide(str(problem.finding.get("reason") or "")),
                "recommendation": hide(str(problem.finding.get("recommendation") or "")),
                "fix_applied": problem.fix_applied,
                "proposed_fix": hide(problem.proposed_fix),
            }
            for index, problem in enumerate(problems)
        ],
        "already_reported": [
            {"clause_id": item["clause_id"], "category": item["category"], "issue": hide(item["issue"]),
             "accepted_by_reviewer": item["round_status"] == "ACCEPTED_RISK"}
            for item in taken_over
        ],
    }

    timeout = budget.timeout(_VERIFY_TIMEOUT)
    if timeout is None:
        _progress(on_progress, "VERIFY", "failed", "Hết thời gian trước khi đối chiếu với vòng trước")
        return None
    _progress(
        on_progress, "VERIFY", "running",
        f"{len(problems)} vấn đề vòng trước · {len(match.changed_old)} điều sửa · "
        f"{len(match.added)} điều thêm · {len(match.removed)} điều bỏ",
    )
    result = _call(client, VERIFY_SYSTEM_PROMPT, payload, timeout)
    if result:
        report_usage(on_usage, result)
    reply = _reply(result)
    if reply is None:
        _progress(on_progress, "VERIFY", "failed", "AI không đối chiếu được; rà soát lại toàn bộ")
        return None

    new_ids = {clause["id"] for clause in clauses}
    verdicts: dict[int, dict[str, Any]] = {}
    for item in reply.get("verdicts") or []:
        if not isinstance(item, dict) or type(item.get("id")) is not int or not 0 <= item["id"] < len(problems):
            continue
        status = _choice(item.get("status"), VERDICTS, None)
        if status is None:
            continue
        clause_id = str(item.get("clause_id") or "").strip() or None
        verdicts[item["id"]] = {
            "status": status,
            "severity": _choice(item.get("severity"), SEVERITIES, None),
            "clause_id": clause_id if clause_id in new_ids else None,
            "note": _text(item.get("note"), 400),
        }

    hidden_text = {clause["id"]: hide(clause["text"]) for clause in clauses}
    by_id = {clause["id"]: clause for clause in clauses}
    judged: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    unverified = False
    for index, problem in enumerate(problems):
        finding = problem.finding
        verdict = verdicts.get(index)
        clause = (by_id.get(verdict["clause_id"]) if verdict and verdict["clause_id"] else None) or problem.new_clause
        if verdict is None:
            unverified = True
            item = _taken_over(finding, clause, "UNVERIFIED")
            item["round_note"] = "AI chưa đối chiếu được vấn đề này với bản sửa; giữ nguyên kết quả vòng trước."
            taken_over.append(item)
            continue
        if verdict["status"] == "RESOLVED":
            resolved.append({
                "parent_finding_key": finding.get("finding_key"),
                "clause": finding.get("clause"),
                "clause_title": finding.get("clause_title"),
                "category": finding["category"],
                "severity": finding["severity"],
                "finding_type": finding["finding_type"],
                "issue": finding["issue"],
                "note": verdict["note"],
                "clause_id": clause["id"] if clause else None,
                "fix_applied": problem.fix_applied,
            })
            continue
        if verdict["status"] == "UNRESOLVED" and not problem.fix_applied:
            # Its fix was never applied, so the wording proposed for it still stands; writing
            # another one each round is the churn this module exists to stop. A missing
            # clause that is still missing is simply carried, with its decision.
            item = _taken_over(finding, clause, "UNRESOLVED" if finding.get("clause_id") else "CARRIED")
            item["round_note"] = verdict["note"]
            taken_over.append(item)
            continue
        item = _taken_over(finding, clause, verdict["status"])
        # Judged on the new wording, in the terms the model sees; revealed with the rest.
        item.update({
            "severity": verdict["severity"] if verdict["status"] == "PARTIAL" and verdict["severity"] else finding["severity"],
            "issue": hide(item["issue"]),
            "reason": hide(item["reason"]),
            "recommendation": hide(item["recommendation"]),
            "evidence": "",
            # The wording proposed last time did not settle it: a new one is written.
            "suggested_revision": "",
            "round_note": verdict["note"],
        })
        judged.append(item)

    allowed = {clause["id"]: hidden_text[clause["id"]] for clause in changed_or_added}
    fresh = _parse_findings(reply.get("new_findings"), allowed, False, allowed_types=FINDING_TYPES)
    for item in fresh:
        item["round_status"] = "NEW"
    new_findings = [*judged, *fresh]
    _progress(
        on_progress, "VERIFY", "done",
        f"{len(resolved)} đã xử lý · "
        f"{sum(item['round_status'] == 'PARTIAL' for item in judged)} xử lý một phần · "
        f"{sum(item['round_status'] == 'UNRESOLVED' for item in [*judged, *taken_over])} chưa đạt · "
        f"{len(fresh)} mới phát sinh",
    )

    revisions_missing = _revise(
        client, new_findings,
        hidden_text=hidden_text, side=side, parties=payload["parties"],
        budget=budget, on_usage=on_usage, on_progress=on_progress,
    )
    new_findings = hider.reveal(new_findings)
    resolved = hider.reveal(resolved)
    assessment = _round_assessment(earlier, match, taken_over, new_findings, resolved, unverified=unverified)
    assessment["revisions_missing"] = revisions_missing
    return assessment


def _round_assessment(
    earlier: dict[str, Any],
    match: ClauseMatch,
    taken_over: list[dict[str, Any]],
    judged: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    *,
    unverified: bool,
) -> dict[str, Any]:
    """The round in the shape ``review_contract`` reads a model's assessment in."""
    new_id = {old_id: clause["id"] for old_id, clause in match.new_for_old.items()}
    # A group the last round found missing and the change added: present now, in that clause.
    now_present = {
        item["category"]: item["clause_id"]
        for item in resolved
        if item["finding_type"] == "MISSING_CLAUSE" and item["clause_id"]
    }
    checklist: list[dict[str, Any]] = []
    for row in earlier.get("checklist") or []:
        category = _code(row.get("category"))
        if category is None:
            continue
        ids = [new_id[old] for old in row.get("clause_ids") or [] if old in new_id]
        status = row.get("status") or "MISSING"
        if status == "MISSING" and category in now_present:
            status, ids = "PRESENT", [now_present[category]]
        checklist.append({**row, "category": category, "status": status, "clause_ids": ids[:5]})
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    findings = sorted([*judged, *taken_over], key=lambda item: order.get(item["severity"], len(order)))
    counts = {status: sum(item.get("round_status") == status for item in findings) for status in ROUND_STATUSES}
    return {
        "contract_type": earlier.get("contract_type"),
        "contract_type_label": earlier.get("contract_type_label") or earlier.get("contract_type"),
        "contract_type_confidence": earlier.get("contract_type_confidence") or 0.5,
        "parties": _parties(earlier),
        "checklist": checklist,
        "findings": findings[:MAX_FINDINGS],
        "unreviewed_clauses": [],
        "revisions_missing": False,
        "round": {
            "summary": {"RESOLVED": len(resolved), **counts},
            "resolved_findings": resolved,
            "changed_clauses": len(match.changed_old),
            "added_clauses": len(match.added),
            "removed_clauses": len(match.removed),
            "unverified": unverified,
            "changed_clause_ids": sorted(match.changed_new_ids | {clause["id"] for clause in match.added}),
        },
    }
