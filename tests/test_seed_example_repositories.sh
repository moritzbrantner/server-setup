#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/test-helpers.sh
source "$SCRIPT_DIR/lib/test-helpers.sh"

# Initialized by test-helpers.sh; repeated here so ShellCheck sees it.
declare -i pass_count="${pass_count:-0}"

SEED_SCRIPT="$ROOT_DIR/scripts/seed-example-repositories.sh"

test_seed_examples_creates_expected_git_repositories() {
  local tmp
  tmp="$(make_temp_dir)"

  "$SEED_SCRIPT" --source-dir "$ROOT_DIR/examples/repositories" --target-dir "$tmp/apps" >"$tmp/seed.log"

  assert_eq "3" "$(find "$tmp/apps" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

  local repo
  for repo in simple-site rest-api complex-site; do
    [[ -d "$tmp/apps/$repo/.git" ]]
    assert_eq "main" "$(git -C "$tmp/apps/$repo" rev-parse --abbrev-ref HEAD)"
    [[ "$(git -C "$tmp/apps/$repo" rev-list --count HEAD)" -ge 1 ]]
  done

  rm -rf "$tmp"
}

test_seed_examples_skips_existing_repositories_without_force() {
  local tmp
  tmp="$(make_temp_dir)"

  "$SEED_SCRIPT" --source-dir "$ROOT_DIR/examples/repositories" --target-dir "$tmp/apps" >/dev/null
  "$SEED_SCRIPT" --source-dir "$ROOT_DIR/examples/repositories" --target-dir "$tmp/apps" >"$tmp/seed.log"

  grep -q "Skipped existing example repository: simple-site" "$tmp/seed.log"
  grep -q "Skipped existing example repository: rest-api" "$tmp/seed.log"
  grep -q "Skipped existing example repository: complex-site" "$tmp/seed.log"

  rm -rf "$tmp"
}

test_seed_examples_include_server_conf_contract() {
  local tmp
  tmp="$(make_temp_dir)"

  "$SEED_SCRIPT" --source-dir "$ROOT_DIR/examples/repositories" --target-dir "$tmp/apps" >/dev/null

  jq -e '.runtime.mode == "service"' "$tmp/apps/complex-site/server.conf" >/dev/null
  jq -e '.runtime.mode == "service"' "$tmp/apps/rest-api/server.conf" >/dev/null
  jq -e '.build_output == "public"' "$tmp/apps/simple-site/server.conf" >/dev/null

  rm -rf "$tmp"
}

run_test "seed-example-repositories creates three standalone git repos" test_seed_examples_creates_expected_git_repositories
run_test "seed-example-repositories skips existing repos without --force" test_seed_examples_skips_existing_repositories_without_force
run_test "seed-example-repositories includes the nested server.conf contract" test_seed_examples_include_server_conf_contract

echo "All tests passed: $pass_count"
