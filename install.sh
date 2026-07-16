#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/skills/orchestrate-it-out/scripts/install" "$@"
