"""Markdown PR comment reporter for Aperture with summary table and collapsible details."""

from __future__ import annotations

from aperture.models import Finding, Severity

_MARKDOWN_MARKER = "<!-- aperture -->"

_SEVERITY_ICONS = {
    Severity.ERROR: "🔴 ERROR",
    Severity.WARNING: "🟡 WARNING",
    Severity.INFO: "ℹ️ INFO",
}


def format_markdown(findings: list[Finding]) -> str:
    """Format findings into a GitHub PR comment markdown document.

    Always begins with the hidden identifier <!-- aperture --> to enable
    subsequent GitHub Action workflow runs to locate and update existing comments.

    Args:
        findings: List of detected findings.

    Returns:
        Markdown formatted report string.
    """
    lines: list[str] = [
        _MARKDOWN_MARKER,
        "## 🛡️ Aperture Report",
        "",
    ]

    if not findings:
        lines.append("✅ **All migration safety checks passed! Zero risks detected.**")
        return "\n".join(lines)

    unsuppressed = [f for f in findings if not f.suppressed]
    if unsuppressed:
        error_count = sum(1 for f in unsuppressed if f.severity == Severity.ERROR)
        warning_count = sum(1 for f in unsuppressed if f.severity == Severity.WARNING)
        lines.append(
            f"❌ **Risks detected:** {error_count} error(s), {warning_count} warning(s). "
            "Please review safe alternatives below before merging."
        )
    else:
        lines.append(
            "⚠️ **All detected findings have been suppressed.** "
            "Please verify suppression reasons before merging."
        )

    lines.extend(
        [
            "",
            "### Summary",
            "",
            "| Severity | Rule | Location | Message | Status |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for f in findings:
        icon = (
            "⚪ SUPPRESSED" if f.suppressed else _SEVERITY_ICONS.get(f.severity, f.severity.value)
        )
        location = f"`{f.file}:{f.line}`"
        status = f"Suppressed: {f.suppression_reason}" if f.suppressed else "Active"
        # Sanitize pipes for markdown table
        clean_msg = f.message.replace("|", "\\|")
        lines.append(f"| {icon} | `{f.rule_id}` | {location} | {clean_msg} | {status} |")

    lines.extend(
        [
            "",
            "### Details & Safe Alternatives",
            "",
        ]
    )

    for idx, f in enumerate(findings, 1):
        status_tag = " (Suppressed)" if f.suppressed else ""
        loc_str = f"{f.file}:{f.line}"
        lines.append(
            f"<details>\n"
            f"<summary><b>Finding #{idx}: {f.rule_id} at {loc_str}</b>{status_tag}</summary>\n"
        )
        lines.append(f"**Message:** {f.message}\n")

        if f.suppressed and f.suppression_reason:
            lines.append(f"**Suppression Reason:** *{f.suppression_reason}*\n")

        lines.append(f"**Problematic SQL:**\n```sql\n{f.statement_sql.strip()}\n```\n")

        lines.append("**Safe Alternative:**")
        for sug_line in f.suggestion.splitlines():
            lines.append(f"> {sug_line}")

        if f.explanation:
            lines.append("\n**🤖 AI-generated explanation:**\n")
            lines.append(f.explanation.strip())

        lines.append("\n</details>\n")

    return "\n".join(lines)
