"""SARIF 2.1.0 reporter for GitHub code scanning."""

from __future__ import annotations

import json
from typing import Any

from aperture.models import Finding, Severity
from aperture.rules import get_all_rules

_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)

_SEVERITY_TO_SARIF_LEVEL = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}


def _build_rule_descriptors() -> list[dict[str, Any]]:
    """Build SARIF rule metadata definitions from registered rules."""
    descriptors: list[dict[str, Any]] = []
    # Include generic suppression error rule MG000
    descriptors.append(
        {
            "id": "MG000",
            "name": "InvalidInlineSuppression",
            "shortDescription": {"text": "Invalid inline suppression syntax or missing reason"},
            "fullDescription": {
                "text": "Inline suppression comments require a non-empty explanation reason."
            },
            "defaultConfiguration": {"level": "warning"},
            "help": {
                "text": "Add a reason to suppression: -- aperture:ignore MGXXX <reason>",
                "markdown": "Add a reason: `-- aperture:ignore MGXXX <reason>`",
            },
        }
    )

    for rule in get_all_rules():
        level = _SEVERITY_TO_SARIF_LEVEL.get(rule.default_severity, "warning")
        name_clean = rule.name.replace(" ", "")
        descriptors.append(
            {
                "id": rule.id,
                "name": name_clean,
                "shortDescription": {"text": rule.name},
                "fullDescription": {"text": rule.description},
                "defaultConfiguration": {"level": level},
                "help": {
                    "text": f"{rule.name}: {rule.description}",
                    "markdown": f"### {rule.name}\n\n{rule.description}",
                },
            }
        )

    return descriptors


def format_sarif(findings: list[Finding], indent: int = 2) -> str:
    """Format findings into SARIF 2.1.0 JSON format for GitHub Code Scanning.

    Args:
        findings: List of detected findings.
        indent: Indentation spaces for JSON output.

    Returns:
        SARIF 2.1.0 JSON string.
    """
    rule_descriptors = _build_rule_descriptors()

    results: list[dict[str, Any]] = []
    active_findings = [f for f in findings if not f.baselined]
    for f in active_findings:
        sarif_level = _SEVERITY_TO_SARIF_LEVEL.get(f.severity, "warning")

        # Convert backslashes to forward slashes for cross-platform URIs
        file_uri = f.file.replace("\\", "/")

        result_obj: dict[str, Any] = {
            "ruleId": f.rule_id,
            "level": sarif_level,
            "message": {"text": f.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": file_uri,
                            "uriBaseId": "%SRCROOT%",
                        },
                        "region": {
                            "startLine": max(1, f.line),
                            "startColumn": 1,
                        },
                    }
                }
            ],
        }

        if f.suppressed:
            result_obj["suppressions"] = [
                {
                    "kind": "inSource",
                    "status": "accepted",
                    "justification": f.suppression_reason or "Suppressed via inline comment",
                }
            ]

        results.append(result_obj)

    sarif_doc = {
        "$schema": _SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Aperture",
                        "informationUri": "https://github.com/Saaras-spec/aperture",
                        "version": "0.1.0",
                        "rules": rule_descriptors,
                    }
                },
                "results": results,
            }
        ],
    }

    return json.dumps(sarif_doc, indent=indent)
