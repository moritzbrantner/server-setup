#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$SCRIPT_DIR/lib/test-helpers.sh"

SEED_SCRIPT="$ROOT_DIR/scripts/seed-example-repositories.sh"
DISCOVER_SCRIPT="$ROOT_DIR/scripts/discover-sites.sh"

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

test_discover_seeded_examples_normalizes_expected_sites() {
  local tmp
  tmp="$(make_temp_dir)"

  "$SEED_SCRIPT" --source-dir "$ROOT_DIR/examples/repositories" --target-dir "$tmp/apps" >/dev/null
  "$DISCOVER_SCRIPT" --base-glob "$tmp/apps/*" --output "$tmp/sites.json"

  assert_eq "complex-site rest-api simple-site" "$(jq -r 'map(.name) | sort | join(" ")' "$tmp/sites.json")"
  assert_eq "api.localhost app.localhost simple.localhost" "$(jq -r 'map(.domain) | sort | join(" ")' "$tmp/sites.json")"
  assert_eq "service" "$(jq -r '.[] | select(.name == "complex-site") | .runtime.mode' "$tmp/sites.json")"
  assert_eq "service" "$(jq -r '.[] | select(.name == "rest-api") | .runtime.mode' "$tmp/sites.json")"
  assert_eq "static" "$(jq -r '.[] | select(.name == "simple-site") | .runtime.mode' "$tmp/sites.json")"

  rm -rf "$tmp"
}

run_test "seed-example-repositories creates three standalone git repos" test_seed_examples_creates_expected_git_repositories
run_test "seed-example-repositories skips existing repos without --force" test_seed_examples_skips_existing_repositories_without_force
run_test "discover-sites normalizes the seeded sandbox examples" test_discover_seeded_examples_normalizes_expected_sites

echo "All tests passed: $pass_count"
