#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/discover-sites.sh [--base-glob '/srv/apps/*'] [--output deploy/sites.json] [--dry-run]

Description:
  Scans each directory that matches --base-glob for a server.conf JSON file,
  validates required keys, normalizes entries, and writes deploy/sites.json.
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BASE_GLOB="/srv/apps/*"
OUTPUT_PATH="deploy/sites.json"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-glob)
      BASE_GLOB="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_cmd python3
PYTHON_ARGS=(discover --base-glob "$BASE_GLOB" --output "$OUTPUT_PATH")
if [[ "$DRY_RUN" -eq 1 ]]; then
  PYTHON_ARGS+=(--dry-run)
fi

python3 "$SCRIPT_DIR/config_contract.py" "${PYTHON_ARGS[@]}"
