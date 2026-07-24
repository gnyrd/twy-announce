#!/usr/bin/env bash
# Thin wrapper to load env and run the reminder script.
# Intended for use from cron on the Hetzner host.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR%/scripts}"
cd "$REPO_ROOT"

PYTHON_BIN="$(command -v python3)"

"$PYTHON_BIN" scripts/send_class_email_reminders.py "$@"
