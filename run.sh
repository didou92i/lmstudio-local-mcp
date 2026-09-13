#!/bin/bash
set -euo pipefail
lm_project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$lm_project_dir"
if [[ ! -x "$lm_project_dir/.venv/bin/python" ]]; then
  echo 'Environnement absent : exécuter uv sync --frozen dans ce dossier.' >&2
  exit 1
fi
exec "$lm_project_dir/.venv/bin/python" -m lmstudio_mcp.server "$@"
