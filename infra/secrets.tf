# Secrets live in Parameter Store, and Terraform never learns their values.
#
# Each parameter is created with a placeholder and then ignored, so the real
# value is set out of band with `aws ssm put-parameter --overwrite` (see
# README.md). Putting them in tfvars instead would write them into the state
# file in plain text, which is the thing worth avoiding.

resource "aws_ssm_parameter" "claude_token" {
  name        = "/${local.name}/CLAUDE_CODE_OAUTH_TOKEN"
  description = "From `claude setup-token` on a machine with a browser. Subscription auth."
  type        = "SecureString"
  value       = "PLACEHOLDER-set-with-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "dex_token" {
  name        = "/${local.name}/DEX_TOKEN"
  description = "Shared secret for the dex API. Empty would mean no auth at all."
  type        = "SecureString"
  value       = "PLACEHOLDER-set-with-aws-ssm-put-parameter"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "postgres_password" {
  name        = "/${local.name}/POSTGRES_PASSWORD"
  description = "Generated once. Nothing outside the VPC can reach Postgres."
  type        = "SecureString"
  value       = random_password.postgres.result

  lifecycle {
    ignore_changes = [value]
  }
}
