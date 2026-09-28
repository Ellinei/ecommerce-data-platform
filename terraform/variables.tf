variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "operator_ip" {
  description = "Operator's current public IPv4 address (bare IP, no CIDR suffix — the /32 is appended where consumed). No default: re-supply on each apply if it changes."
  type        = string
}

variable "project_name" {
  description = "Prefix used for resource names and tags across every Terraform-managed resource."
  type        = string
  default     = "airflow-dbt-pipeline"
}

variable "db_instance_class" {
  description = "RDS instance class. Bump to db.t4g.small if the Olist dbt build is found to OOM the micro class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_master_username" {
  description = "RDS master username. Not \"admin\" — RDS PostgreSQL rejects several reserved master usernames (admin, rdsadmin, public among them)."
  type        = string
  default     = "dbadmin"
}

variable "github_oidc_sub_prefix" {
  description = "Prefix of the GitHub Actions OIDC `sub` claim this deploy role trusts — must match exactly what GitHub sends, which depends on the repo's OIDC settings (e.g. \"repo:owner@123/repo@456\" with immutable subjects, \"repo:owner/repo\" without). Get it with: gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix. No default: must be supplied to scope the trust policy to this repo only."
  type        = string
}
