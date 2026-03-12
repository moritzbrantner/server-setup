#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./scripts/seed-example-repositories.sh [--source-dir DIR] [--target-dir DIR] [--force]

Description:
  Copies the sandbox example repositories into the target apps directory and
  initializes each one as its own standalone git repository on branch main.

Options:
  --source-dir DIR   Source templates directory (default: /opt/server-setup/examples/repositories)
  --target-dir DIR   Target apps directory (default: /srv/apps)
  --force            Replace existing seeded repos
  -h, --help         Show this help text
USAGE
}

SOURCE_DIR="/opt/server-setup/examples/repositories"
TARGET_DIR="/srv/apps"
FORCE=0
SANDBOX_REPOS=(simple-site rest-api complex-site)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir)
      SOURCE_DIR="${2:-}"
      shift 2
      ;;
    --target-dir)
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    --force)
      FORCE=1
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

copy_template() {
  local source_repo="$1"
  local target_repo="$2"

  mkdir -p "$target_repo"
  (
    cd "$source_repo"
    tar --exclude=.git -cf - .
  ) | (
    cd "$target_repo"
    tar --no-same-owner -xf -
  )
}

initialize_git_repo() {
  local target_repo="$1"

  git -C "$target_repo" init -q
  git -C "$target_repo" config user.email "sandbox@example.local"
  git -C "$target_repo" config user.name "server-setup sandbox"
  git -C "$target_repo" add .
  git -C "$target_repo" commit -qm "Initial sandbox example"
  git -C "$target_repo" branch -M main
}

main() {
  local created=0
  local skipped=0
  local replaced=0

  [[ -d "$SOURCE_DIR" ]] || { echo "Source directory not found: $SOURCE_DIR" >&2; exit 1; }
  mkdir -p "$TARGET_DIR"

  local repo_name
  for repo_name in "${SANDBOX_REPOS[@]}"; do
    local source_repo="$SOURCE_DIR/$repo_name"
    local target_repo="$TARGET_DIR/$repo_name"

    [[ -d "$source_repo" ]] || { echo "Example repository template not found: $source_repo" >&2; exit 1; }

    if [[ -e "$target_repo" ]]; then
      if [[ "$FORCE" -eq 1 ]]; then
        rm -rf "$target_repo"
        copy_template "$source_repo" "$target_repo"
        initialize_git_repo "$target_repo"
        echo "Replaced example repository: $repo_name"
        replaced=$((replaced + 1))
      else
        echo "Skipped existing example repository: $repo_name"
        skipped=$((skipped + 1))
      fi
      continue
    fi

    copy_template "$source_repo" "$target_repo"
    initialize_git_repo "$target_repo"
    echo "Created example repository: $repo_name"
    created=$((created + 1))
  done

  echo "Example repository seed summary: created=$created replaced=$replaced skipped=$skipped target_dir=$TARGET_DIR"
}

main "$@"
