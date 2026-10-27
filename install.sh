set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$ROOT/scripts/install.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$ROOT/scripts/install.py" "$@"
fi
printf '%s\n' 'Error: Python 3 is required. Install Python or activate the Hermes virtual environment.' >&2
exit 1
