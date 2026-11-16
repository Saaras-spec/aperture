#!/bin/sh
set -e

# If running within GitHub Actions, delegate to the action_runner
if [ -n "$GITHUB_ACTIONS" ]; then
    exec python -m migrationguard.action_runner "$@"
else
    exec migrationguard "$@"
fi
