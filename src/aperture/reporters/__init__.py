"""Reporters package for Aperture."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aperture.reporters.console import format_console
from aperture.reporters.json_reporter import format_json
from aperture.reporters.markdown import format_markdown
from aperture.reporters.sarif import format_sarif

if TYPE_CHECKING:
    from aperture.models import Finding, Severity

__all__ = [
    "format_console",
    "format_json",
    "format_markdown",
    "format_sarif",
]


def render_report(
    findings: list[Finding],
    format_name: str,
    fail_on: Severity | None = None,
) -> str:
    """Render findings into the requested output format string.

    Args:
        findings: List of detected findings.
        format_name: One of 'console', 'markdown', 'json', 'sarif'.
        fail_on: Optional severity threshold for console summary coloring.

    Returns:
        Formatted string representation.

    Raises:
        ValueError: If format_name is unrecognized.
    """
    fmt = format_name.strip().lower()
    if fmt == "console":
        return format_console(findings, fail_on=fail_on)
    if fmt == "markdown":
        return format_markdown(findings)
    if fmt == "json":
        return format_json(findings)
    if fmt == "sarif":
        return format_sarif(findings)

    raise ValueError(
        f"Unsupported report format '{format_name}'. "
        "Allowed formats: console, markdown, json, sarif."
    )
