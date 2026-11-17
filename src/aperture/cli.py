"""Command line interface for Aperture powered by Typer."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from aperture.config import ConfigError, load_config
from aperture.discovery import DiscoveryError, discover_files
from aperture.engine import check_files, compute_exit_code
from aperture.parser import ParseError
from aperture.reporters import render_report
from aperture.rules import get_all_rules, get_rule

app = typer.Typer(
    name="aperture",
    help="Scan PostgreSQL migration files for dangerous operations.",
    add_completion=False,
    no_args_is_help=True,
)

_STARTER_CONFIG = """# Aperture Configuration
# See examples/aperture.yml for full options documentation.

fail_on: ERROR

migration_paths:
  - "migrations/**/*.sql"
  - "migrations/*.sql"

large_tables:
  - users
  - orders

rules:
  MG001:
    enabled: true
    severity: ERROR
  MG002:
    enabled: true
    severity: WARNING
  MG003:
    enabled: true
    severity: ERROR
  MG004:
    enabled: true
    severity: WARNING
  MG005:
    enabled: true
    severity: ERROR
  MG006:
    enabled: true
    severity: ERROR
  MG007:
    enabled: true
    severity: WARNING
  MG008:
    enabled: true
    severity: ERROR
  MG009:
    enabled: true
    severity: ERROR
  MG010:
    enabled: true
    severity: WARNING
