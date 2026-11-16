# TODO: completed in Phase 4 (CLI & Reporting)
"""Command line interface for MigrationGuard."""

import typer

app = typer.Typer(
    name="migrationguard",
    help="Scan PostgreSQL migration files for dangerous operations.",
    add_completion=False,
)


@app.command()
def check() -> None:
    """Scan migration files for unsafe operations."""
    typer.echo("MigrationGuard check (to be implemented in later phases)")


@app.command()
def init() -> None:
    """Initialize a default migrationguard.yml configuration file."""
    typer.echo("MigrationGuard init (to be implemented in later phases)")


@app.command()
def list_rules() -> None:
    """List all available rules and descriptions."""
    typer.echo("MigrationGuard list-rules (to be implemented in later phases)")


@app.command()
def explain(rule_id: str) -> None:
    """Explain a specific rule and its safe alternative."""
    typer.echo(f"Explaining rule {rule_id} (to be implemented in later phases)")
