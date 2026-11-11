# Documentation

Start with the [project README](../README.md) for the one-page overview, then pick the guide that
matches what you want to do.

## Guides

| I want to… | Read |
|---|---|
| Understand what the pieces are and how data moves through them | [architecture.md](architecture.md) |
| Run it on my machine, run the tests, or run dev + prod side by side | [local-development.md](local-development.md) |
| Deploy it to AWS (and tear it down again) | [aws-deployment.md](aws-deployment.md) |
| Know which Olist CSV files to download and where to put them | [../data/olist/README.md](../data/olist/README.md) |

## Project history: specs & plans

The project was built in phases. Each phase has a **design spec** (the *why*: goals, decisions,
risks) and an **implementation plan** (the *how*: step-by-step tasks). They are point-in-time
records — the guides above describe the current state.

| Phase | What it added | Design spec | Implementation plan |
|---|---|---|---|
| Olist integration | Real e-commerce data: ingestion task, 9 staging views, 2 marts, governance, ML training | [spec](superpowers/specs/2026-07-14-olist-ecommerce-integration-design.md) | [plan](superpowers/plans/2026-07-14-olist-ecommerce-integration.md) |
| 1 — Testing & CI | pytest suite, ruff, GitHub Actions CI | [spec](superpowers/specs/2026-07-17-phase1-testing-ci-design.md) | [plan](superpowers/plans/2026-07-17-phase1-testing-ci.md) |
| 2 — Reliability | Shared retries, Slack alerting, idempotent tasks | [spec](superpowers/specs/2026-07-18-phase2-retries-alerting-idempotency-design.md) | [plan](superpowers/plans/2026-07-18-phase2-retries-alerting-idempotency.md) |
| 3 — Dev/prod separation | Second "prod" stack from the same compose file | [spec](superpowers/specs/2026-08-18-phase3-dev-prod-separation-design.md) | [plan](superpowers/plans/2026-08-18-phase3-dev-prod-separation.md) |
| 4 — Cloud deployment | AWS via Terraform (ECS Fargate, RDS, ECR, S3) + manual deploy workflow | [spec](superpowers/specs/2026-08-18-phase4-cloud-deployment-design.md) | plans: [4a app & image prep](superpowers/plans/2026-08-19-phase4a-app-image-prep.md), [4b AWS infra & deploy](superpowers/plans/2026-08-19-phase4b-aws-infra-deploy.md) |
