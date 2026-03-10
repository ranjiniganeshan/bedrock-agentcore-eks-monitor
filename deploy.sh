#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  One-command deploy — no manual steps required.
#  Usage: bash deploy.sh
#
#  JIRA/Slack: credentials are loaded from .env automatically.
#  Copy .env.example to .env and fill in your values.
# ─────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/k8s-agent:latest"
INFRA_DIR="$SCRIPT_DIR/infra"

# ── Load credentials from .env ────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "==> Loading credentials from .env"
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
else
  echo "WARNING: .env not found — JIRA/Slack escalation will not work."
  echo "         Copy .env.example to .env and fill in your credentials."
  echo ""
fi

echo "Account : $ACCOUNT_ID"
echo "Region  : $REGION"
echo "Image   : $IMAGE"
echo ""

cd "$INFRA_DIR"

# ── Step 1: Init ──────────────────────────────────────────────
echo "==> terraform init"
terraform init -upgrade -input=false

# ── Step 2: Import pre-existing resources ─────────────────────
# Each block checks AWS/K8s first, then checks Terraform state.
# Only imports if the resource exists but is not yet in state.

import_if_needed() {
  local label="$1" resource="$2" id="$3"
  if ! terraform state show "$resource" &>/dev/null 2>&1; then
    echo "  Importing $label..."
    terraform import -var="aws_account_id=$ACCOUNT_ID" "$resource" "$id" || true
  else
    echo "  $label already in state — skipping"
  fi
}

echo "==> Checking for pre-existing resources to import..."

# ECR repository
if aws ecr describe-repositories --repository-names k8s-agent \
    --region "$REGION" &>/dev/null 2>&1; then
  import_if_needed "ECR repo" aws_ecr_repository.k8s_agent k8s-agent
fi

# OIDC provider
OIDC_URL=$(aws eks describe-cluster --name demo-cluster --region "$REGION" \
  --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null || true)
if [ -n "$OIDC_URL" ] && [ "$OIDC_URL" != "None" ]; then
  OIDC_HOST="${OIDC_URL#https://}"
  OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"
  if aws iam get-open-id-connect-provider \
      --open-id-connect-provider-arn "$OIDC_ARN" &>/dev/null 2>&1; then
    import_if_needed "OIDC provider" aws_iam_openid_connect_provider.eks "$OIDC_ARN"
  fi
fi

# IAM roles
if aws iam get-role --role-name agentcore-k8s-troubleshooter-role &>/dev/null 2>&1; then
  import_if_needed "IAM role agentcore" \
    aws_iam_role.agentcore_runtime_role agentcore-k8s-troubleshooter-role
fi
if aws iam get-role --role-name webhook-server-irsa-role &>/dev/null 2>&1; then
  import_if_needed "IAM role webhook-irsa" \
    aws_iam_role.webhook_server_irsa webhook-server-irsa-role
fi

# S3 bucket
S3_BUCKET="agentcore-artifacts-${ACCOUNT_ID}-${REGION}"
if aws s3api head-bucket --bucket "$S3_BUCKET" &>/dev/null 2>&1; then
  import_if_needed "S3 bucket" aws_s3_bucket.agentcore_artifacts "$S3_BUCKET"
fi

# Kubernetes resources
if kubectl get namespace alertmanager-agent &>/dev/null 2>&1; then
  import_if_needed "K8s namespace" \
    kubernetes_namespace.alertmanager_agent alertmanager-agent
fi
if kubectl get clusterrole webhook-server-role &>/dev/null 2>&1; then
  import_if_needed "K8s ClusterRole" \
    kubernetes_cluster_role.webhook_server webhook-server-role
fi
if kubectl get clusterrolebinding webhook-server-rolebinding &>/dev/null 2>&1; then
  import_if_needed "K8s ClusterRoleBinding" \
    kubernetes_cluster_role_binding.webhook_server webhook-server-rolebinding
fi
if kubectl get serviceaccount webhook-server-sa -n alertmanager-agent &>/dev/null 2>&1; then
  import_if_needed "K8s ServiceAccount" \
    kubernetes_service_account.webhook_server "alertmanager-agent/webhook-server-sa"
fi
if kubectl get service alertmanager-webhook-server -n alertmanager-agent &>/dev/null 2>&1; then
  import_if_needed "K8s Service" \
    kubernetes_service.webhook_server "alertmanager-agent/alertmanager-webhook-server"
fi

# ── Step 3: Apply ─────────────────────────────────────────────
echo ""
echo "==> terraform apply"
terraform apply \
  -var="aws_account_id=$ACCOUNT_ID" \
  -var="agentcore_container_image=$IMAGE" \
  -auto-approve

echo ""
echo "==> Stack is up. Outputs:"
terraform output
