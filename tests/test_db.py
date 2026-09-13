from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy

REPO_ROOT = Path(__file__).resolve().parent.parent


def _warehouse_engine_url_in_subprocess(env_overrides: dict[str, str]) -> str:
    """Imports dags._db in a clean subprocess and renders
    warehouse_engine_url() with the password unmasked, needed because
    dags._db may already be imported elsewhere in the pytest session."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    for key in ("WAREHOUSE_DB_HOST", "WAREHOUSE_DB_PORT", "WAREHOUSE_DB_USER",
                "WAREHOUSE_DB_PASSWORD", "WAREHOUSE_DB_NAME"):
        env.pop(key, None)
    env.update(env_overrides)
    code = (
        "from dags._db import warehouse_engine_url; "
        "print(warehouse_engine_url().render_as_string(hide_password=False))"
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


def test_warehouse_engine_url_percent_encodes_special_characters_in_password():
    # A raw f-string DSN misparses "p@ss:word/1" as its own host/port —
    # URL.create() must percent-encode it so host/port/password all survive.
    url = _warehouse_engine_url_in_subprocess({
        "WAREHOUSE_DB_USER": "warehouse",
        "WAREHOUSE_DB_PASSWORD": "p@ss:word/1",
        "WAREHOUSE_DB_HOST": "localhost",
        "WAREHOUSE_DB_PORT": "5432",
        "WAREHOUSE_DB_NAME": "warehouse",
    })
    parsed = sqlalchemy.engine.make_url(url)
    assert parsed.host == "localhost"
    assert parsed.port == 5432
    assert parsed.password == "p@ss:word/1"


def test_warehouse_engine_url_falls_back_to_compose_service_name_when_unset():
    url = _warehouse_engine_url_in_subprocess({})
    assert "@postgres_warehouse:5432/" in url


def test_warehouse_engine_url_uses_rds_host_and_port_when_set():
    url = _warehouse_engine_url_in_subprocess({
        "WAREHOUSE_DB_HOST": "my-rds-endpoint.rds.amazonaws.com",
        "WAREHOUSE_DB_PORT": "5433",
    })
    assert "@my-rds-endpoint.rds.amazonaws.com:5433/" in url
