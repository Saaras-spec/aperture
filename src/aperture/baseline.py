"""Baseline mode: record and filter known legacy migration findings."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pglast
from pglast import stream

from aperture.models import Finding


def compute_statement_fingerprint(statement_sql: str) -> str:
    """Compute a stable SHA-256 fingerprint from normalized SQL statement text.

    Uses pglast's AST serializer to obtain canonical SQL tokens independent of
    whitespace, comments, line breaks, or punctuation spacing.
    """
    try:
        tree = pglast.parse_sql(statement_sql)
        if tree and hasattr(tree[0], "stmt") and tree[0].stmt is not None:
            canonical = str(stream.RawStream()(tree[0].stmt)).strip().lower()
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        pass

    # Fallback to regex-based token normalization
    cleaned = re.sub(r"/\*.*?\*/", " ", statement_sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", " ", cleaned)
    cleaned = re.sub(r"\s*([(),;])\s*", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower().rstrip(";")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def normalize_file_path(file_path: str | Path, base_dir: Path | None = None) -> str:
    """Normalize file path to a relative POSIX-style path (forward slashes) from base_dir."""
    p = Path(file_path)
    base = base_dir.resolve() if base_dir else Path.cwd().resolve()
    try:
        rel = p.resolve().relative_to(base)
        return rel.as_posix()
    except ValueError:
        return p.as_posix()


@dataclass
class BaselineFinding:
    """A single recorded finding in the baseline file."""

    rule_id: str
    file: str
    fingerprint: str


@dataclass
class Baseline:
    """Root model for an Aperture baseline file."""

    version: str = "1.0"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    findings: list[BaselineFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert baseline model to a dictionary suitable for JSON serialization."""
        return {
            "version": self.version,
            "created_at": self.created_at,
            "findings": [asdict(f) for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Baseline:
        """Construct Baseline instance from parsed dictionary data."""
        version = str(data.get("version", "1.0"))
        created_at = str(data.get("created_at", ""))
        raw_findings = data.get("findings", [])
        findings: list[BaselineFinding] = []
        if isinstance(raw_findings, list):
            for item in raw_findings:
                if isinstance(item, dict):
                    findings.append(
                        BaselineFinding(
                            rule_id=str(item.get("rule_id", "")),
                            file=str(item.get("file", "")),
                            fingerprint=str(item.get("fingerprint", "")),
                        )
                    )
        return cls(version=version, created_at=created_at, findings=findings)


def create_baseline(
    findings: Sequence[Finding],
    base_dir: Path | None = None,
) -> Baseline:
    """Create a Baseline instance from a sequence of active findings."""
    baseline_findings: list[BaselineFinding] = []
    for f in findings:
        norm_file = normalize_file_path(f.file, base_dir=base_dir)
        fp = compute_statement_fingerprint(f.statement_sql)
        baseline_findings.append(BaselineFinding(rule_id=f.rule_id, file=norm_file, fingerprint=fp))
    # Sort deterministically by file, rule_id, fingerprint
    baseline_findings.sort(key=lambda b: (b.file, b.rule_id, b.fingerprint))
    return Baseline(findings=baseline_findings)


def save_baseline(baseline: Baseline, output_path: str | Path) -> None:
    """Save Baseline to a JSON file on disk."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(baseline.to_dict(), fh, indent=2)
        fh.write("\n")


def load_baseline(baseline_path: str | Path) -> Baseline:
    """Load Baseline from a JSON file on disk."""
    path = Path(baseline_path)
    if not path.is_file():
        raise FileNotFoundError(f"Baseline file '{path}' not found.")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Baseline file '{path}' does not contain a JSON object.")
        return Baseline.from_dict(data)
    except json.JSONDecodeError as err:
        raise ValueError(f"Failed to parse baseline JSON file '{path}': {err}") from err


def apply_baseline(
    findings: Sequence[Finding],
    baseline: Baseline,
    base_dir: Path | None = None,
) -> list[Finding]:
    """Mark matching findings as baselined (f.baselined = True).

    Returns the list of all findings with baselined status updated.
    """
    counter: Counter[tuple[str, str, str]] = Counter()
    for b in baseline.findings:
        counter[(b.rule_id, b.file, b.fingerprint)] += 1

    result: list[Finding] = []
    for f in findings:
        norm_file = normalize_file_path(f.file, base_dir=base_dir)
        fp = compute_statement_fingerprint(f.statement_sql)
        key = (f.rule_id, norm_file, fp)

        if counter[key] > 0:
            counter[key] -= 1
            f.baselined = True
        else:
            f.baselined = False
        result.append(f)

    return result


def filter_findings_with_baseline(
    findings: Sequence[Finding],
    baseline: Baseline,
    base_dir: Path | None = None,
) -> tuple[list[Finding], list[Finding]]:
    """Partition findings into new findings and baselined findings.

    Returns:
        (new_findings, baselined_findings)
    """
    updated = apply_baseline(findings, baseline, base_dir=base_dir)
    new_findings = [f for f in updated if not f.baselined]
    baselined_findings = [f for f in updated if f.baselined]
    return new_findings, baselined_findings
