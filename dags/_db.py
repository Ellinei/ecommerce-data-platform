"""Shared warehouse-connection helper.

Every DAG and the test suite build a SQLAlchemy engine for the same
warehouse Postgres from the same WAREHOUSE_DB_* env vars. Centralized here
(inside dags/, not the repo root) so credentials are never hand-interpolated
into an f-string DSN — a password containing '@', ':', or '/' misparses the
host/port in a raw f-string, but URL.create() percent-encodes it correctly.

Lives in dags/ rather than the repo root because that's the only path every
DAG consumer can actually reach at runtime: docker-compose.yml bind-mounts
./dags (not the repo root) into the Airflow containers, and the Dockerfile
bakes the same dags/ directory for the AWS/ECS image — mirroring the
existing dags/_operational_defaults.py convention. rag/query.py is a
standalone script invoked as `python rag/query.py` (sys.path[0] is its own
directory, not the repo root or dags/), so it cannot import this module —
it fixes the same underlying defect locally instead.
"""
from __future__ import annotations

import os

import sqlalchemy


def warehouse_engine_url(
    *,
    default_host: str = "postgres_warehouse",
    default_port: str = "5432",
) -> sqlalchemy.engine.URL:
    return sqlalchemy.engine.URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("WAREHOUSE_DB_USER", "warehouse"),
        password=os.getenv("WAREHOUSE_DB_PASSWORD", "warehouse"),
        host=os.getenv("WAREHOUSE_DB_HOST", default_host),
        port=int(os.getenv("WAREHOUSE_DB_PORT", default_port)),
        database=os.getenv("WAREHOUSE_DB_NAME", "warehouse"),
    )
