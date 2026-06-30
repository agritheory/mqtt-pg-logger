#!/usr/bin/env bash
# Ensure .venv uses Python 3.12 (required by pyproject.toml).
# Run from project root: ./scripts/setup-venv.sh

set -e
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.12}"
if ! command -v "$PYTHON" &>/dev/null; then
  PYTHON="$(pyenv root)/versions/3.12.7/bin/python"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Python 3.12 not found. Install with: pyenv install 3.12.7"
  exit 1
fi

rm -rf .venv
"$PYTHON" -m venv .venv
poetry env use .venv/bin/python
poetry install
