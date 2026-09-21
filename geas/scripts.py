"""
geas.scripts — Generador de scripts del repositorio (spec §25).

Cada repositorio integrado dispone de status.sh, sync.sh, start.sh y
finish.sh. Son wrappers de la CLI work, para que cualquier persona o
agente pueda consultar el estado sin conocer la CLI de Geas.

Regla (§25):
    NOT READY → NO WORK
    READY     → WORK ALLOWED
"""

from __future__ import annotations

from pathlib import Path

STATUS_SH = """#!/usr/bin/env bash
# status.sh — ¿se puede trabajar en este repositorio? (spec §25)
#   ./status.sh          → READY TO WORK / NOT READY
#   ./status.sh --json   → salida estructurada para agentes
set -uo pipefail
if command -v geas >/dev/null 2>&1; then
  geas work status "${PWD}" "$@"
else
  exec uv run --project "$(git rev-parse --show-toplevel)/.." python -m geas.work status "${PWD}" "$@"
fi
"""

SYNC_SH = """#!/usr/bin/env bash
# sync.sh — sincroniza el repo con Geas y con Git (spec §24)
set -uo pipefail
if command -v geas >/dev/null 2>&1; then
  geas work sync "${PWD}"
else
  exec uv run --project "$(git rev-parse --show-toplevel)/.." python -m geas.work sync "${PWD}"
fi
"""

START_SH = """#!/usr/bin/env bash
# start.sh <ticket_id> — empieza a trabajar en un ticket (spec §24)
set -uo pipefail
if [ -z "$1" ]; then
  echo "Uso: ./start.sh <ticket_id>"
  exit 1
fi
if command -v geas >/dev/null 2>&1; then
  geas work start "$1" "${PWD}"
else
  exec uv run --project "$(git rev-parse --show-toplevel)/.." python -m geas.work start "$1" "${PWD}"
fi
"""

FINISH_SH = """#!/usr/bin/env bash
# finish.sh <ticket_id> — termina el trabajo en un ticket (spec §24)
set -uo pipefail
if [ -z "$1" ]; then
  echo "Uso: ./finish.sh <ticket_id>"
  exit 1
fi
if command -v geas >/dev/null 2>&1; then
  geas work finish "$1" "${PWD}"
else
  exec uv run --project "$(git rev-parse --show-toplevel)/.." python -m geas.work finish "$1" "${PWD}"
fi
"""

SCRIPTS = {
    "status.sh": STATUS_SH,
    "sync.sh": SYNC_SH,
    "start.sh": START_SH,
    "finish.sh": FINISH_SH,
}


def generate_scripts(repo_path: str | Path) -> list[Path]:
    """Genera los 4 scripts en la raíz del repositorio (§25).

    Género: script operativo de un repo. Los genera `work init`.
    """
    root = Path(repo_path)
    scripts_dir = root / ".orchestrator"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for name, content in SCRIPTS.items():
        script_path = root / name
        if script_path.exists():
            continue
        script_path.write_text(content)
        script_path.chmod(0o755)
        created.append(script_path)
    return created
