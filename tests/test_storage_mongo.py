"""tests.test_storage_mongo — contrato del backend MongoDB.

La batería completa de ``tests/test_storage.py`` corre también contra Mongo
con ``GEAS_TEST_BACKEND=mongo``. Aquí van las garantías que **solo** se
pueden probar con dos conexiones independientes: que dos actores no reclaman
el mismo ticket y que una transacción abortada no deja filas.

Sin ``GEAS_TEST_MONGO_URL`` no se finge nada: los tests se saltan.
"""

from __future__ import annotations

import os
import uuid

import pytest

from geas.dbpath import is_mongo, redact_uri, resolve_target
from geas.models import Organization, Resource, ResourceLock, Ticket

URL = os.environ.get("GEAS_TEST_MONGO_URL", "")
CLUSTER = pytest.mark.skipif(
    not URL, reason="GEAS_TEST_MONGO_URL no está definida (cluster real)"
)


@pytest.fixture
def db_name():
    return f"geas_test_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def abrir(db_name):
    from geas.storage_mongo import MongoStorage

    abiertos = []

    def _abrir():
        s = MongoStorage(URL, database=db_name)
        abiertos.append(s)
        return s

    yield _abrir
    for s in abiertos:
        s.client.drop_database(db_name)
        s.close()


@CLUSTER
def test_dos_actores_no_reclaman_el_mismo_ticket(abrir):
    uno, otro = abrir(), abrir()
    org = Organization(name="Carrera")
    uno.create_organization(org)
    ticket = Ticket(organization_id=org.id, title="Solo uno")
    uno.create_ticket(ticket)

    assert uno.start_ticket(ticket.id) is True
    assert otro.start_ticket(ticket.id) is False
    assert otro.get_ticket(ticket.id).status.value == "IN_PROGRESS"


@CLUSTER
def test_locks_son_todos_o_ninguno_entre_conexiones(abrir):
    uno, otro = abrir(), abrir()
    org = Organization(name="Locks")
    uno.create_organization(org)
    a = Resource(path="a.py")
    b = Resource(path="b.py")
    uno.create_resource(a)
    uno.create_resource(b)
    t1 = Ticket(organization_id=org.id, title="Uno")
    t2 = Ticket(organization_id=org.id, title="Dos")
    uno.create_ticket(t1)
    uno.create_ticket(t2)

    assert uno.acquire_locks([a.id], t1.id, "actor-1") == (True, None)
    ok, bloqueado = otro.acquire_locks([a.id, b.id], t2.id, "actor-2")
    assert ok is False
    assert bloqueado == a.id
    assert otro.get_lock_for_resource(b.id) is None


@CLUSTER
def test_una_transaccion_abortada_no_deja_nada(abrir):
    storage = abrir()
    org = Organization(name="Rollback")
    storage.create_organization(org)
    ticket = Ticket(organization_id=org.id, title="Huerfano")

    with pytest.raises(RuntimeError), storage.transaction():
        storage.create_ticket(ticket)
        raise RuntimeError("a mitad")

    assert storage.get_ticket(ticket.id) is None
    assert storage.list_tickets(org.id) == []


@CLUSTER
def test_indice_unico_impide_dos_locks_del_mismo_recurso(abrir):
    from pymongo.errors import DuplicateKeyError

    storage = abrir()
    lock = ResourceLock(
        resource_id="recurso-x", ticket_id="t1", expires_at="2099-01-01"
    )
    storage.create_lock(lock)
    with pytest.raises(DuplicateKeyError):
        storage.create_lock(
            ResourceLock(
                resource_id="recurso-x", ticket_id="t2", expires_at="2099-01-01"
            )
        )


def test_is_mongo_y_redact_uri():
    assert is_mongo("mongodb+srv://u:p@host/db") is True
    assert is_mongo("mongodb://localhost:27017") is True
    assert is_mongo("/tmp/geas.db") is False
    assert redact_uri("mongodb+srv://user:secreto@host/db") == (
        "mongodb+srv://user:****@host/db"
    )
    assert redact_uri("mongodb://localhost:27017") == "mongodb://localhost:27017"


def test_resolve_target_no_destripa_la_uri(monkeypatch):
    monkeypatch.setenv("GEAS_DB", "mongodb+srv://u:p@host/db")
    assert resolve_target() == "mongodb+srv://u:p@host/db"
