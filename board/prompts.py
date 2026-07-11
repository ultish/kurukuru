"""Stage prompt construction — short slash-command forms for Claude.

Methodology lives in skills on disk; the board only names the stage + slice.
Other backends (Grok, cmd) may ignore these and build their own prompts.
"""

from __future__ import annotations

# stage name → /kuru:* command stem (not always identical)
_STAGE_CMD = {
    "check": "check-contract",
    "repair": "slice",  # human-heavy; board rarely dispatches this in Phase 1–2
    "build": "build",
    "verify": "verify",
    "review": "review",
    "ship": "ship",
}

ROLE = {
    "check": "critic",
    "repair": "planner",
    "build": "builder",
    "verify": "verifier",
    "review": "reviewer",
    "ship": "ship",
}


def stage_prompt(stage: str, slice_id: str) -> str:
    """Return the Claude slash-command prompt for a board stage."""
    sid = slice_id.upper()
    if stage == "ship":
        # Always defer git commit during a multi-slice board run.
        return f"/kuru:ship {sid} --no-commit"
    cmd = _STAGE_CMD.get(stage, stage)
    return f"/kuru:{cmd} {sid}"


def stage_role(stage: str) -> str:
    return ROLE.get(stage, stage)
