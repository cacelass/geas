"""Inicializa la base de datos de GEAS en MongoDB.

Crea las 16 colecciones y los índices necesarios para que funcione
como reemplazo directo del SQLite existente.

Uso:
    cd /home/cancelas/geas && python -m geas.db_init
"""

from __future__ import annotations

import sys
from pathlib import Path

from pymongo import MongoClient


def get_client() -> MongoClient:
    # Lee de .env (sigue .gitignore, nunca se versiona)
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


# Índices: lista de [(campo, orden), ...] o [("campo",), ...] para único simple
INDEXES = {
    "organizations": [
        [("id", 1)],
        [("name", 1)],
    ],
    "departments": [
        [("id", 1)],
        [("organization_id", 1)],
        [("organization_id", 1), ("name", 1), ("parent_department_id", 1)],
    ],
    "roles": [
        [("id", 1)],
        [("organization_id", 1), ("name", 1)],
    ],
    "policies": [
        [("id", 1)],
        [("organization_id", 1), ("name", 1), ("department_id", 1)],
    ],
    "users": [
        [("id", 1)],
        [("organization_id", 1)],
    ],
    "agents": [
        [("id", 1)],
        [("organization_id", 1)],
    ],
    "devices": [
        [("id", 1)],
        [("organization_id", 1), ("name", 1)],
    ],
    "repositories": [
        [("id", 1)],
        [("organization_id", 1)],
        [("organization_id", 1), ("name", 1), ("department_id", 1)],
    ],
    "resources": [
        [("id", 1)],
        [("repository_id", 1)],
    ],
    "resource_locks": [
        [("id", 1)],
        [("resource_id", 1), ("ticket_id", 1)],
        [("expires_at", 1)],
    ],
    "tickets": [
        [("id", 1)],
        [("organization_id", 1), ("status", 1), ("priority", -1)],
        [("id", 1), ("status", 1)],
        [("ticket_id", 1), ("depends_on_ticket_id", 1)],
    ],
    "ticket_dependencies": [
        [("id", 1)],
        [("ticket_id", 1), ("depends_on_ticket_id", 1)],
    ],
    "executions": [
        [("id", 1)],
        [("ticket_id", 1), ("started_at", 1)],
    ],
    "test_results": [
        [("id", 1)],
        [("ticket_id", 1), ("started_at", 1)],
    ],
    "events": [
        [("id", 1)],
        [("organization_id", 1), ("timestamp", -1)],
    ],
    "audit_log": [
        [("id", 1)],
        [("timestamp", -1)],
    ],
}


def init_database(client: MongoClient, db_name: str = "geas") -> None:
    db = client[db_name]
    created = 0
    for collection_name, index_specs in INDEXES.items():
        collection = db[collection_name]
        try:
            db.create_collection(collection_name)
        except Exception:
            pass
        existing = {
            idx["key"][0] if isinstance(idx["key"], list) else idx["key"]
            for idx in collection.index_information().values()
        }
        for spec in index_specs:
            name = spec[0][0] if isinstance(spec[0], list) else spec[0]
            if name in existing:
                continue
            collection.create_index(spec)
        created += 1
    print(f"Init OK: {created} colecciones preparadas en '{db_name}'")


def main() -> None:
    client = get_client()
    client.admin.command("ping")
    init_database(client)
    client.close()


if __name__ == "__main__":
    main()
