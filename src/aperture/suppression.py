"""Suppression comment parsing for Aperture.

Parses inline suppression comments in the format:
    -- aperture:ignore <RULE_ID> <reason>

A non-empty reason is mandatory. An ignore comment without a reason produces
a finding for rule MG000 (severity WARNING) and does not suppress the rule.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from aperture.models import Finding, ParsedStatement, Severity

# Pattern matches:
# -- aperture:ignore MG001 Some explanation here
# /* aperture:ignore MG001 Some explanation here */
_IGNORE_PATTERN = re.compile(
    r"(?:--|/\*)\s*aperture:ignore\b(?P<remainder>[^*\n\r]*)",
    re.IGNORECASE,
)


class SuppressionResult(NamedTuple):
    """Result of parsing suppressions for a statement."""

    suppressions: dict[str, str]  # rule_id -> reason
    findings: list[Finding]  # MG000 findings for malformed/missing reasons


def parse_suppressions(
    statement: ParsedStatement,
    file_path: str = "<string>",
) -> SuppressionResult:
    """Extract and validate suppression directives from statement comments.

    Args:
        statement: Parsed statement containing preceding comments.
        file_path: Migration file path for finding location.

    Returns:
        SuppressionResult with valid suppressions and any MG000 findings.
    """
    suppressions: dict[str, str] = {}
    findings: list[Finding] = []

    for comment in statement.comments:
        for line in comment.splitlines():
            match = _IGNORE_PATTERN.search(line)
            if not match:
                continue

            remainder = match.group("remainder").strip()
            parts = remainder.split(maxsplit=1)

            if not parts:
                findings.append(
                    Finding(
                        rule_id="MG000",
                        severity=Severity.WARNING,
                        message="Inline suppression comment is missing rule ID and reason.",
                        suggestion=(
                            "Specify the rule ID and reason, e.g.: "
                            "-- aperture:ignore MG001 Deprecated table"
                        ),
                        file=file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )
                continue

            rule_id = parts[0].strip().upper()
            reason = parts[1].strip() if len(parts) > 1 else ""

            if not reason:
                findings.append(
                    Finding(
                        rule_id="MG000",
                        severity=Severity.WARNING,
                        message=(
                            f"Inline suppression for rule '{rule_id}' "
                            "is missing a mandatory reason."
                        ),
                        suggestion=(
                            f"Add an explanation why this rule is suppressed, e.g.: "
                            f"-- aperture:ignore {rule_id} Backfilled in previous release"
                        ),
                        file=file_path,
                        line=statement.start_line,
                        statement_sql=statement.sql,
                    )
                )
            else:
                suppressions[rule_id] = reason

    return SuppressionResult(suppressions=suppressions, findings=findings)