"""

_RULE_EXPLANATIONS: dict[str, dict[str, str]] = {
    "MG001": {
        "why_risky": (
            "DROP TABLE and DROP COLUMN take an ACCESS EXCLUSIVE lock and cause immediate, "
            "irreversible data loss. Any active or rolled-back transactions can be broken."
        ),
        "safe_alternative": (
            "Follow a phased deprecation: stop referencing the table/column in application code, "
            "deploy and verify zero queries, and drop the relation in a subsequent release."
        ),
    },
    "MG002": {
        "why_risky": (
            "Renaming a table or column immediately breaks currently running application "
            "instances that execute queries using the old name during rolling deployments."
        ),
        "safe_alternative": (
            "Use the expand-contract pattern: add new column/table, dual-write to both, "
            "backfill data, switch reads to new name, and drop old column/table later."
        ),
    },
    "MG003": {
        "why_risky": (
            "ALTER TABLE ... ADD COLUMN ... NOT NULL without DEFAULT immediately fails on any "
            "non-empty table because existing rows would contain NULL."
        ),
        "safe_alternative": (
            "1. Add column as nullable.\n"
            "2. Backfill existing rows in batches.\n"
            "3. Add NOT NULL constraint with NOT VALID.\n"
            "4. Validate constraint in a separate transaction."
        ),
    },
    "MG004": {
        "why_risky": (
            "ALTER COLUMN SET NOT NULL scans the entire table under an ACCESS EXCLUSIVE lock, "
            "blocking all concurrent reads and writes for the duration of the scan."
        ),
        "safe_alternative": (
            "Add a CHECK constraint with NOT VALID: "
            "ALTER TABLE t ADD CONSTRAINT chk_not_null CHECK (col IS NOT NULL) NOT VALID; "
            "Then run: ALTER TABLE t VALIDATE CONSTRAINT chk_not_null;"
        ),
    },
    "MG005": {
        "why_risky": (
            "Standard CREATE INDEX acquires a SHARE lock, allowing reads but blocking all "
            "concurrent writes (INSERT, UPDATE, DELETE) until the index build finishes."
        ),
        "safe_alternative": (
            "Use CREATE INDEX CONCURRENTLY to build the index without blocking concurrent writes."
        ),
    },
    "MG006": {
        "why_risky": (
            "Adding a FOREIGN KEY without NOT VALID requires a full table scan while holding "
            "SHARE ROW EXCLUSIVE on referencing table, blocking all concurrent writes."
        ),
        "safe_alternative": (
            "Add the foreign key with NOT VALID, then validate it in a separate transaction "
            "with ALTER TABLE ... VALIDATE CONSTRAINT ...;"
        ),
    },
    "MG007": {
        "why_risky": (
            "Adding a CHECK constraint without NOT VALID performs a full table scan while "
            "holding an ACCESS EXCLUSIVE lock, blocking all concurrent reads and writes."
        ),
        "safe_alternative": (
            "Add the constraint with NOT VALID, then validate it separately with "
            "ALTER TABLE ... VALIDATE CONSTRAINT ...;"
        ),
    },
    "MG008": {
        "why_risky": (
            "ALTER COLUMN TYPE rewrites the entire table on disk and rebuilds all indexes "
            "while holding an ACCESS EXCLUSIVE lock, freezing all table queries."
        ),
        "safe_alternative": (
            "Add a new column with the target type, dual-write to both, backfill data, "
            "switch reads to the new column, and drop the old column later."
        ),
    },
    "MG009": {
        "why_risky": (
            "UPDATE or DELETE without a WHERE clause modifies or removes every row in the table, "
            "holding ROW EXCLUSIVE locks, flooding WAL, and causing accidental data loss."
        ),
        "safe_alternative": (
            "Add an explicit WHERE clause or batch updates/deletes in smaller transactions."
        ),
    },
    "MG010": {
        "why_risky": (
            "PostgreSQL lock requests enter a FIFO queue. ALTER TABLE waiting for a lock "
            "causes all incoming queries (even simple SELECTs) to queue behind it, "
            "quickly exhausting application connection pools and taking down the app."
        ),
        "safe_alternative": (
            "Add SET lock_timeout = '2s'; at the beginning of the migration file so Postgres "
            "aborts the migration if lock acquisition would stall incoming traffic."
        ),
    },
}


@app.command()
def check(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Migration files or directories to scan.",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to aperture.yml configuration file.",
        ),
    ] = None,
    base_ref: Annotated[
        str | None,
        typer.Option(
            "--base-ref",
            "-b",
            help="Git base ref to compare against HEAD via git diff.",
        ),
    ] = None,
    report_format: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output report format: console, markdown, json, sarif.",
        ),
    ] = "console",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write report output to specified file path instead of stdout.",
        ),
    ] = None,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain",
            help="Generate AI-powered plain-English risk explanations and migration plans.",
        ),
    ] = False,
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help="Path to baseline file (.aperture-baseline.json) to filter known legacy findings.",
        ),
    ] = None,
) -> None:
    """Scan PostgreSQL migration files for risky operations and policy violations."""
    # 1. Load configuration
    try:
        cfg = load_config(config)
    except ConfigError as err:
        typer.secho(f"Configuration error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    # 2. Discover migration files
    try:
        discovered = discover_files(
            paths=paths,
            config=cfg,
            base_ref=base_ref,
        )
    except DiscoveryError as err:
        typer.secho(f"File discovery error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    if not discovered:
        if report_format.lower() == "console":
            typer.echo("No migration files found to scan.")
        raise typer.Exit(code=0)

    # 3. Parse and analyze migration files
    try:
        findings = check_files(discovered, cfg)
    except ParseError as err:
        typer.secho(f"SQL parse error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    # Filter findings against baseline if provided
    if baseline:
        from aperture.baseline import apply_baseline, load_baseline

        try:
            bl = load_baseline(baseline)
            findings = apply_baseline(findings, bl)
        except (FileNotFoundError, ValueError) as err:
            typer.secho(f"Baseline error: {err}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from None

    # Optional AI explanation enrichment
    if explain:
        from aperture.explainer import enrich_findings_with_explanations

        findings = enrich_findings_with_explanations(findings, cfg)

    # 4. Render report
    try:
        report_text = render_report(findings, report_format, fail_on=cfg.fail_on)
    except ValueError as err:
        typer.secho(f"Report format error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    if output:
        try:
            output.write_text(report_text, encoding="utf-8")
        except OSError as err:
            typer.secho(f"Failed to write output file: {err}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from None
    else:
        typer.echo(report_text)

    # 5. Compute exit code
    exit_code = compute_exit_code(findings, cfg.fail_on)
    raise typer.Exit(code=exit_code)


@app.command(name="list-rules")
def list_rules() -> None:
    """List all available Aperture rules with their severities and descriptions."""
    rules = get_all_rules()

    typer.echo("")
    header = f"{'RULE ID':<10} {'DEFAULT':<10} {'NAME':<42} {'DESCRIPTION'}"
    typer.secho(header, bold=True)
    typer.secho("-" * 100, dim=True)

    for rule in rules:
        color = typer.colors.WHITE
        if rule.default_severity.value == "ERROR":
            color = typer.colors.RED
        elif rule.default_severity.value == "WARNING":
            color = typer.colors.YELLOW

        sev_styled = typer.style(f"{rule.default_severity.value:<10}", fg=color)
        id_styled = typer.style(f"{rule.id:<10}", bold=True)
        name_styled = f"{rule.name[:40]:<42}"
        desc_styled = rule.description[:45]
        typer.echo(f"{id_styled} {sev_styled} {name_styled} {desc_styled}")

    typer.echo("")


@app.command()
def init() -> None:
    """Initialize a default aperture.yml configuration file in current directory."""
    target_path = Path("aperture.yml")

    if target_path.exists() or Path("aperture.yaml").exists():
        typer.secho(
            "Error: Configuration file already exists. Refusing to overwrite.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        target_path.write_text(_STARTER_CONFIG, encoding="utf-8")
        typer.secho("Created aperture.yml successfully.", fg=typer.colors.GREEN, bold=True)
    except OSError as err:
        typer.secho(f"Failed to write configuration file: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    raise typer.Exit(code=0)


@app.command()
def explain(
    rule_id: Annotated[
        str,
        typer.Argument(
            help="Rule ID to explain (e.g. MG001).",
        ),
    ],
) -> None:
    """Explain a specific rule: why the operation is risky and safe alternatives."""
    target_id = rule_id.strip().upper()
    rule = get_rule(target_id)

    if not rule:
        typer.secho(
            f"Error: Rule '{rule_id}' not found. Run 'aperture list-rules' to view all rules.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    details = _RULE_EXPLANATIONS.get(
        target_id,
        {
            "why_risky": rule.description,
            "safe_alternative": "Refer to PostgreSQL documentation for safe migration patterns.",
        },
    )

    typer.echo("")
    typer.secho(f"Rule: {rule.id} - {rule.name}", bold=True, fg=typer.colors.BRIGHT_BLUE)
    typer.echo(f"Default Severity: {rule.default_severity.value}")
    typer.echo(f"Description:      {rule.description}")
    typer.echo("")
    typer.secho("Why is this risky in production?", bold=True, fg=typer.colors.YELLOW)
    typer.echo(f"  {details['why_risky']}")
    typer.echo("")
    typer.secho("Safe Alternative:", bold=True, fg=typer.colors.GREEN)
    for line in details["safe_alternative"].splitlines():
        typer.echo(f"  {line}")
    typer.echo("")


@app.command()
def baseline(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Optional specific SQL migration file paths or directories to scan.",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to aperture.yml configuration file.",
        ),
    ] = None,
    base_ref: Annotated[
        str | None,
        typer.Option(
            "--base-ref",
            "-b",
            help="Git base ref to compare against HEAD via git diff.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Path to write baseline JSON file.",
        ),
    ] = Path(".aperture-baseline.json"),
) -> None:
    """Record current migration findings into a baseline file."""
    # 1. Load config
    try:
        cfg = load_config(config)
    except ConfigError as err:
        typer.secho(f"Configuration error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    # 2. Discover files
    try:
        discovered = discover_files(paths=paths, config=cfg, base_ref=base_ref)
    except DiscoveryError as err:
        typer.secho(f"File discovery error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    if not discovered:
        typer.echo("No migration files found to baseline.")
        raise typer.Exit(code=0)

    # 3. Analyze
    try:
        findings = check_files(discovered, cfg)
    except ParseError as err:
        typer.secho(f"SQL parse error:\n{err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    # 4. Generate and save baseline
    from aperture.baseline import create_baseline, save_baseline

    bl = create_baseline(findings)
    try:
        save_baseline(bl, output)
    except OSError as err:
        typer.secho(f"Failed to write baseline file: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    typer.echo(f"Recorded {len(bl.findings)} finding(s) to {output}")
    raise typer.Exit(code=0)


def main() -> None:
    """CLI entry point callable directly."""
    app()


if __name__ == "__main__":
    sys.exit(app())
