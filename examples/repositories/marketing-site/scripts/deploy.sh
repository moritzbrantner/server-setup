#!/usr/bin/env bash
set -euo pipefail

# Example deploy hook for app repositories.
# Keep this logic in the app repo so server-setup stays generic.
if [[ -d dist ]]; then
  echo "Build output ready in dist/"
fi
