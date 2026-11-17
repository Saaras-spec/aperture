"""Console reporter for Aperture with colored and grouped terminal output."""

from __future__ import annotations

from collections import defaultdict

import typer

from aperture.models import Finding, Severity

_SEVERITY_COLORS = {
    Severity.ERROR: typer.colors.RED,
    Severity.WARNING: typer.colors.YELLOW,
    Severity.INFO: typer.colors.CYAN,
}


def _style_severity(severity: Severity, suppressed: bool = False) -> str:
    """Style severity label with color."""
    if suppressed:
        return typer.style("[SUPPRESSED]", fg=typer.colors.WHITE, dim=True)
    color = _SEVERITY_COLORS.get(severity, typer.colors.WHITE)
    return typer.style(f"[{severity.value}]", fg=color, bold=True)


def format_console(
    findings: list[Finding],
    fail_on: Severity | None = None,
) -> str:
    """Format findings into a colored terminal report grouped by file.

    Args:
        findings: List of findings to display.
        fail_on: Optional severity threshold for summary formatting.

    Returns:
        Formatted console report string.
    """
    if not findings:
        success_msg = typer.style(
            "✓ No migration risks detected! All checks passed.",
            fg=typer.colors.GREEN,
            bold=True,
        )
        return f"\n{success_msg}\n"

    # Group unbaselined findings by file path
    active_findings = [f for f in findings if not f.baselined]
    total_baselined = sum(1 for f in findings if f.baselined)

    by_file: dict[str, list[Finding]] = defaultdict(list)
    for f in active_findings:
        by_file[f.file].append(f)

    lines: list[str] = [""]

    total_errors = 0
    total_warnings = 0
    total_suppressed = 0

    for file_path, file_findings in by_file.items():
        file_header = typer.style(file_path, fg=typer.colors.MAGENTA, bold=True, underline=True)
        lines.append(file_header)

        for finding in file_findings:
            if finding.suppressed:
                total_suppressed += 1
            elif finding.severity == Severity.ERROR:
                total_errors += 1
            elif finding.severity == Severity.WARNING:
                total_warnings += 1

            line_str = typer.style(f"line {finding.line}:", fg=typer.colors.WHITE, dim=True)
            sev_badge = _style_severity(finding.severity, suppressed=finding.suppressed)
            rule_badge = typer.style(finding.rule_id, fg=typer.colors.BRIGHT_BLUE, bold=True)

            lines.append(f"  {line_str} {sev_badge} {rule_badge}: {finding.message}")

            if finding.suppressed and finding.suppression_reason:
                reason_styled = typer.style(
                    f"     Ignored: {finding.suppression_reason}",
                    fg=typer.colors.WHITE,
                    dim=True,
                )
                lines.append(reason_styled)

            # Statement snippet
            sql_snippet = " ".join(finding.statement_sql.strip().split())
            if len(sql_snippet) > 80:
                sql_snippet = sql_snippet[:77] + "..."
            lines.append(typer.style(f"     SQL: {sql_snippet}", fg=typer.colors.WHITE, dim=True))

            # Suggestion
            for s_line in finding.suggestion.splitlines():
                if s_line.strip():
                    lines.append(f"     💡 {s_line.strip()}")

            # AI-generated explanation
            if finding.explanation:
                lines.append(
                    typer.style(
                        "     🤖 AI-generated explanation:",
                        fg=typer.colors.CYAN,
                        bold=True,
                    )
                )
                for exp_line in finding.explanation.splitlines():
                    if exp_line.strip():
                        lines.append(f"        {exp_line.strip()}")

            lines.append("")

    # Summary line
    summary_parts: list[str] = []
    if total_errors > 0:
        summary_parts.append(
            typer.style(f"{total_errors} error(s)", fg=typer.colors.RED, bold=True)
        )
    if total_warnings > 0:
        summary_parts.append(
            typer.style(f"{total_warnings} warning(s)", fg=typer.colors.YELLOW, bold=True)
        )
    if total_suppressed > 0:
        summary_parts.append(
            typer.style(f"{total_suppressed} suppressed", fg=typer.colors.WHITE, dim=True)
        )
    if total_baselined > 0:
        summary_parts.append(
            typer.style(f"{total_baselined} baselined", fg=typer.colors.CYAN, dim=True)
        )

    file_count = len(by_file)
    summary_text = ", ".join(summary_parts) if summary_parts else "0 issues"
    summary_line = f"Summary: Found {summary_text} across {file_count} file(s)."
    lines.append(summary_line)

    if fail_on is not None:
        has_blocking = any(
            not f.suppressed and not f.baselined and f.severity >= fail_on for f in findings
        )
        if has_blocking:
            lines.append(
                typer.style(
                    f"Result: FAILED (violations at or above {fail_on.value})",
                    fg=typer.colors.RED,
                    bold=True,
                )
            )
        else:
            lines.append(
                typer.style(
                    "Result: PASSED",
                    fg=typer.colors.GREEN,
                    bold=True,
                )
            )

    return "\n".join(lines)
