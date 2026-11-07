#!/bin/sh
set -eu
cd "$(dirname "$0")"
scenevoice_python="${SCENEVOICE_PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
    if ! "$scenevoice_python" -c 'import sys, tkinter; assert sys.version_info >= (3, 11)'; then
        printf '%s\n' 'Install Python 3.11 or newer with Tk support. See README.md.' >&2
        exit 1
    fi
    "$scenevoice_python" -m venv .venv
fi
if ! cmp -s requirements.txt .venv/scenevoice-requirements.txt; then
    .venv/bin/python -m pip --disable-pip-version-check install -r requirements.txt
    cp requirements.txt .venv/scenevoice-requirements.txt
fi
exec .venv/bin/python -m scenevoice "$@"
