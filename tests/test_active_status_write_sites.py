from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Independent sibling ratchet to test_in_position_write_sites.py (audit 39:
# the two cannot be merged without denaturing S2 into a behavioural test).
# This one is a pure text scan, exactly like S2: it inventories every
# update_setup_status(...) call whose literal target argument is one of the
# "ACTIF" statuses below, so a NEW site writing one of them anywhere in
# app/ must be reviewed and either routed through an existing guarded path
# or added here with a justification.
#
# Structural blind spot (documented, not fixed by this ratchet): a call
# that writes an ACTIF status via a *variable* computed earlier would be
# invisible to this scan, since the literal never appears inside the
# call's own parentheses. reconciliation.py's SUBMITTED branch used to be
# an example of this (it computed `target_status = SetupStatus.
# STOP_ORDER_PLACED.value if ... else SetupStatus.ENTRY_ORDER_PLACED.value`
# and wrote it directly) until A6-SEC (audit 51) replaced that write with a
# literal MANUAL_REVIEW_REQUIRED -- `target_status` now only feeds an event
# payload for traceability, never update_setup_status(). No ACTIF-status
# write remains in that branch, so this blind spot is currently only a
# structural risk for a *future* variable-target write, not a live gap.
# That site's alerting behaviour is covered by SubmittedBranchReviewLockTests
# (tests/test_reconciliation.py, S5b-1 + A6-SEC).
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        "ENTRY_ORDER_PLACED",
        "ENTRY_PARTIALLY_FILLED",
        "ENTRY_FILLED",
        "STOP_ORDER_PLACED",
        "STOP_PLACED",
        "IN_POSITION",
        "MANAGING_POSITION",
        "PARTIAL_EXIT",
        "RECONCILING_EXISTING_POSITION",
        # A-1b (audit 64): CLOSED joined repositories.py's _ACTIVE_STATUSES so
        # the review guard also protects it. Mirrored here so a future
        # literal update_setup_status(..., SetupStatus.CLOSED.value, ...)
        # call gets caught by this ratchet like any other ACTIF write.
        "CLOSED",
    }
)

ALLOWED_ACTIVE_WRITE_SITES: dict[str, str] = {
    "app/engine/order_manager.py": (
        "Three literal writes: place_entry_order() writes ENTRY_ORDER_PLACED "
        "after a bracket order is accepted (:180); place_stop_order() writes "
        "STOP_ORDER_PLACED when called with update_setup_status=True, which "
        "only happens from fill_executor's simulated-fill path (:374); "
        "attach_missing_stop() writes ENTRY_ORDER_PLACED after repairing a "
        "missing protective stop (:465-470), passing allow_from_review=True "
        "-- the one caller in the codebase authorised to cross the S5b-3a "
        "review guard (app/storage/repositories.py), and only in the branch "
        "where stop_order.status proves the repaired stop is CREATED or "
        "SUBMITTED at the broker (S5b-3b, audit 46). See "
        "tests/test_review_status_sticky.py for the behavioural coverage of "
        "each: place_entry_order's two callers gate on setup status upstream "
        "(unreachable from an alarm today); the simulated-fill cascade "
        "through place_stop_order does NOT check setup status and cannot "
        "overwrite MANUAL_REVIEW_REQUIRED/ERROR_REQUIRES_MANUAL_REVIEW since "
        "S5b-3a's central guard (documented as S5b-3 debt prior to S5b-3a, "
        "now blocked); attach_missing_stop is the sole legitimate exception "
        "to that guard, proven safe by AttachMissingStopReviewStickyTests."
    ),
    "app/engine/post_fill_progression.py": (
        "record_fill() writes ENTRY_FILLED unconditionally once a trailing "
        "stop is found (:64); mark_in_position() writes IN_POSITION when "
        "the caller passes protection_verified=True (:92). Both are called "
        "from two places: reconciliation.py's FILLED branch (gated on "
        "setup_status in {ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED} "
        "upstream, unreachable from an alarm) and fill_executor.py's "
        "simulate_fill_order (NOT gated on setup status at all -- see "
        "tests/test_review_status_sticky.py, confirmed able to overwrite "
        "both alarm statuses, S5b-3 debt)."
    ),
    "app/engine/reconciliation.py": (
        "Two literal writes: the existing-IBKR-position adoption loop in "
        "run() writes IN_POSITION once all adoption checks pass (:263) -- "
        "gated on _TERMINAL_SETUP_STATUSES (:162), which excludes "
        "MANUAL_REVIEW_REQUIRED, so this CAN and does overwrite it "
        "(confirmed by tests/test_review_status_sticky.py, S5b-3 debt); "
        "ERROR_REQUIRES_MANUAL_REVIEW is in that set and is protected. The "
        "FILLED branch's 'fill price/quantity unavailable' fallback writes "
        "ENTRY_FILLED before immediately overwriting it with "
        "MANUAL_REVIEW_REQUIRED (:507) -- gated upstream on setup_status in "
        "{ENTRY_ORDER_PLACED, ENTRY_PARTIALLY_FILLED} (:488-491), "
        "unreachable from an alarm. (The SUBMITTED branch no longer restores "
        "an ACTIF status for a terminal setup at all -- A6-SEC, audit 51, "
        "replaced that write with a literal MANUAL_REVIEW_REQUIRED, which is "
        "not an ACTIF status and therefore doesn't need a site here; the "
        "computed `target_status` variable only feeds an event payload now. "
        "See the module docstring above and SubmittedBranchReviewLockTests "
        "in tests/test_reconciliation.py, S5b-1 + A6-SEC.)"
    ),
}


def _find_update_setup_status_calls(text: str) -> list[tuple[int, str]]:
    """Return (start_index, call_text) for every update_setup_status(...) call
    in ``text``, with call_text spanning from the opening to the matching
    closing parenthesis (arguments here never contain unbalanced parens)."""
    calls = []
    for match in re.finditer(r"update_setup_status\(", text):
        open_paren = match.end() - 1
        depth = 0
        end = open_paren
        for i in range(open_paren, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        calls.append((match.start(), text[open_paren : end + 1]))
    return calls


class ActiveStatusWriteSiteRatchetTests(unittest.TestCase):
    def test_only_known_sites_write_an_active_status(self) -> None:
        found: dict[str, list[int]] = {}
        for path in APP_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for start, call_text in _find_update_setup_status_calls(text):
                if any(status in call_text for status in ACTIVE_STATUSES):
                    rel = path.relative_to(APP_ROOT.parent).as_posix()
                    line_no = text.count("\n", 0, start) + 1
                    found.setdefault(rel, []).append(line_no)

        unexpected = {
            file: lines
            for file, lines in found.items()
            if file not in ALLOWED_ACTIVE_WRITE_SITES
        }
        self.assertFalse(
            unexpected,
            "New direct write(s) of an ACTIF status found outside the "
            f"audited sites: {unexpected}. Either route the write through an "
            "already-guarded path, or add the site to "
            "ALLOWED_ACTIVE_WRITE_SITES here with a justification -- and add "
            "a behavioural test to tests/test_review_status_sticky.py "
            "proving it cannot overwrite a review-alarm status.",
        )
        for file in ALLOWED_ACTIVE_WRITE_SITES:
            self.assertIn(
                file,
                found,
                f"Expected {file} to still write an ACTIF status; the "
                "audited site seems to have moved or been removed.",
            )


if __name__ == "__main__":
    unittest.main()
