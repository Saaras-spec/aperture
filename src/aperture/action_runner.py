"""GitHub Action runner orchestrator for Aperture.

Reads GitHub Action inputs, performs migration checks, sets action outputs,
and posts or updates PR comments if enabled.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aperture.config import load_config
from aperture.discovery import discover_files
from aperture.engine import check_files, compute_exit_code
from aperture.github_comment import get_pr_context_from_env, post_or_update_comment
from aperture.models import Severity
from aperture.reporters import render_report


def _write_github_output(name: str, value: str) -> None:
    """Write an output parameter to the GitHub Actions output file."""
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path and Path(output_path).is_file():
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def run_action() -> int:
    """Main GitHub Action execution routine.

    Returns:
        Exit code (0 for pass, 1 for fail, 2 for error).
    """
    config_path = os.getenv("INPUT_CONFIG", "").strip() or None
    base_ref = os.getenv("INPUT_BASE_REF", "").strip() or None
    fail_on_str = os.getenv("INPUT_FAIL_ON", "ERROR").strip().upper()
    token = os.getenv("INPUT_GITHUB_TOKEN", "").strip()
    comment_on_pr = os.getenv("INPUT_COMMENT_ON_PR", "true").strip().lower() in ("true", "1", "yes")

    # 1. Load configuration
    try:
        cfg = load_config(config_path)
    except Exception as err:
        print(f"::error::Failed to load configuration: {err}", file=sys.stderr)
        _write_github_output("findings-count", "0")
        _write_github_output("passed", "false")
        return 2

    # Override fail_on from action input if provided
    try:
        fail_on = Severity(fail_on_str)
    except ValueError:
        fail_on = cfg.fail_on

    # 2. Discover migration files
    try:
        files = discover_files(config=cfg, base_ref=base_ref)
    except Exception as err:
        print(f"::error::File discovery failed: {err}", file=sys.stderr)
        _write_github_output("findings-count", "0")
        _write_github_output("passed", "false")
        return 2

    if not files:
        print("No migration files found to scan.")
        _write_github_output("findings-count", "0")
        _write_github_output("passed", "true")
        return 0

    # 3. Check migration files
    try:
        findings = check_files(files, cfg)
    except Exception as err:
        print(f"::error::Error during migration scanning: {err}", file=sys.stderr)
        _write_github_output("findings-count", "0")
        _write_github_output("passed", "false")
        return 2

    findings_count = len(findings)
    exit_code = compute_exit_code(findings, fail_on)
    passed = exit_code == 0

    # 4. Set action outputs
    _write_github_output("findings-count", str(findings_count))
    _write_github_output("passed", "true" if passed else "false")

    # 5. Print console report to action logs
    console_report = render_report(findings, "console", fail_on=fail_on)
    print(console_report)

    # 6. Post or update PR comment if enabled
    if comment_on_pr and token:
        pr_context = get_pr_context_from_env()
        if pr_context:
            repo, pr_number = pr_context
            markdown_body = render_report(findings, "markdown")
            try:
                cid = post_or_update_comment(token, repo, pr_number, markdown_body)
                print(f"Posted/updated PR comment #{cid} on {repo}#{pr_number}")
            except Exception as err:
                print(f"::warning::Failed to post PR comment: {err}", file=sys.stderr)
        else:
            print("Not running in a pull request context; skipping PR comment.")
    elif not token and comment_on_pr:
        print("github-token input not provided; skipping PR comment.")

    return exit_code


if __name__ == "__main__":
    sys.exit(run_action())
