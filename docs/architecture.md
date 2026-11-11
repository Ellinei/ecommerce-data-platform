# Architecture

> Part of the [documentation index](README.md). This page explains **what the pieces are and how
> they fit together**. For how to run them, see [local-development.md](local-development.md) and
> [aws-deployment.md](aws-deployment.md).

- [The idea in one paragraph](#the-idea-in-one-paragraph)
- [Runtime services](#runtime-services)
- [The four DAGs](#the-four-dags)
- [Data flow: toy demo](#data-flow-toy-demo)
- [Data flow: Olist e-commerce](#data-flow-olist-e-commerce)
- [Warehouse schemas](#warehouse-schemas)
- [Data quality](#data-quality)
- [Governance](#governance)
- [MLOps](#mlops)
- [Catalog search (RAG)](#catalog-search-rag)
- [Operational defaults](#operational-defaults)
- [Three ways to run it](#three-ways-to-run-it)
- [Key design decisions](#key-design-decisions)

---

## The idea in one paragraph

Airflow orchestrates, dbt transforms, PostgreSQL stores. Raw CSVs land in a `raw` schema, dbt
turns them into cleaned **staging** views and business-ready **mart** tables, and everything
downstream — BI dashboards, ML training, role-based access, semantic search over the catalog —
reads from the marts. Two datasets flow through the same wiring: a tiny **toy demo** (10 customers,
15 orders) that always works and shows the plumbing, and the real **Olist** Brazilian e-commerce
dataset (~100k orders across 9 tables) that proves it at realistic scale.

---

## Runtime services

```
┌───────────────────────────────────────────────────────────────┐
│  Docker network                                               │
│                                                               │
│  ┌─────────────────┐     metadata      ┌──────────────────┐   │
│  │ Airflow         │ ◄──────────────►  │ postgres_airflow │   │
│  │  · webserver    │                   │ (Airflow DB)     │   │
│  │  · scheduler    │                   └──────────────────┘   │
│  │                 │                                          │
│  │  DAGs run here  │  ingest + dbt     ┌───────────────────┐  │
│  │  (dbt, pandas,  │ ──────────────►   │ postgres_warehouse│  │
│  │   sklearn ...)  │                   │ (pgvector/pg15)   │  │
│  │                 │   runs/models     ├───────────────────┤  │
│  │                 │ ──────────────►   │ mlflow-server     │  │
│  └─────────────────┘                   │ (optional)        │  │
│                                        └───────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

| Service | Purpose | When it runs |
|---|---|---|
| `airflow-webserver` | Airflow UI | always |
| `airflow-scheduler` | Schedules and executes DAG tasks (LocalExecutor) | always |
| `airflow-init` | One-shot DB migration + admin user | once at startup |
| `postgres_airflow` | Airflow metadata store | always |
| `postgres_warehouse` | The data warehouse — dbt target, plus the pgvector table used by RAG | always |
| `mlflow-server` | MLflow tracking UI + artifact store | `--profile mlops` in dev; always-on in the prod stack |
| `openmetadata-*` | OpenMetadata data catalog (server, MySQL, Elasticsearch, ingestion) | `--profile catalog`, dev only |

All Airflow containers share one custom image (`Dockerfile`) with dbt, Cosmos, pandas, MLflow and
the OpenAI client pre-installed. Host ports are listed in
[local-development.md](local-development.md#running-dev-and-prod-side-by-side).

---

## The four DAGs

| DAG | Schedule | What it does | Code |
|---|---|---|---|
| `dbt_pipeline` | daily | `ingest_olist` loads the Olist CSVs into `raw`; `dbt deps`/`dbt seed` prepare the project; a Cosmos task group runs every dbt model and test as its own Airflow task; `dbt_docs_generate` writes the manifest | `dags/dbt_pipeline_dag.py` |
| `mlflow_training` | weekly | Trains a lifetime-value model on the toy mart, logs to MLflow | `dags/mlflow_training_dag.py` |
| `mlflow_training_olist` | weekly | Same model, trained on the Olist customer mart | `dags/mlflow_training_olist_dag.py` |
| `rag_index` | manual | Embeds dbt model/column descriptions into pgvector for semantic search | `dags/rag_index_dag.py` |

`dbt_pipeline` is the backbone; the other three consume what it produces:

```
ingest_olist ─────────────┐
dbt_deps ──► dbt_seed ────┴─► [dbt_transform: one task per model + test] ──► dbt_docs_generate
                                        │                                          │
                                        ▼                                          ▼
                              marts in the warehouse                      target/manifest.json
                                        │                                          │
                     mlflow_training / mlflow_training_olist                   rag_index
```

Two small shared modules keep the DAGs consistent: `dags/_db.py` (one way to build the warehouse
connection) and `dags/_operational_defaults.py` (retries + Slack alerting, see
[Operational defaults](#operational-defaults)).

---

## Data flow: toy demo

```
seeds/raw_customers.csv  ──┐
                            ├─► dbt seed ──► raw_customers
seeds/raw_orders.csv     ──┘              ► raw_orders
                                                │
                                    ┌───────────┴───────────┐
                                    ▼                       ▼
                             stg_customers           stg_orders
                             (VIEW)                  (VIEW)
                                    │                       │
                                    └───────────┬───────────┘
                                                ▼
                                     mart_customer_orders (TABLE)
                                                │
                                                ▼
                                  mart_customer_orders_masked
                                  (analyst-facing, email masked)
```

| Layer | Model | Materialisation | Description |
|---|---|---|---|
| Seeds | `raw_customers` | table | 10 sample customers loaded from CSV |
| Seeds | `raw_orders` | table | 15 sample orders loaded from CSV |
| Staging | `stg_customers` | view | Type-cast + normalised customer records |
| Staging | `stg_orders` | view | Type-cast + normalised order records |
| Marts | `mart_customer_orders` | table | Per-customer order aggregates for BI |
| Marts | `mart_customer_orders_masked` | view | Same mart with the email column masked, for the `analyst` role |

---

## Data flow: Olist e-commerce

Uses the Kaggle **"Brazilian E-Commerce Public Dataset by Olist"** (`olistbr/brazilian-ecommerce`)
— ~100k real, anonymized orders across 9 raw tables. It runs alongside the toy demo without
modifying it.

```
data/olist/*.csv (user-downloaded, gitignored)
        │  bind-mounted read-only: ./data → /opt/airflow/data
        │  (on AWS: downloaded from S3 at task start instead)
        ▼
Airflow task `ingest_olist` (pandas + SQLAlchemy)
        │  loads into literal Postgres schema `raw` (raw.olist_*)
        ▼
dbt source('olist_raw', ...)
        │
   models/olist_staging/*.sql  (9 views, schema public_olist_staging)
        │
   models/olist_marts/*.sql    (2 tables, schema public_olist_marts)
        │
        ├─► mart_olist_customer_orders    (one row per customer, grouped by
        │                                  customer_unique_id — Olist mints a
        │                                  new customer_id per order)
        └─► mart_olist_seller_performance (one row per seller: revenue,
                                           freight, review scores)
```

| Layer | Model | Materialisation | Description |
|---|---|---|---|
| Staging | `stg_olist_customers` | view | Customer records (order-scoped `customer_id` + the real repeat-customer `customer_unique_id`) |
| Staging | `stg_olist_orders` | view | Order records, status + timestamps |
| Staging | `stg_olist_order_items` | view | Line items — product, seller, price, freight |
| Staging | `stg_olist_order_payments` | view | Payment method/value per order |
| Staging | `stg_olist_order_reviews` | view | Customer review scores + comments |
| Staging | `stg_olist_products` | view | Product catalog (fixes upstream column-name typos) |
| Staging | `stg_olist_sellers` | view | Seller records |
| Staging | `stg_olist_geolocation` | view | Zip-code geolocation, aggregated to one row per prefix (raw table has ~1M near-duplicate rows) |
| Staging | `stg_olist_product_category_translation` | view | Portuguese → English category names |
| Marts | `mart_olist_customer_orders` | table | Per-customer lifetime value, order counts, delivered/cancelled breakdown |
| Marts | `mart_olist_seller_performance` | table | Per-seller revenue, freight, average review score |

`ingest_olist` is idempotent: it truncates and reloads each `raw.olist_*` table rather than
dropping it, because the staging views depend on those tables after the first run.

---

## Warehouse schemas

dbt prefixes custom schemas with the target schema (`public`), so the names in Postgres are:

| Schema | Written by | Contents |
|---|---|---|
| `public_raw` | `dbt seed` | Toy demo seeds |
| `public_staging` | dbt | Toy demo staging views |
| `public_marts` | dbt | Toy demo mart + masked view |
| `raw` | `ingest_olist` | The 9 `olist_*` raw tables (literal schema name — loaded by pandas, not dbt) |
| `public_olist_staging` | dbt | Olist staging views |
| `public_olist_marts` | dbt | Olist marts |
| `public` | `rag_index` | `catalog_embeddings` (pgvector) |

---

## Data quality

- **Schema tests** in each layer's `schema.yml`: `not_null` / `unique` on primary keys,
  `relationships` for foreign keys, `accepted_values` for vocabularies (`order_status`,
  `payment_type`, `review_score`, …). The Olist models alone carry 43 tests.
- **Singular tests** in `dbt_project/tests/` assert business invariants that generic tests can't express: order
  subtotals never exceed totals, and seller revenue and review averages aren't inflated by join
  fan-out.
- **Exposures** (`models/marts/exposures.yml`) record which dashboards depend on which marts.

Cosmos turns each of these into an Airflow task, so a failing test is visible (and alertable) in
the Airflow UI.

---

## Governance

`governance/setup_roles.sql` is policy-as-code for two roles:

| Role | Can read | Restrictions |
|---|---|---|
| `engineer` | All schemas: raw, staging, marts | none |
| `analyst` | Toy demo: only `mart_customer_orders_masked`. Olist: both marts | Email is masked in the toy mart; row-level security on `mart_olist_customer_orders` limits analysts to customers with at least one delivered order |

Olist's customer table carries no name or email, so it needs no masked view — row-level security
is enough.

Because dbt rebuilds mart tables on every run (which would silently drop grants and policies),
the grants and RLS are also re-applied by dbt post-hooks: `macros/grant_select.sql` and
`macros/apply_rls.sql`. The SQL script stays the place where the roles themselves are created —
which is why it must be run once on any fresh warehouse before the first `dbt_pipeline` run.

---

## MLOps

Both training DAGs read a customer mart, train a `RandomForestRegressor` to predict customer
lifetime value, and log parameters, metrics and the model artifact to MLflow.

- `mlflow_training` — the toy mart; a fast always-works demo of the pattern.
- `mlflow_training_olist` — `mart_olist_customer_orders` (~96k unique customers); the same
  pattern against real signal.

Both need `mlflow-server` running (`--profile mlops` in dev).

---

## Catalog search (RAG)

```
dbt_docs_generate ──► target/manifest.json
                              │
                 rag_index DAG: extract model + column descriptions
                              │  OpenAI embeddings (text-embedding-3-small)
                              ▼
                 catalog_embeddings (pgvector, in the warehouse)
                              ▲
                              │  nearest-neighbour search
            python rag/query.py "which table has customer emails?"
                              │  top matches + question ──► gpt-4o-mini ──► answer
```

Without `OPENAI_API_KEY` the infrastructure still builds; the indexing step is skipped.

---

## Operational defaults

Every DAG gets the same `default_args` from `dags/_operational_defaults.py`:

- **Retries:** 2, five minutes apart.
- **Alerting:** a Slack message on final failure (after retries are exhausted), if
  `SLACK_WEBHOOK_URL` is set.
- **Idempotency:** tasks are safe to re-run — ingestion truncates and reloads, embeddings upsert
  on a unique index, and `max_active_runs=1` prevents overlapping runs.

---

## Three ways to run it

| | Dev stack | Local "prod" stack | AWS |
|---|---|---|---|
| Purpose | Day-to-day development | Proves dev/prod separation on one machine | Portfolio/demo cloud deployment, torn down between sessions |
| Compute | Docker Compose | Same compose file, `-p airflow-dbt-prod --env-file .env.prod` | ECS Fargate (webserver + scheduler tasks) |
| Databases | 2 Postgres containers | 2 separate Postgres containers | One RDS PostgreSQL instance, 2 databases |
| Olist data | `./data/olist` bind mount | same | S3 bucket, pulled by `ingest_olist` |
| Secrets | `.env` | `.env.prod` | AWS Secrets Manager |
| Task logs | `./logs` | `./logs` (shared) | S3 + CloudWatch |
| Deploy | `docker compose up` | `docker compose up` | Terraform + manual `deploy.yml` workflow |
| Guide | [local-development.md](local-development.md) | [local-development.md](local-development.md#running-dev-and-prod-side-by-side) | [aws-deployment.md](aws-deployment.md) |

The DAG and dbt code is identical in all three; only environment variables differ.

---

## Key design decisions

| Decision | Rationale |
|---|---|
| Custom `Dockerfile` instead of `_PIP_ADDITIONAL_REQUIREMENTS` | Packages are baked in at build time, so containers start fast and don't need compilers at runtime; the same image runs locally and on ECS. |
| Two PostgreSQL databases | Mirrors real-world separation of Airflow metadata and the analytics warehouse — same pattern as Snowflake + managed Airflow in production. |
| Cosmos `LoadMode.DBT_LS` | Cosmos calls `dbt ls` at scheduler startup to auto-discover all models. You get one Airflow task per node without hard-coding anything. |
| File-based `profiles.yml` | Avoids needing an Airflow Connection object for the warehouse — credentials flow from `.env` → docker-compose env vars → dbt `env_var()`. |
| Staging as VIEWs, Marts as TABLEs | Standard dbt layering: staging is cheap to rebuild, marts are materialised for BI performance. |
| Toy demo kept alongside Olist | A tiny dataset that always works isolates "is the wiring broken?" from "is the data wrong?". |
| One shared `docker-compose.yml` for dev + prod | Every dev/prod difference is a scalar env-var value (ports, `DBT_TARGET`, `COMPOSE_PROFILES`), not structure — avoids double-maintaining service definitions across two files. Trade-off: host bind mounts (`./dags`, `./logs`, `./dbt_project`, etc.) are shared between both stacks, so avoid running `dbt_pipeline` in both at once. |
| AWS: public subnets, no NAT, manual deploys | Optimised for near-zero cost and a clean `terraform destroy`, not high availability. Details in the [Phase 4 design spec](superpowers/specs/2026-08-18-phase4-cloud-deployment-design.md). |

The full reasoning behind each phase lives in the [design specs](README.md#project-history-specs--plans).
