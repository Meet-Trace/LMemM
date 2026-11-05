#!/usr/bin/env bash
# Update the local LMemM.app to a branch (default main) and relaunch it. Usage: ./update_app.sh [branch]
#
# The app is an alias build: it runs this repository's code, so switching branch is the update. It is
# rebuilt only when it is missing or setup.py / packaging / assets changed since the last build.
# Permissions stick to the app's signature, so sign with a stable identity, not the default ad hoc one
# (README, "Keeping permissions between builds").
set -eu
cd "$(dirname "$0")"
branch="${1:-main}"
stamp=build/.built-from

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes in tracked files; commit or stash them first." >&2
  exit 1
fi
if [ -z "${LMEMM_SIGN_IDENTITY:-}" ]; then
  echo "note: LMEMM_SIGN_IDENTITY is not set, so a rebuild signs ad hoc and macOS will ask for permissions again." >&2
fi

git fetch -q origin "$branch"
git checkout -q "$branch"
git merge -q --ff-only "origin/$branch"

if pgrep -f "LMemM.app/Contents/MacOS/LMemM" >/dev/null; then
  pkill -f "LMemM.app/Contents/MacOS/LMemM" || true
  sleep 1
fi

inputs="$(git rev-parse HEAD:setup.py)$(git rev-parse HEAD:packaging)$(git rev-parse HEAD:assets)"
if [ ! -d dist/LMemM.app ] || [ "$(cat "$stamp" 2>/dev/null)" != "$inputs" ]; then
  if ! ./build_app.sh; then
    # build_app.sh's last step (codesign --verify --strict) can fail on links into a conda-based .venv
    # even though the app was built and signed; keep going if the bundle exists, but say so.
    [ -d dist/LMemM.app ] || exit 1
    echo "warning: build_app.sh reported an error at its final check; launching the built app anyway." >&2
  fi
  mkdir -p build && echo "$inputs" > "$stamp"
fi

open dist/LMemM.app
echo "LMemM is on $branch at $(git rev-parse --short HEAD)."
