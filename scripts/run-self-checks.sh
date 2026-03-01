#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

"$ROOT_DIR/tests/run-tests.sh"
"$ROOT_DIR/benchmarks/discover-sites-benchmark.sh"
