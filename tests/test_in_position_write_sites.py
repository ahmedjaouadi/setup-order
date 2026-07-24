from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# The real safety invariant behind audit 28 / S2's ENTRY_FILLED -> IN_POSITION
# table entry is NOT the transition table: it is that IN_POSITION is only
# ever written by PostFillProgression.mark_in_position, which refuses to
# write unless protection_verified=True. Every other known direct writer is
# listed here, with its justification. Any new direct write of IN_POSITION
# discovered anywhere else in app/ must fail this test until it is either
# routed through mark_in_position or added here with a reason.
ALLOWED_WRITE_SITES: dict[str, str] = {
    "app/engine/post_fill_progression.py": (
        "mark_in_position(): the only writer gated on protection_verified=True, "
        "shared by both the real bracket path (reconciliation.py) and the "
        "simulated path (fill_executor.py)."
    ),
    "app/engine/reconciliation.py": (
        "Existing IBKR position adoption: RECONCILING_EXISTING_POSITION -> "
        "IN_POSITION for a position discovered already open at startup. A "
        "distinct transition from the post-fill path this ratchet guards."
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


class InPositionWriteSiteRatchetTests(unittest.TestCase):
    def test_only_known_sites_write_in_position(self) -> None:
        found: dict[str, list[int]] = {}
        for path in APP_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for start, call_text in _find_update_setup_status_calls(text):
                if "IN_POSITION" in call_text:
                    rel = path.relative_to(APP_ROOT.parent).as_posix()
                    line_no = text.count("\n", 0, start) + 1
                    found.setdefault(rel, []).append(line_no)

        unexpected = {
            file: lines for file, lines in found.items() if file not in ALLOWED_WRITE_SITES
        }
        self.assertFalse(
            unexpected,
            "New direct write(s) of IN_POSITION found outside the audited "
            f"sites: {unexpected}. Either route the write through "
            "PostFillProgression.mark_in_position (which requires "
            "protection_verified=True), or add the site to "
            "ALLOWED_WRITE_SITES here with a justification.",
        )
        for file in ALLOWED_WRITE_SITES:
            self.assertIn(
                file,
                found,
                f"Expected {file} to still write IN_POSITION; the audited "
                "site seems to have moved or been removed.",
            )


if __name__ == "__main__":
    unittest.main()
