"""Migra datos de SQLite a MongoDB.

Lee de la BD SQLite actual (~/.local/share/geas/geas.db) y exporte
todo a MongoDB (la colección correspondiente a cada tabla).

Uso:
    cd /home/cancelas/geas && python -m geas.db_migrate
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

from pymongo import MongoClient


def get_client() -> MongoClient:
    # .env esta un nivel arriba: /home/cancelas/geas/.env
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        env_path = Path(__file__).parent / ".env"
    uri: str | None = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("MONGODB_URI="):
                uri = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not uri:
        print("ERROR: falta MONGODB_URI en .env", file=sys.stderr)
        sys.exit(1)
    return MongoClient(uri, serverSelectionTimeoutMS=10000)


def get_sqlite_connection() -> sqlite3.Connection:
    # Determinar la ruta de la BD SQLite (igual que dbpath.py)
    geas_db = None
    geas_data_dir = None
    default_path = Path.home() / ".local" / "share" / "geas" / "geas.db"

    for line in open("/dev/null", "r"):  # noqa: SIM115 — placeholder
        pass

    import os
    explicit = os.environ.get("GEAS_DB")
    if explicit:
        return sqlite3.connect(explicit)
    data_dir = os.environ.get("GEAS_DATA_DIR")
    if data_dir:
        return sqlite3.connect(Path(data_dir) / "geas.db")
    if default_path.exists():
        return sqlite3.connect(str(default_path))
    print("ERROR: no se encontró BD SQLite (busca GEAS_DB, GEAS_DATA_DIR o ~/.local/share/geas/geas.db)")
    sys.exit(1)


# Mapeo: tabla_sqlite → campo_mongo (sin cambios, campos idénticos)
TABLES = [
    "organizations",
    "departments",
    "roles",
    "policies",
    "users",
    "agents",
    "devices",
    "repositories",
    "resources",
    "resource_locks",
    "tickets",
    "ticket_dependencies",
    "executions",
    "test_results",
    "events",
    "audit_log",
]

# Columnas que son JSON en SQLite (TEXT) y deben parsearse
JSON_COLUMNS = {
    "organizations": ["profile"],  # profile es TEXT pero es string simple
    "roles": ["permissions"],
    "policies": ["permissions"],
    "agents": ["configuration"],
    "resources": ["metadata"],
    "tickets": ["files", "resources", "dependencies", "blocked_tickets"],
    "executions": ["tools_used"],
    "events": ["metadata"],
    "audit_log": ["metadata"],
    "ticket_dependencies": [],
}

# Columnas que son booleanos en SQLite (INTEGER 0/1) → bool en MongoDB
BOOL_COLUMNS = {
    "organizations": ["active"],
    "departments": ["active"],
    "roles": [],
    "policies": ["active"],
    "users": ["active"],
    "agents": ["active"],
    "devices": ["active"],
    "repositories": ["active"],
    "tickets": [],
    "executions": [],
    "test_results": [],
    "events": [],
    "audit_log": [],
}


def convert_value(table: str, key: str, value: object) -> object:
    """Convierte valores de SQLite a tipos MongoDB."""
    if value is None:
        return None

    # Booleanos
    if key in BOOL_COLUMNS.get(table, []):
        return bool(value)

    # JSON
    if key in JSON_COLUMNS.get(table, []):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    # INTEGER → int (por defecto)
    # REAL → float
    # TEXT → str
    # BLOB → str (base64 si es binario)
    return value


def migrate() -> None:
    client = get_client()
    db = client["geas"]

    conn = get_sqlite_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    total = 0
    for table in TABLES:
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        if not rows:
            print(f"  {table}: 0 filas (vacío)")
            continue

        # Convertir cada fila a dict MongoDB-friendly
        documents = []
        for row in rows:
            doc = {}
            for key in row.keys():
                val = row[key]
                doc[key] = convert_value(table, key, val)
            documents.append(doc)

        # Insertar en MongoDB
        result = db[table].insert_many(documents)
        inserted = len(result.inserted_ids)
        total += inserted
        print(f"  {table}: {inserted} filas importadas")

    conn.close()
    client.close()
    print(f"\nMigración completa: {total} documentos en MongoDB")


if __name__ == "__main__":
    migrate()
