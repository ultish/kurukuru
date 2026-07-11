"""Stage prompt construction for board backends.

- **Claude** — short slash-command forms (``/kuru:build SL-0001``) when the
  plugin is loaded via ``--plugin-dir``.
- **Grok** — self-contained prompts that point at skill files on disk and
  ``python3 $KURU_PY`` (Grok has no reliable Claude slash-command discovery).

Methodology lives in skills; the board only names the stage + slice + paths.
"""

from __future__ import annotations

from pathlib import Path

# stage name → /kuru:* command stem (not always identical)
_STAGE_CMD = {
    "check": "check-contract",
    "repair": "slice",  # human-heavy; board rarely dispatches this in Phase 1–2
    "build": "build",
    "verify": "verify",
    "review": "review",
    "ship": "ship",
}

# stage → skills/<name>/SKILL.md under the plugin root (Grok / skill-on-disk)
_STAGE_SKILL = {
    "build": "building-a-slice",
    "verify": "verifying-a-slice",
    "review": "reviewing-a-slice",
    "check": "checking-a-contract",
    # repair uses slicing-work when needed; ship is mechanical (no skill)
    "repair": "slicing-work",
}

ROLE = {
    "check": "critic",
    "repair": "planner",
    "build": "builder",
    "verify": "verifier",
    "review": "reviewer",
    "ship": "ship",
}


def stage_prompt_claude(stage: str, slice_id: str) -> str:
    """Return the Claude slash-command prompt for a board stage."""
    sid = slice_id.upper()
    if stage == "ship":
        # Always defer git commit during a multi-slice board run.
        return f"/kuru:ship {sid} --no-commit"
    cmd = _STAGE_CMD.get(stage, stage)
    return f"/kuru:{cmd} {sid}"


def stage_prompt(stage: str, slice_id: str, **_kwargs) -> str:
    """Default / Claude slash-command prompt (backward-compatible entry point)."""
    return stage_prompt_claude(stage, slice_id)


def skill_path_for(stage: str, plugin_dir: Path) -> Path | None:
    """Absolute path to the skill SKILL.md for a stage, or None if none."""
    name = _STAGE_SKILL.get(stage)
    if not name:
        return None
    p = Path(plugin_dir).resolve() / "skills" / name / "SKILL.md"
    return p


def stage_prompt_grok(
    stage: str,
    slice_id: str,
    *,
    plugin_dir: Path | str,
    kuru_py: Path | str,
) -> str:
    """Self-contained Grok prompt: skill on disk + kuru.py, no slash discovery.

    Keeps prompts short — “read skill X and do stage Y for slice Z” — and ends
    with ledger status rules the orchestrator re-checks after the process exits.
    """
    sid = slice_id.upper()
    plugin = Path(plugin_dir).resolve()
    kpy = Path(kuru_py).resolve()
    skill = skill_path_for(stage, plugin)
    kuru = f"python3 {kpy}"

    if stage == "ship":
        return (
            f"Ship Kurukuru slice {sid} without git commit.\n"
            f"Engine: {kuru}  (env KURU_PY={kpy}).\n"
            f"1. Run: {kuru} show {sid}\n"
            f"2. Run: {kuru} set-status {sid} done --no-commit\n"
            f"3. Confirm: {kuru} show {sid} --json  (status must be done).\n"
            f"Do NOT git commit — the board does one deferred commit after the run.\n"
            f"Exit when the ledger status is done (or report why ship was refused)."
        )

    skill_line = (
        f"1. Read {skill} and follow it for this stage only.\n"
        if skill is not None
        else "1. Perform this stage using kuru.py and the slice under .kuru/slices/.\n"
    )

    if stage == "build":
        status_rules = (
            f"4. You are the BUILDER only. When done: {kuru} set-status {sid} built --by builder.\n"
            f"   Do NOT set verified/reviewed/done. On genuine unblockable failure: set blocked with a note.\n"
        )
        role = "BUILDER"
    elif stage == "verify":
        status_rules = (
            f"4. You are the VERIFIER only (never the author of this build). "
            f"Record a verdict via the skill / kuru gates — typically "
            f"{kuru} set-status {sid} verified --by verifier  or  rejected --by verifier.\n"
            f"   Do NOT set done. Do NOT rebuild the slice yourself.\n"
        )
        role = "VERIFIER"
    elif stage == "review":
        status_rules = (
            f"4. You are the REVIEWER. Follow the skill; set reviewed or rejected per policy "
            f"(never set done / verified yourself unless the skill says so).\n"
        )
        role = "REVIEWER"
    elif stage == "check":
        status_rules = (
            f"4. You are the CONTRACT CRITIC. Follow the skill; leave ledger status as the skill directs.\n"
        )
        role = "CONTRACT CRITIC"
    else:
        status_rules = (
            f"4. Advance only this stage for {sid}; do not skip ahead in the pipeline.\n"
        )
        role = stage.upper()

    return (
        f"You are the {role} for Kurukuru slice {sid}.\n"
        f"Engine: {kuru}  (env KURU_PY={kpy}; CLAUDE_PLUGIN_ROOT={plugin}).\n"
        f"{skill_line}"
        f"2. Run: {kuru} show {sid}  (and inspect .kuru/slices/{sid}/ as needed).\n"
        f"3. Complete the **{stage}** stage for {sid} only — nothing else on the board.\n"
        f"{status_rules}"
        f"5. When finished: {kuru} show {sid} --json\n"
        f"The orchestrator trusts only the ledger status after your process exits — not your narration."
    )


def stage_prompt_for(
    backend: str,
    stage: str,
    slice_id: str,
    *,
    plugin_dir: Path | str | None = None,
    kuru_py: Path | str | None = None,
) -> str:
    """Select the prompt builder for a backend name (``claude`` / ``mock`` / ``grok``)."""
    name = (backend or "claude").lower()
    if name == "grok":
        if plugin_dir is None or kuru_py is None:
            raise ValueError("stage_prompt_for(grok) requires plugin_dir and kuru_py")
        return stage_prompt_grok(
            stage, slice_id, plugin_dir=plugin_dir, kuru_py=kuru_py
        )
    # mock ignores the prompt; claude and unknown default to slash form
    return stage_prompt_claude(stage, slice_id)


def stage_role(stage: str) -> str:
    return ROLE.get(stage, stage)
