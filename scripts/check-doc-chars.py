#!/usr/bin/env python3
"""Barrido de caracteres no latinos en documentos de diagnostico.

Motivo (2026-10-02): al escribir en fallos_geas.md se colaron caracteres
raros (una 'o' acentuada al revés, palabras en chino donde debia haber
español). Un grep de las cadenas esperadas NO los encuentra: uno busca lo
que sospecha, no lo que hay. Este script recorre el fichero entero y avisa de
cualquier caracter ASCII imprimible... al reves, de cualquier caracter no
ASCII que no este en la lista blanca.

Salida: 0 si todo limpio, 1 si hay sospechosos (con linea y nombre Unicode).
Uso:    uv run python scripts/check-doc-chars.py [fichero ...]
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

# Caracteres no ASCII legitimos en estos documentos: acentos y enye
# castellana, signos de puntuacion, simbolos de estado usados a proposito.
LISTA_BLANCA = set(
    "áéíóúüñÁÉÍÓÚÜÑ"
    "¿¡«»·°…“”‘’"
    "€→←↔✓✗—–"
    "⚠️✅❌"
)

RUTAS_POR_DEFECTO = [
    Path.home() / "fallos_geas.md",
]


def sospechoso(ch: str) -> bool:
    if ord(ch) < 128:
        return False
    return ch not in LISTA_BLANCA


def revisar(ruta: Path) -> dict[str, list[int]]:
    hallazgos: dict[str, list[int]] = {}
    for num, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        for ch in linea:
            if sospechoso(ch):
                clave = f"U+{ord(ch):04X} {unicodedata.name(ch, '?')} {ch!r}"
                hallazgos.setdefault(clave, []).append(num)
    return hallazgos


def main(argv: list[str]) -> int:
    rutas = [Path(a) for a in argv[1:]] or RUTAS_POR_DEFECTO
    faltan = [r for r in rutas if not r.exists()]
    if faltan:
        for r in faltan:
            print(f"no existe: {r}")
        return 1

    sucio = False
    for ruta in rutas:
        hallazgos = revisar(ruta)
        if not hallazgos:
            print(f"{ruta}: limpio")
            continue
        sucio = True
        print(f"{ruta}: {len(hallazgos)} caracter(es) sospechoso(s)")
        for clave, lineas in sorted(hallazgos.items()):
            muestra = ", ".join(str(n) for n in lineas[:8])
            extra = f" (+{len(lineas) - 8})" if len(lineas) > 8 else ""
            print(f"  {clave} -> lineas {muestra}{extra}")
    return 1 if sucio else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))