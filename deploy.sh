#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  One-command deploy: handles pre-existing resources, imports,
#  and runs terraform apply — no manual steps required.
#  Usage: bash deploy.sh
#
#  JIRA/Slack: copy .env.example → .env, fill in credentials,
#  then run this script. It will be sourced automatically.
# ─────────────────────────────────────────────────────────────
set -e

# Source credentials from .env if present
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "==> Loading credentials from .env"
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
else
  echo "==> No .env file found (JIRA/Slack escalation will be skipped)"
  echo "    Copy .env.example to .env and fill in credentials to enable escalation."
  echo ""
fi

REGION=${AWS_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/k8s-agent:latest"
INFRA_DIR="$SCRIPT_DIR/infra"

echo "Account : $ACCOUNT_ID"
echo "Region  : $REGION"
echo "Image   : $IMAGE"
echo ""

cd "$INFRA_DIR"

# ── Step 1: Init ─────────────────────────────────────────────
echo "==> terraform init"
terraform init -upgrade -input=false

# ── Step 2: Import pre-existing resources ─────────────────────
echo "==> Checking for pre-existing resources to import..."

# ECR repository
if aws ecr describe-repositories --repository-names k8s-agent \
    --region "$REGION" &>/dev/null 2>&1; then
  if ! terraform state show aws_ecr_repository.k8s_agent &>/dev/null 2>&1; then
    echo "  Importing ECR repo k8s-agent..."
    terraform import \
      -var="aws_account_id=$ACCOUNT_ID" \
      aws_ecr_repository.k8s_agent k8s-agent
  else
    echo "  ECR repo already in state — skipping import"
  fi
fi

# OIDC provider
OIDC_URL=$(aws eks describe-cluster --name demo-cluster --region "$REGION" \
  --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null || true)
if [ -n "$OIDC_URL" ] && [ "$OIDC_URL" != "None" ]; then
  OIDC_HOST="${OIDC_URL#https://}"
  OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_HOST}"
  if aws iam get-open-id-connect-provider \
      --open-id-connect-provider-arn "$OIDC_ARN" &>/dev/null 2>&1; then
    if ! terraform state show aws_iam_openid_connect_provider.eks &>/dev/null 2>&1; then
      echo "  Importing OIDC provider..."
      terraform import \
        -var="aws_account_id=$ACCOUNT_ID" \
        aws_iam_openid_connect_provider.eks "$OIDC_ARN"
    else
      echo "  OIDC provider already in state — skipping import"
    fi
  fi
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
