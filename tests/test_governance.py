from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import sqlalchemy

from dags.dbt_pipeline_dag import OLIST_FILES, _ingest_olist_files

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "dbt_project"
OLIST_SAMPLE_DIR = Path(__file__).resolve().parent / "fixtures" / "olist_sample"

OLIST_MARTS_SCHEMA = "public_olist_marts"
CUSTOMER_ORDERS_TABLE = "mart_olist_customer_orders"
CUSTOMER_ORDERS_MART = f"{OLIST_MARTS_SCHEMA}.{CUSTOMER_ORDERS_TABLE}"
SELLER_PERFORMANCE_MART = f"{OLIST_MARTS_SCHEMA}.mart_olist_seller_performance"


def _run_dbt(*args: str) -> None:
    dbt_exe = shutil.which("dbt")
    assert dbt_exe, "dbt not found on PATH — install project requirements first"
    subprocess.run(
        [dbt_exe, *args, "--project-dir", str(DBT_PROJECT_DIR),
         "--profiles-dir", str(DBT_PROJECT_DIR)],
        check=True,
    )


def _apply_governance_grants_and_rls(conn: sqlalchemy.Connection) -> None:
    """Mirrors governance/setup_roles.sql's Olist grants + RLS section — this
    is the manual, out-of-band step that currently must be re-run after every
    dbt rebuild. Applied once here, before a rebuild, so the test can assert
    the rebuild doesn't silently undo it."""
    conn.execute(sqlalchemy.text(
        f"GRANT SELECT ON {CUSTOMER_ORDERS_MART} TO analyst"
    ))
    conn.execute(sqlalchemy.text(
        f"GRANT SELECT ON {SELLER_PERFORMANCE_MART} TO analyst"
    ))
    conn.execute(sqlalchemy.text(
        f"ALTER TABLE {CUSTOMER_ORDERS_MART} ENABLE ROW LEVEL SECURITY"
    ))
    conn.execute(sqlalchemy.text(f"""
        DO $$ BEGIN
          CREATE POLICY engineer_full_access
            ON {CUSTOMER_ORDERS_MART}
            FOR SELECT TO engineer
            USING (true);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sqlalchemy.text(f"""
        DO $$ BEGIN
          CREATE POLICY analyst_delivered_only
            ON {CUSTOMER_ORDERS_MART}
            FOR SELECT TO analyst
            USING (delivered_orders > 0);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))


def _analyst_has_select(conn: sqlalchemy.Connection, qualified_table: str) -> bool:
    return bool(conn.execute(
        sqlalchemy.text("SELECT has_table_privilege('analyst', :t, 'SELECT')"),
        {"t": qualified_table},
    ).scalar())


def _rls_enabled(conn: sqlalchemy.Connection, qualified_table: str) -> bool:
    return bool(conn.execute(
        sqlalchemy.text("SELECT relrowsecurity FROM pg_class WHERE oid = (:t)::regclass"),
        {"t": qualified_table},
    ).scalar())


def _policy_exists(conn: sqlalchemy.Connection, schema: str, table: str, policy: str) -> bool:
    return bool(conn.execute(
        sqlalchemy.text(
            "SELECT EXISTS (SELECT 1 FROM pg_policies "
            "WHERE schemaname = :schema AND tablename = :table AND policyname = :policy)"
        ),
        {"schema": schema, "table": table, "policy": policy},
    ).scalar())


def test_analyst_grants_and_rls_survive_a_dbt_rebuild(warehouse_engine):
    _ingest_olist_files(warehouse_engine, OLIST_SAMPLE_DIR, OLIST_FILES)
    _run_dbt("seed")
    _run_dbt("run")

    with warehouse_engine.begin() as conn:
        _apply_governance_grants_and_rls(conn)
        assert _analyst_has_select(conn, CUSTOMER_ORDERS_MART)
        assert _analyst_has_select(conn, SELLER_PERFORMANCE_MART)
        assert _rls_enabled(conn, CUSTOMER_ORDERS_MART)
        assert _policy_exists(
            conn, OLIST_MARTS_SCHEMA, CUSTOMER_ORDERS_TABLE, "analyst_delivered_only"
        )

    # `table` materialization drops and recreates these relations — this is
    # the rebuild that must not silently undo the grants/RLS applied above.
    _run_dbt("run")

    with warehouse_engine.connect() as conn:
        assert _analyst_has_select(conn, CUSTOMER_ORDERS_MART), \
            "analyst SELECT grant on mart_olist_customer_orders did not survive a dbt rebuild"
        assert _analyst_has_select(conn, SELLER_PERFORMANCE_MART), \
            "analyst SELECT grant on mart_olist_seller_performance did not survive a dbt rebuild"
        assert _rls_enabled(conn, CUSTOMER_ORDERS_MART), \
            "row-level security was disabled by a dbt rebuild"
        assert _policy_exists(
            conn, OLIST_MARTS_SCHEMA, CUSTOMER_ORDERS_TABLE, "engineer_full_access"
        ), "engineer_full_access RLS policy did not survive a dbt rebuild"
        assert _policy_exists(
            conn, OLIST_MARTS_SCHEMA, CUSTOMER_ORDERS_TABLE, "analyst_delivered_only"
        ), "analyst_delivered_only RLS policy did not survive a dbt rebuild"
