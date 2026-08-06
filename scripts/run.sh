#!/usr/bin/env bash
# Start the gesture server. Pass --simulate to disable real OS control.
set -euo pipefail
cd "$(dirname "$0")/.."
exec handgesture serve "$@"
