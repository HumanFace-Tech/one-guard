set -eu
: "${HERMES_SOURCE:?Set HERMES_SOURCE to the pinned upstream Hermes checkout}"
PYTHON="${PYTHON:-/tmp/opencode/one-guard-venv/bin/python}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$PYTHON" -m unittest discover -s "$ROOT/tests" -p 'test_hermes_integration.py' -v
