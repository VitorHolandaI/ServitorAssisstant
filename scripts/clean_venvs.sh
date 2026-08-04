#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

removed=0

while IFS= read -r -d '' marker; do
    venv_dir="${marker%/pyvenv.cfg}"
    venv_name="$(basename "$venv_dir")"

    case "$venv_name" in
        .venv|venv|env) ;;
        *) continue ;;
    esac

    case "$venv_dir" in
        "$ROOT_DIR"/*) ;;
        *)
            echo "Refusing to remove path outside repository: $venv_dir" >&2
            exit 1
            ;;
    esac

    if $DRY_RUN; then
        echo "Would remove: $venv_dir"
    else
        rm -rf -- "$venv_dir"
        echo "Removed: $venv_dir"
    fi
    ((removed += 1))
done < <(find "$ROOT_DIR" -type f -name pyvenv.cfg -print0)

if ((removed == 0)); then
    echo "No virtual environments found."
elif $DRY_RUN; then
    echo "$removed virtual environment(s) would be removed."
else
    echo "$removed virtual environment(s) removed."
fi
