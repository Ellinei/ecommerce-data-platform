# Local Development

> Part of the [documentation index](README.md). For what the pieces are and how they fit together,
> read [architecture.md](architecture.md) first.

Everything runs in Docker from one `docker-compose.yml`. This page covers setup, day-to-day
commands, the optional side-by-side "prod" stack, and the test suite.

- [Prerequisites](#prerequisites)
- [Secrets & configuration](#secrets--configuration)
- [Quick start (dev stack)](#quick-start-dev-stack)
- [Loading the Olist data](#loading-the-olist-data)
- [Optional services](#optional-services)
- [Everyday commands](#everyday-commands)
- [Running dev and prod side by side](#running-dev-and-prod-side-by-side)
- [Tests & CI](#tests--ci)
- [Stopping / resetting](#stopping--resetting)

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (with Compose v2)
- ~4 GB RAM allocated to Docker

---

## Secrets & configuration

Copy `.env.example` to `.env` and fill in real values before starting the stack — `.env` is
gitignored and never committed. `.env.example` documents every variable, including how to
generate a fresh Airflow Fernet key.

> Older commits in this repo's history contain local-dev-only demo credentials (Airflow Fernet
> key, default admin password, OpenMetadata service passwords) that predate this file. They have
> since been rotated; current credentials live only in your own `.env`.

For the optional prod stack, copy `.env.prod.example` to `.env.prod` and fill in **independently
generated** secrets — never copy values from `.env`. `.env.prod` is gitignored the same way `.env`
is.

---

## Quick start (dev stack)

```bash
# 1. From the repo root, create your .env (see "Secrets & configuration" above)
cp .env.example .env

# 2. Build the image and start all core services
#    The first build bakes dbt + Cosmos + the other Python deps into the image (a few minutes)
docker compose up -d --build

# 3. Watch the init container finish before opening the UI
docker compose logs -f airflow-init

# 4. Bootstrap the engineer/analyst roles in the warehouse — every dbt model has a
#    grant_select() post-hook that requires these roles to exist. On a fresh warehouse
#    only the role-creation section succeeds; the schema grants error out (harmlessly)
#    until dbt_pipeline has created the schemas, so re-run this once after the first run.
docker compose exec -T postgres_warehouse \
  psql -U warehouse -d warehouse \
  -v engineer_password="$GOVERNANCE_ENGINEER_PASSWORD" \
  -v analyst_password="$GOVERNANCE_ANALYST_PASSWORD" \
  < governance/setup_roles.sql

# 5. Open the Airflow UI
#    http://localhost:8080   login: admin / $AIRFLOW_ADMIN_PASSWORD (see .env)

# 6. Unpause and trigger the dbt_pipeline DAG from the UI (DAGs are created paused),
#    or leave it unpaused and wait for the daily schedule
```

`dbt_pipeline` includes the `ingest_olist` task, which fails if the Olist CSVs are missing — see
the next section before the first run.

---

## Loading the Olist data

1. Download the dataset from Kaggle (`olistbr/brazilian-ecommerce`) — see
   [`data/olist/README.md`](../data/olist/README.md) for the exact 9 filenames expected.
2. Place all 9 CSVs directly in `./data/olist/` (gitignored — never commit them).
3. `docker compose up -d --build`, then trigger `dbt_pipeline` — `ingest_olist` loads the raw
   tables, then Cosmos auto-discovers and runs the Olist dbt models alongside the toy demo's.

`ingest_olist` loads the CSVs into Postgres; it does not fetch them from Kaggle.

---

## Optional services

Compose profiles keep the heavier services off unless you ask for them:

| Profile | Start with | What you get |
|---|---|---|
| `mlops` | `docker compose --profile mlops up -d` | `mlflow-server` at http://localhost:5000 — needed by the `mlflow_training` and `mlflow_training_olist` DAGs |
| `catalog` | `docker compose --profile catalog up -d` | OpenMetadata at http://localhost:8585 (dev only) |

The OpenMetadata ingestion CLI configs are generated from tracked templates rather than committed
directly:

```bash
envsubst < openmetadata/postgres_ingestion.yaml.template > openmetadata/postgres_ingestion.yaml
envsubst < openmetadata/dbt_ingestion.yaml.template > openmetadata/dbt_ingestion.yaml
```

### Ask the catalog a question (RAG)

Needs `OPENAI_API_KEY` in `.env`, `dbt_pipeline` to have run at least once (so
`target/manifest.json` exists), and then the `rag_index` DAG triggered manually to fill the
`catalog_embeddings` table. From the host, in the repo root:

```bash
pip install openai psycopg2-binary sqlalchemy
python rag/query.py "which table has customer emails?"
```

---

## Everyday commands

### Run dbt manually inside the scheduler container

```bash
docker compose exec airflow-scheduler bash
# Prod stack equivalent:
docker compose -p airflow-dbt-prod --env-file .env.prod exec airflow-scheduler bash
```

Inside the container:

```bash
cd /opt/airflow/dbt_project
dbt seed   --profiles-dir .
dbt run    --profiles-dir .
dbt test   --profiles-dir .
dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir . --port 8081
```

### Connect to the warehouse directly

```
Host:     localhost
Port:     5433
Database: $WAREHOUSE_DB_NAME  (see .env — default "warehouse")
User:     $WAREHOUSE_DB_USER  (see .env — default "warehouse")
Password: $WAREHOUSE_DB_PASSWORD  (see .env)
```

(Prod stack: port 5443, see `.env.prod`.)

---

## Running dev and prod side by side

A second "prod" stack can run at the same time as dev, on the same machine, using the same
`docker-compose.yml` — every difference between them is an env var, not a second compose file.

| Service | Dev port | Prod port | Env var |
|---|---|---|---|
| `airflow-webserver` | 8080 | 8090 | `AIRFLOW_WEBSERVER_PORT` |
| `postgres_airflow` | 5432 | 5442 | `AIRFLOW_DB_PORT` |
| `postgres_warehouse` | 5433 | 5443 | `WAREHOUSE_DB_PORT` |
| `mlflow-server` | 5000 | 5010 | `MLFLOW_PORT` |

```bash
# 1. Copy the prod template and fill in independently-generated secrets
cp .env.prod.example .env.prod

# 2. Start the prod stack under its own Compose project name
docker compose -p airflow-dbt-prod --env-file .env.prod up -d

# 3. Bootstrap the engineer/analyst roles against the NEW prod warehouse — every
#    dbt model has a grant_select() post-hook that requires these roles to
#    exist, and prod's warehouse is a fresh, independent database that never
#    inherits dev's roles. Skipping this step fails dbt_pipeline's very first
#    run in prod with "role engineer does not exist".
docker compose -p airflow-dbt-prod --env-file .env.prod exec -T postgres_warehouse \
  psql -U warehouse -d warehouse \
  -v engineer_password="$GOVERNANCE_ENGINEER_PASSWORD" \
  -v analyst_password="$GOVERNANCE_ANALYST_PASSWORD" \
  < governance/setup_roles.sql

# 4. Open the prod Airflow UI
#    http://localhost:8090   login: admin / $AIRFLOW_ADMIN_PASSWORD (see .env.prod)
```

`mlflow-server` auto-starts in the prod stack via `COMPOSE_PROFILES=prod-core` in `.env.prod` — no
extra `--profile` flag needed on the command line (dev still requires `--profile mlops` to start it
manually).

> **Shared between both stacks:** host directories (`./dags`, `./logs`, `./dbt_project`, `./data`)
> are mounted into *both* stacks, and the Docker image tag they build/run from is also shared —
> containers, volumes, and ports are the only things isolated per project. Concretely:
> - **`./logs`** is written by both stacks' schedulers unconditionally, from the moment both stacks
>   are up — no DAG needs to run. Both append to
>   `logs/dag_processor_manager/dag_processor_manager.log` and write into
>   `logs/scheduler/<date>/` continuously. Because both stacks also share `./dags` (identical DAG
>   definitions) and both run `@daily`, an unpaused DAG in both stacks produces the *same* `run_id`,
>   so task logs land at the *same* path (`logs/dag_id=…/run_id=scheduled__…/task_id=…/attempt=1.log`)
>   — prod's UI could end up serving a file dev also wrote.
> - **`dbt_project/target/`/`dbt_packages/`** race if `dbt_pipeline` is triggered in both stacks at
>   the same moment — avoid running it in both at once.
> - **The image tag `airflow-dbt-pipeline:latest`** (`x-airflow-common.image` in
>   `docker-compose.yml`) is daemon-global, not project-scoped — rebuilding from either stack retags
>   what the other picks up on its next container recreate. This never actually breaks anything
>   (identical Dockerfile, shared `./dags` guarantees identical code either way) but it is a shared
>   resource, not an isolated one.
>
> **Container renaming:** the dev/prod split also removed `container_name:` from dev's core
> services, so containers run under Compose's auto-generated names (e.g. `postgres_airflow` →
> `airflowdbtpipeline-postgres_airflow-1`). Data is unaffected — named volumes are unchanged, only
> container identity changes — but any personal script or alias that calls
> `docker exec -it postgres_warehouse ...` (or similar literal names) will break with "No such
> container" until updated to `docker compose exec <service-name>` (see
> [Everyday commands](#everyday-commands)).

---

## Tests & CI

`tests/` is a pytest suite that runs against a real Postgres (with pgvector) — no Docker stack
needed, just a database reachable through the `WAREHOUSE_DB_*` env vars.

| Test file | What it protects |
|---|---|
| `test_dag_integrity.py` | Every DAG imports and parses cleanly |
| `test_operational_defaults.py` | Shared retries + Slack alerting are applied to all DAGs |
| `test_db.py` | The shared warehouse-connection helper (`dags/_db.py`) |
| `test_ingest_olist.py` | `ingest_olist` loads the CSVs correctly and is idempotent |
| `test_dbt_build.py` | A full `dbt build` (models + tests) passes on the Olist sample fixtures |
| `test_governance.py` | Role grants and row-level security behave as designed |
| `test_rag_index.py`, `test_rag_query.py` | Catalog embedding indexing and retrieval |
| `test_environment_separation.py` | Dev and prod stacks stay isolated in `docker-compose.yml` / env templates |
| `test_cloud_deployment.py`, `test_deploy_workflow.py` | The AWS image, Terraform and deploy workflow keep their guarantees (e.g. deploys are manual-only) |

`tests/fixtures/olist_sample/` holds a tiny slice of each Olist CSV so the suite never needs the
real dataset.

To run it the way CI does ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml), Python 3.12):

```bash
# A throwaway warehouse on localhost:5432
docker run -d --name ci-warehouse -p 5432:5432 \
  -e POSTGRES_USER=warehouse -e POSTGRES_PASSWORD=warehouse -e POSTGRES_DB=warehouse \
  pgvector/pgvector:pg15

export WAREHOUSE_DB_USER=warehouse WAREHOUSE_DB_PASSWORD=warehouse WAREHOUSE_DB_NAME=warehouse \
       WAREHOUSE_DB_HOST=localhost WAREHOUSE_DB_PORT=5432

pip install \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.9.1/constraints-3.12.txt" \
  "apache-airflow==2.9.1" -r requirements.txt
pip install --no-deps "dbt-postgres==1.8.0" "pandas==2.1.4"
pip install -r requirements-dev.txt

ruff check .
pytest -v
```

CI runs the same lint + tests on every push and pull request to `master`. Deploying to AWS is a
separate, manual-only workflow — see [aws-deployment.md](aws-deployment.md).

---

## Stopping / resetting

```bash
# Stop containers (keeps data volumes)
docker compose down

# Full reset — removes ALL data including the warehouse
docker compose down -v

# Prod stack equivalents
docker compose -p airflow-dbt-prod --env-file .env.prod down
docker compose -p airflow-dbt-prod --env-file .env.prod down -v
```
