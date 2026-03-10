# ─── S3 bucket for AgentCore code artifact ───────────────────────────────────

resource "aws_s3_bucket" "agentcore_artifacts" {
  bucket = "agentcore-artifacts-${var.aws_account_id}-${var.aws_region}"
}

resource "aws_s3_bucket_versioning" "agentcore_artifacts" {
  bucket = aws_s3_bucket.agentcore_artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "agentcore_artifacts" {
  bucket = aws_s3_bucket.agentcore_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "agentcore_artifacts" {
  bucket                  = aws_s3_bucket.agentcore_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ─── Package agentcore/ code ──────────────────────────────────────────────────

data "archive_file" "agentcore_code" {
  type        = "zip"
  source_dir  = "${path.module}/../agentcore"
  output_path = "${path.module}/.build/agentcore_code.zip"
  excludes    = ["__pycache__", ".env", "*.md"]
}

resource "aws_s3_object" "agentcore_code" {
  bucket = aws_s3_bucket.agentcore_artifacts.id
  key    = "agentcore_code.zip"
  source = data.archive_file.agentcore_code.output_path
  etag   = data.archive_file.agentcore_code.output_md5

  depends_on = [aws_s3_bucket.agentcore_artifacts]
}

# ─── Deploy AgentCore Runtime ─────────────────────────────────────────────────
# Uses a Python script to create-or-update the AgentCore Runtime via boto3.
# Re-runs whenever the code zip changes (tracked by MD5 hash).

locals {
  # Use explicit image if provided, otherwise default to the private ECR repo
  effective_container_image = (
    var.agentcore_container_image != "" ?
    var.agentcore_container_image :
    "${aws_ecr_repository.k8s_agent.repository_url}:latest"
  )
}

resource "null_resource" "agentcore_runtime" {
  triggers = {
    container_image   = local.effective_container_image
    role_arn          = aws_iam_role.agentcore_runtime_role.arn
    model_id          = var.bedrock_model_id
    jira_base_url     = var.jira_base_url
    jira_project_key  = var.jira_project_key
    slack_configured  = var.slack_webhook_url != "" ? "true" : "false"
  }

  provisioner "local-exec" {
    environment = {
      AWS_REGION        = var.aws_region
      RUNTIME_NAME      = "k8s_troubleshooter"
      ROLE_ARN          = aws_iam_role.agentcore_runtime_role.arn
      CONTAINER_IMAGE   = local.effective_container_image
      MODEL_ID          = var.bedrock_model_id
      EKS_CLUSTER       = data.aws_eks_cluster.example.name
      OUTPUT_FILE       = "${path.module}/.build/agentcore_runtime_id.txt"
      JIRA_BASE_URL     = var.jira_base_url
      JIRA_PROJECT_KEY  = var.jira_project_key
      JIRA_EMAIL        = var.jira_email
      JIRA_API_TOKEN    = var.jira_api_token
      SLACK_WEBHOOK_URL = var.slack_webhook_url
    }
    command = "python3 ${path.module}/scripts/deploy_agentcore_runtime.py"
  }

  depends_on = [
    aws_iam_role_policy.agentcore_runtime_policy,
    aws_ecr_repository.k8s_agent,
    time_sleep.iam_propagation,
  ]
}

# Read back the runtime ID written by the deploy script
data "local_file" "agentcore_runtime_id" {
  filename   = "${path.module}/.build/agentcore_runtime_id.txt"
  depends_on = [null_resource.agentcore_runtime]
}
