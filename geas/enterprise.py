"""geas.enterprise — Bootstrap de la estructura Enterprise (§43).

Genera el árbol declarativo de una organización a partir de lo que el
usuario declara (nombre, departamentos, repositorios). Es SOLO bootstrap:
la estructura versionada define el estado deseado y `geas sync` la aplica
a la base de datos. Aquí nunca se escribe estado operativo — ni tickets,
ni locks, ni ejecuciones; la base de datos de GEAS es su única fuente de
verdad.

Copier puede usarse como alternativa opcional de scaffolding (§35/§43),
pero no es una dependencia: este generador es stdlib pura y no arrastra
nada de Enterprise a una instalación Individual.
"""

from __future__ import annotations

from pathlib import Path

from geas.declarative import (
    build_structure,
    render_ci_workflow,
    render_structure,
)


def generate_enterprise(
    root: str | Path,
    org_name: str,
    *,
    profile: str = "enterprise",
    description: str = "",
    departments: list[str] | None = None,
    repositories: dict[str, list[str]] | None = None,
) -> list[Path]:
    """Crea el árbol declarativo §43 bajo `root` y devuelve lo escrito.

    No toca la base de datos: con `geas init --profile enterprise` +
    `geas sync` la estructura se aplica después (bootstrap → sync)."""
    struct = build_structure(
        org_name,
        profile=profile,
        description=description,
        departments=departments,
        repositories=repositories,
    )
    root = Path(root)
    written: list[Path] = []
    for rel, content in render_structure(struct).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(target)

    workflow = root / ".github" / "workflows" / "geas-sync.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(render_ci_workflow())
    written.append(workflow)
    return written