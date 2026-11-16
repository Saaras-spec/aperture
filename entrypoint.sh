#!/bin/sh
set -e

# If running within GitHub Actions, delegate to the action_runner
if [ -n "$GITHUB_ACTIONS" ]; then
    exec python -m aperture.action_runner "$@"
else
    exec aperture "$@"
fi
