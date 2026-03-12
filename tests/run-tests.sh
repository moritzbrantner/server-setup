#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

"$ROOT_DIR/tests/test_install_nginx_site.sh"
"$ROOT_DIR/tests/test_seed_example_repositories.sh"
"$ROOT_DIR/tests/test_discover_sites.sh"
"$ROOT_DIR/tests/test_sync_github_sites.sh"
"$ROOT_DIR/tests/run-python-tests.sh"
