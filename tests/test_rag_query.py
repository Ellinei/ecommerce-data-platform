from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy

from dags.rag_index_dag import _ensure_catalog_embeddings_schema, _store_embeddings
from rag.query import retrieve

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_retrieve_uses_real_vectors_through_the_fixed_cast(warehouse_engine):
    """retrieve() glued `:vec::vector` directly onto the bind param in both
    the SELECT and ORDER BY clauses — a Postgres syntax error, not just an
    unbound param (confirmed empirically: `psycopg2.errors.SyntaxError:
    syntax error at or near ":"`). This exercises both occurrences
    end-to-end with a real vector through the fixed CAST(:vec AS vector).

    Doesn't assert on ranking across multiple rows: retrieve() has no WHERE
    clause, so it's a global top-k search through catalog_embeddings' real
    ivfflat index (lists = 10), which is approximate at the default
    probes = 1 — with only a couple of rows in the table that can miss a
    row entirely. That's pgvector behaving as documented, not something
    this fix changes; don't "fix" this test back to asserting order."""
    _ensure_catalog_embeddings_schema(warehouse_engine)

    with warehouse_engine.begin() as conn:
        conn.execute(sqlalchemy.text(
            "DELETE FROM catalog_embeddings WHERE model_name LIKE 'query_test_%'"
        ))

    close_vec = [1.0] + [0.0] * 1535
    far_vec = [0.0] * 1535 + [1.0]
    docs = [
        {"source": "model", "model_name": "query_test_close", "column_name": None,
         "description": "close match"},
        {"source": "model", "model_name": "query_test_far", "column_name": None,
         "description": "far match"},
    ]
    _store_embeddings(warehouse_engine, docs, [close_vec, far_vec])

    try:
        results = retrieve(warehouse_engine, close_vec, k=1)

        with warehouse_engine.begin() as conn:
            stored_count = conn.execute(sqlalchemy.text(
                "SELECT count(*) FROM catalog_embeddings WHERE model_name LIKE 'query_test_%'"
            )).scalar()
    finally:
        with warehouse_engine.begin() as conn:
            conn.execute(sqlalchemy.text(
                "DELETE FROM catalog_embeddings WHERE model_name LIKE 'query_test_%'"
            ))

    assert stored_count == 2  # both docs written by _store_embeddings
    assert len(results) == 1
    assert results[0]["model_name"] == "query_test_close"
    assert results[0]["similarity"] > 0.99  # self-similarity of a unit vector


def _rag_query_dsn_in_subprocess(env_overrides: dict[str, str]) -> str:
    """Imports rag.query in a clean subprocess and renders its module-level
    WAREHOUSE_DSN with the password unmasked."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    for key in ("WAREHOUSE_DB_HOST", "WAREHOUSE_DB_PORT", "WAREHOUSE_DB_USER",
                "WAREHOUSE_DB_PASSWORD", "WAREHOUSE_DB_NAME"):
        env.pop(key, None)
    env.update(env_overrides)
    code = (
        "from rag.query import WAREHOUSE_DSN; "
        "print(WAREHOUSE_DSN.render_as_string(hide_password=False))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().split("\n")[-1]


def test_rag_query_dsn_percent_encodes_special_characters_in_password():
    # A raw f-string DSN misparses "p@ss:word/1" as its own host/port —
    # URL.create() must percent-encode it so host/port/password all survive.
    url = _rag_query_dsn_in_subprocess({
        "WAREHOUSE_DB_USER": "warehouse",
        "WAREHOUSE_DB_PASSWORD": "p@ss:word/1",
        "WAREHOUSE_DB_HOST": "localhost",
        "WAREHOUSE_DB_PORT": "5433",
        "WAREHOUSE_DB_NAME": "warehouse",
    })
    parsed = sqlalchemy.engine.make_url(url)
    assert parsed.host == "localhost"
    assert parsed.port == 5433
    assert parsed.password == "p@ss:word/1"


def test_rag_query_dsn_falls_back_to_localhost_5433_when_unset():
    url = _rag_query_dsn_in_subprocess({})
    assert "@localhost:5433/" in url
