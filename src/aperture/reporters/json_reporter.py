"""JSON reporter for Aperture."""

from __future__ import annotations

import json
from typing import Any

from aperture.models import Finding, Severity


def format_json(findings: list[Finding], indent: int = 2) -> str:
    """Format findings into a structured JSON string.

    Includes all finding attributes and suppressed findings with reasons.

    Args:
        findings: List of detected findings.
        indent: Indentation spaces for JSON formatting.

    Returns:
        JSON formatted string.
    """
    total = len(findings)
    errors = sum(1 for f in findings if not f.suppressed and f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if not f.suppressed and f.severity == Severity.WARNING)
    info = sum(1 for f in findings if not f.suppressed and f.severity == Severity.INFO)
    suppressed = sum(1 for f in findings if f.suppressed)

    findings_data: list[dict[str, Any]] = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "message": f.message,
            "suggestion": f.suggestion,
            "file": f.file,
            "line": f.line,
            "statement_sql": f.statement_sql,
            "suppressed": f.suppressed,
            "suppression_reason": f.suppression_reason,
            "explanation": f.explanation,
        }
        for f in findings
    ]

    report = {
        "summary": {
            "total": total,
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "suppressed": suppressed,
            "passed": errors == 0,
        },
        "findings": findings_data,
    }

    return json.dumps(report, indent=indent)
