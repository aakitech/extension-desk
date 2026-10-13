"""The officer approval gate: the only code that changes a queue item's
status. Every decision is written to the audit log inside the same queue
transaction, so an item is never approved or rejected without its entry.

Approval means the officer has signed off. Nothing is delivered from here:
sending the advisory to the household is the officer's own step.
"""

from extension_desk.agent.audit import AuditLog
from extension_desk.review_queue import ReviewQueue

DECISIONS = ("approve", "edit", "reject")


ANSWERED_VIA = ("prompt", "flag", "unspecified")


def decide(queue: ReviewQueue, audit: AuditLog, item_id: str, decision: str, officer: str,
           edits: list[str] | None = None, note: str = "", answered_via: str = "unspecified") -> dict:
    """Record an officer's decision on a pending advisory.

    `edit` replaces recommendation texts (one per recommendation, same
    order, citations kept); an empty text removes that line. Editing every
    line away is a rejection, not an approval of nothing.

    `answered_via` says how the decision reached the gate: `prompt` (the
    officer answered the draft in front of them) or `flag` (a fixed decision
    given on the command line, so no one read this draft). It is logged so a
    scripted approval can never pass for a reviewed one."""
    if answered_via not in ANSWERED_VIA:
        raise ValueError(f"answered_via must be one of {', '.join(ANSWERED_VIA)}")
    if not isinstance(officer, str) or not officer.strip():
        raise ValueError("a decision needs the officer's name")
    if edits is not None and not (isinstance(edits, list) and all(isinstance(t, str) for t in edits)):
        raise ValueError("edits must be a list of texts")
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
    item = queue.get(item_id)
    original = item["payload"]
    final = original
    status = "rejected" if decision == "reject" else "approved"
    if decision == "edit":
        recs = original.get("recommendations", [])
        if edits is None or len(edits) != len(recs):
            raise ValueError(f"edit needs one text per recommendation ({len(recs)})")
        kept = [{**r, "text": t.strip()} for r, t in zip(recs, edits) if t and t.strip()]
        if not kept:
            raise ValueError("every line was removed; reject the draft instead")
        final = {**original, "recommendations": kept}
    elif edits is not None:
        raise ValueError("edits are only accepted with decision='edit'")

    def log(decided: dict) -> None:
        audit.append(
            "officer_decision",
            item_id=item_id,
            household_id=decided["household_id"],
            decision=decision,
            status=status,
            officer=decided["decided_by"],
            answered_via=answered_via,
            inputs={"payload": original, "citations": decided["citations"],
                    "created_by": decided["created_by"], "created_at": decided["created_at"]},
            outputs={"final_payload": final, "delivered": False},
            note=note,
        )

    decided = queue.decide(item_id, status, officer, final, log)
    return {**decided, "decision": decision, "delivered": False}
