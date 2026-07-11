"""Preflight checks before planning or running the board."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from board.ledger import Ledger


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def raise_or_none(self) -> None:
        if not self.ok:
            raise SystemExit(
                "Refusing to start:\n" + "\n".join(f"  ✗ {e}" for e in self.errors)
            )


def check_preconditions(
    ledger: Ledger,
    *,
    scope: list[str] | None = None,
    require_no_drafts: bool = True,
) -> PreflightResult:
    """Mirror loop-workflow / runner preconditions (charter, spec, doctor, drafts).

    scope: if set, only those slice ids (+ their deps) need contracts; other drafts ok.
    """
    errors: list[str] = []
    warnings: list[str] = []
    repo = ledger.repo
    kd = repo / ".kuru"

    if not kd.is_dir():
        errors.append(f"no .kuru workspace under {repo} — run: python3 scripts/kuru.py init")
        return PreflightResult(ok=False, errors=errors)

    if not (kd / "charter.md").is_file():
        errors.append("missing .kuru/charter.md — run /kuru:charter")
    else:
        text = (kd / "charter.md").read_text(encoding="utf-8", errors="replace")
        # Heuristic: template still full of placeholders is "not filled"
        if "<!--" in text and text.count("[") > 20:
            warnings.append("charter.md still looks like a template — fill it via /kuru:charter")

    spec_dir = kd / "spec"
    if not spec_dir.is_dir() or not any(spec_dir.glob("*.md")):
        # Accept legacy prd/ during migration
        prd_dir = kd / "prd"
        if not prd_dir.is_dir() or not any(prd_dir.glob("*.md")):
            errors.append("no specs under .kuru/spec/ — run /kuru:spec")
        else:
            warnings.append("found legacy .kuru/prd/ — rename to .kuru/spec/ when convenient")

    doc = ledger.doctor()
    # doctor prints OK + warnings to stdout; hard problems exit 1
    out = (doc.stdout or "") + (doc.stderr or "")
    if doc.returncode != 0:
        # Extract ✗ lines if present
        hard = [ln.strip() for ln in out.splitlines() if "✗" in ln]
        if hard:
            errors.extend(f"doctor: {h}" for h in hard)
        else:
            errors.append(f"kuru doctor failed:\n{out.strip()}")
    else:
        for ln in out.splitlines():
            if "⚠" in ln or "runs/" in ln:
                warnings.append(ln.strip())

    # Draft / scope rules need next --all
    try:
        board = ledger.next_all()
    except Exception as e:
        errors.append(f"cannot read board (kuru next --all --json): {e}")
        return PreflightResult(ok=False, errors=errors, warnings=warnings)

    drafts = {d["id"].upper() for d in board.get("draft") or []}
    if scope:
        scope_u = [s.upper() for s in scope]
        for sid in scope_u:
            if sid in drafts:
                errors.append(f"{sid} is still draft — run /kuru:slice")
    elif require_no_drafts and drafts:
        errors.append(
            "draft slices remain: "
            + ", ".join(sorted(drafts))
            + " — run /kuru:slice (or scope with --slices)"
        )

    return PreflightResult(ok=not errors, errors=errors, warnings=warnings)
