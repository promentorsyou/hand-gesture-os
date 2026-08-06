#!/usr/bin/env bash
# Run the pipeline on synthetic gestures. No camera required.
set -euo pipefail
cd "$(dirname "$0")/.."
exec handgesture simulate "$@"
