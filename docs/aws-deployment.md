# AWS Cloud Deployment

> Part of the [documentation index](README.md). All commands run from the repo root unless a step says otherwise.

Phase 4 adds an optional, fully-torn-down-between-sessions AWS deployment on top of the same
Airflow + dbt + Postgres stack described in [architecture.md](architecture.md) — a portfolio/demo deployment, not an always-on
production environment. It's optimized for low cost and a clean `terraform destroy`, not scale or
high availability: managed services (RDS, ECS Fargate, ECR, Secrets Manager) provisioned by
Terraform, public subnets with no NAT gateway (security groups lock inbound traffic to the
operator's own IP), and CI/CD deploys that only ever run on manual `workflow_dispatch` — pushing to
`master` never touches AWS or costs money. Full design rationale lives in
[`docs/superpowers/specs/2026-08-18-phase4-cloud-deployment-design.md`](superpowers/specs/2026-08-18-phase4-cloud-deployment-design.md).

## 1. Architecture

```
GitHub repo ──(workflow_dispatch, OIDC)──► GitHub Actions ──build/push──► ECR repo
                                                              │
                                                    update-service ×2 (force new deployment)
                                                              ▼
                        AWS Account (one region)
   VPC — 2 public subnets, 2 AZs, IGW + public route table, no NAT

   SG: ecs_sg (in: 8080 from operator IP)     SG: rds_sg (in: 5432 from operator IP + from ecs_sg)

   ECS Fargate "webserver" task ──┐                    ┌── ECS Fargate "scheduler" task
   (same image, cmd=webserver)    │                    │   (same image, cmd=scheduler)
   public IP via ENI              │                    │   public IP via ENI
                                   ▼                    ▼
                     RDS PostgreSQL 15.x (public subnet, publicly_accessible)
                       db: airflow (metadata)   db: warehouse (dbt + pgvector)

                     S3 bucket: /task-logs/ (Airflow remote task logging)
                                /olist-raw/ (9 CSVs, uploaded once by operator)

                     CloudWatch Logs: /ecs/airflow-webserver, /ecs/airflow-scheduler
                     Secrets Manager: RDS-managed master password (auto) +
                                      one app-secrets JSON (Fernet key, webserver
                                      secret key, OPENAI_API_KEY, Slack webhook)
```

## 2. Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured (`aws configure`, or an SSO login) with an account able to create the resources above
- [Terraform](https://developer.hashicorp.com/terraform/downloads) `>= 1.5`
- [Docker](https://docs.docker.com/get-docker/) installed and running locally — needed for the
  one-time manual image push in step 5 (subsequent redeploys use `deploy.yml`'s CI-hosted Docker
  instead)
- `psql` (a PostgreSQL client) installed locally and on `PATH` — the bootstrap steps below shell out
  to it directly (not via Docker), four separate invocations across steps 5.2 and 5.3
- An AWS account
- Your current public IPv4 address: `curl ifconfig.me`
- The Olist CSVs already present at `data/olist/*.csv` before you reach step 7 — `data/` is
  gitignored, so a fresh clone won't have them; see [Real-World Data Pipeline
  (Olist)](../data/olist/README.md) to download them from Kaggle beforehand (`ingest_olist`
  loads these CSVs into Postgres, it does not fetch them)

## 3. First-time setup

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # fill in operator_ip and github_oidc_sub_prefix (see below)
terraform init
terraform apply
cd ..   # remaining steps run from the repo root; terraform outputs below are read via -chdir=terraform
```

**`github_oidc_sub_prefix`** must match exactly what GitHub puts in its OIDC token's `sub` claim,
so copy it from GitHub rather than typing `owner/repo`:

```bash
gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix
```

Repos with GitHub's immutable subjects enabled send `repo:owner@<owner-id>/repo@<repo-id>`, not
`repo:owner/repo`. A trust policy built from the plain name rejects every deploy with `Not
authorized to perform sts:AssumeRoleWithWebIdentity`. If that error ever appears, CloudTrail's
`AssumeRoleWithWebIdentity` events show the `sub` GitHub actually sent (as `userIdentity.userName`).

**Troubleshooting: `EntityAlreadyExists` on the OIDC provider.** `terraform/oidc.tf` creates an
`aws_iam_openid_connect_provider` for `token.actions.githubusercontent.com`. AWS allows only **one**
such provider per issuer URL per account, so if this AWS account already has a GitHub OIDC provider
configured (e.g. left over from an unrelated project's CI setup), `terraform apply` above fails with
`EntityAlreadyExists`. Fix: adopt the existing provider into this Terraform state instead of trying to
create a second one, then re-apply — run from `terraform/`:

```bash
terraform import aws_iam_openid_connect_provider.github \
  arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
terraform apply
cd ..   # back to repo root — remaining steps assume this, same as step 3 above
```

In a **shared** AWS account, two extra precautions: first, run `terraform plan` before that `apply`
and read it — `terraform/oidc.tf` pins `client_id_list = ["sts.amazonaws.com"]`, so if the existing
provider was configured with additional audiences another project relies on, applying this config
would silently strip them (add those audiences to `client_id_list` locally before applying if so).
Second, carry this through to teardown (step 11) too: if other repositories or projects depend on the
same OIDC provider, a plain `terraform destroy` would delete it out from under them — run
`terraform state rm aws_iam_openid_connect_provider.github` first (from `terraform/`) so `destroy`
leaves it alone, and re-run the `import` above at the start of your next session, since the state no
longer tracks it.

## 4. Populate the two operator-managed secrets

Must happen after `apply` — both secrets below depend on the RDS master secret already existing:

```bash
# Fernet key — same command .env.example already documents for local dev:
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

RDS_MASTER_SECRET=$(terraform -chdir=terraform output -raw rds_master_secret_arn)
RDS_USER=$(aws secretsmanager get-secret-value --secret-id "$RDS_MASTER_SECRET" \
  --query SecretString --output text | python -c "import json,sys; print(json.load(sys.stdin)['username'])")
RDS_PASS=$(aws secretsmanager get-secret-value --secret-id "$RDS_MASTER_SECRET" \
  --query SecretString --output text | python -c "import json,sys; print(json.load(sys.stdin)['password'])")
RDS_ENDPOINT=$(terraform -chdir=terraform output -raw rds_endpoint)   # "host:port"
RDS_HOST="${RDS_ENDPOINT%%:*}"                                        # bare host, for psql -h below
export PGPASSWORD="$RDS_PASS"   # so the psql calls in step 5 don't prompt interactively for it

aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=terraform output -raw app_secrets_arn)" \
  --secret-string "{\"fernet_key\":\"<paste from above>\",\"webserver_secret_key\":\"<generate the same way>\",\"openai_api_key\":\"<your key, or empty string>\",\"slack_webhook_url\":\"<your webhook, or empty string>\"}"

aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=terraform output -raw airflow_db_conn_secret_arn)" \
  --secret-string "postgresql+psycopg2://${RDS_USER}:${RDS_PASS}@${RDS_ENDPOINT}/airflow"
```

## 5. One-time RDS bootstrap

The ECS task definitions pull `${ecr_repository_url}:latest`, but a fresh `terraform apply` leaves
that repo empty. The ECS *services* tolerate this — they retry on every subsequent deploy (see the
spec's Risks note: "First `terraform apply` has no image to pull yet ... Expected and benign") — but
the one-off `run-task` bootstrap in step 5.1 below has no such retry, so push an image manually
first. This is the same build `deploy.yml` (step 8) will later automate; doing it once by hand here
unblocks both the bootstrap task and the ECS services' first launch:

```bash
# --region must match var.aws_region (terraform/variables.tf; default "us-east-1") — update it if
# you changed that variable
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin "$(terraform -chdir=terraform output -raw ecr_repository_url | cut -d/ -f1)"
# --platform linux/amd64: the ECS task defs default to LINUX_X86_64 — required if you're building on
# an ARM host (e.g. Apple Silicon), otherwise ECS can't pull the resulting image
docker build --platform linux/amd64 -t "$(terraform -chdir=terraform output -raw ecr_repository_url):latest" .
docker push "$(terraform -chdir=terraform output -raw ecr_repository_url):latest"
```

Three ordered steps, run once per fresh `terraform apply`:

1. **Airflow metadata migrate + admin user**, via a one-off `aws ecs run-task` against the scheduler
   task definition, into the same subnets/`ecs_sg` as the running services, `assignPublicIp=ENABLED`
   (needed to reach ECR). Subnet IDs and the security group ID aren't exposed as Terraform outputs, so
   look them up by the `Name`/`group-name` tags Terraform assigns them:
   ```bash
   ECS_CLUSTER=$(terraform -chdir=terraform output -raw ecs_cluster_name)
   SUBNET_IDS=$(aws ec2 describe-subnets \
     --filters "Name=tag:Name,Values=${ECS_CLUSTER}-public-a,${ECS_CLUSTER}-public-b" \
     --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
   SG_ID=$(aws ec2 describe-security-groups \
     --filters "Name=group-name,Values=${ECS_CLUSTER}-ecs-sg" \
     --query 'SecurityGroups[0].GroupId' --output text)

   aws ecs run-task \
     --cluster "$ECS_CLUSTER" \
     --task-definition "${ECS_CLUSTER}-scheduler" \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_IDS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
     --overrides '{"containerOverrides":[{"name":"scheduler","command":["bash","-c","airflow db migrate && airflow users create --username admin --firstname Admin --lastname User --role Admin --email admin@example.com --password <choose one>"]}]}'
   ```
2. **Warehouse database + pgvector**, from the operator's IP (paths below are repo-root relative —
   run from the repo root, as step 3 leaves you):
   ```bash
   psql -h "$RDS_HOST" -U "$RDS_USER" -d airflow -c "CREATE DATABASE warehouse;"
   psql -h "$RDS_HOST" -U "$RDS_USER" -d warehouse -f warehouse-init/01_pgvector.sql
   psql -h "$RDS_HOST" -U "$RDS_USER" -d warehouse -f warehouse-init/02_catalog_embeddings_unique_index.sql
   ```
3. **Governance roles:**
   ```bash
   export GOVERNANCE_ENGINEER_PASSWORD="<choose a password>"
   export GOVERNANCE_ANALYST_PASSWORD="<choose a password>"
   psql -h "$RDS_HOST" -U "$RDS_USER" -d warehouse \
     -v engineer_password="$GOVERNANCE_ENGINEER_PASSWORD" \
     -v analyst_password="$GOVERNANCE_ANALYST_PASSWORD" \
     -f governance/setup_roles.sql
   ```
   On a fresh warehouse only section 1 (roles + demo users) succeeds: from section 2 on, psql prints
   errors like `schema "public_raw" does not exist` and keeps going (easy to miss in the output),
   since those schemas don't exist until `dbt_pipeline`'s first run. That's expected and safe to
   proceed past — the pipeline only needs the roles (for its `grant_select('engineer')` post-hook).
   Step 10 has you re-run this script once the schemas exist.

## 6. One-time GitHub setup

Set the `AWS_DEPLOY_ROLE_ARN` repo secret to `terraform -chdir=terraform output -raw github_deploy_role_arn`.

## 7. Upload the Olist data once

```bash
aws s3 sync data/olist/ s3://$(terraform -chdir=terraform output -raw s3_bucket_name)/olist-raw/
```

## 8. Deploy

Run the `deploy.yml` workflow manually — GitHub Actions UI ("Run workflow"), or:

```bash
gh workflow run deploy.yml
```

## 9. Find the webserver's public IP

There's no stable URL without an Application Load Balancer (out of scope — see the spec's Risks
section), so look it up after each deploy:

```bash
ECS_CLUSTER=$(terraform -chdir=terraform output -raw ecs_cluster_name)
TASK_ARN=$(aws ecs list-tasks --cluster "$ECS_CLUSTER" \
  --service-name "${ECS_CLUSTER}-webserver" --query 'taskArns[0]' --output text)
ENI_ID=$(aws ecs describe-tasks --cluster "$ECS_CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' --output text)
aws ec2 describe-network-interfaces --network-interface-ids "$ENI_ID" \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text
```

Note: `AIRFLOW__WEBSERVER__BASE_URL` isn't set anywhere in this deployment, so any links Airflow
generates itself (e.g. in Slack alert messages) will point at `localhost` rather than the public IP
above. This is an expected consequence of the no-ALB/no-stable-URL design (see the spec's Risks
section) — there's no fixed hostname to set `BASE_URL` to in the first place — not a bug to fix.

## 10. Log in and trigger the pipeline

Log in at `http://<public-ip>:8080` with the admin user created in step 5, unpause `dbt_pipeline`
(it lands **paused** on first deploy, per the existing `DAGS_ARE_PAUSED_AT_CREATION=true` config —
easy to mistake for a broken deploy if you don't expect it), trigger it, and confirm task logs
render in the UI (proves remote S3 task logging is wired up correctly between the two separate
Fargate containers).

Once that first run succeeds, re-run step 5.3's `setup_roles.sql` command: `dbt_pipeline` has now
created the schemas that script grants on, so the schema grants and row-level security skipped on
the fresh warehouse apply this time.

## 11. Teardown

In a **shared** AWS account where other repositories/projects depend on the same GitHub OIDC provider
(step 3's troubleshooting note), run this first so `destroy` below doesn't remove it out from under
them — skip this in a dedicated/solo account, since it just means re-importing it next session for no
benefit:

```bash
terraform -chdir=terraform state rm aws_iam_openid_connect_provider.github
```

Then, in all cases:

```bash
terraform -chdir=terraform destroy
```

Full teardown between demo sessions — $0 standing cost. `destroy` removes the ECR repo along with
everything else, so the next `apply` starts from empty again. The accepted tradeoff is redoing steps
4, 5 (including its manual image-push lead-in — the old image is gone with the old repo), 7, and 8
(~10-15 min, plus image build/push time — the image is ~3–4 GB, see the Cost estimate's ECR row)
before the next session: 4 (secrets — the RDS master secret is regenerated by the new
`apply`), 5 (RDS bootstrap, image push included — new database, no migrate/admin-user/pgvector/
governance state survives), 7 (Olist re-upload — new S3 bucket), and 8 (deploy — re-pushes the image
via CI so later redeploys go back to being one command). Step 6 (GitHub setup) only needs redoing if
the deploy role's ARN changed. In a **shared** AWS account where you `state rm`'d the OIDC provider
before this `destroy` (see step 3's troubleshooting note), also redo the `terraform import` at the
start of the next session — the provider itself is untouched, but this project's state no longer
knows about it.

## Cost estimate

Approximate `us-east-1` on-demand pricing — re-verify current rates before committing, since AWS
pricing (especially the newer per-hour public IPv4 charge) changes over time.

| Resource | If left running 24/7 | Notes |
|---|---|---|
| Fargate — webserver (0.5 vCPU / 2 GB) | ~$21/mo | 2 GB + 2 gunicorn workers: 1 GB with the default 4 workers was OOM-killed |
| Fargate — scheduler (1 vCPU / 2 GB) | ~$36/mo | dbt builds are more CPU/memory-hungry than the webserver; sized up deliberately |
| RDS `db.t4g.micro` + 20 GB gp3 | ~$14/mo | bump to `db.t4g.small` (~+$12/mo) if the Olist build OOMs the micro class — Paid-plan AWS accounts only; a Free-plan account must stay on micro (an `apply` with small fails partway through with `FreeTierRestrictionError`) |
| Public IPv4 (2 ECS ENIs + 1 RDS) | ~$11/mo | direct cost of public-subnets-no-NAT (§4) — still roughly a third of a NAT gateway's standing charge |
| ECR storage | ~$0.30–0.40/mo | image is ~3–4 GB (see Risks) |
| S3 (task logs + Olist raw data) | ~$0.05/mo | 121 MB Olist data + low log volume |
| Secrets Manager | ~$0.80/mo | 1 RDS-managed secret + 1 app-secrets JSON |
| CloudWatch Logs | ~$0.50/mo | short retention, low volume |
| **Total if left running continuously** | **~$83–88/mo** | not the intended usage pattern |

**Per demo session** (stand-up → bootstrap → trigger a run → review → `terraform destroy`, a few
hours): roughly **$0.30–0.50 total**. Given the full-teardown decision, there is no standing cost
between demo sessions at all.
