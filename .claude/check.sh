#!/usr/bin/env bash
# The single command that must pass before work counts as done.
# Mirrors the CI "offline" job: Ruff plus the offline pytest suite.
# MySQL integration tests and the resource validator are intentionally excluded (they need a database or are not in CI).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
"$PY" -m ruff check .
"$PY" -m pytest -q -m "not integration"
