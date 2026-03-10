# ─── OIDC Provider (required for IRSA) ───────────────────────────────────────

data "tls_certificate" "eks" {
  url = data.aws_eks_cluster.example.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint]
  url             = data.aws_eks_cluster.example.identity[0].oidc[0].issuer
}

# ─── IAM propagation delay ───────────────────────────────────────────────────
# IAM roles and policies can take a few seconds to propagate globally.
# This sleep prevents the AgentCore Runtime deploy from failing on first apply.

resource "time_sleep" "iam_propagation" {
  create_duration = "30s"
  depends_on = [
    aws_iam_openid_connect_provider.eks,
    aws_iam_role_policy.agentcore_runtime_policy,
  ]
}

# ─── AgentCore Runtime execution role ────────────────────────────────────────

resource "aws_iam_role" "agentcore_runtime_role" {
  name = "agentcore-k8s-troubleshooter-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "bedrock-agentcore.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "agentcore_runtime_policy" {
  name = "agentcore-runtime-policy"
  role = aws_iam_role.agentcore_runtime_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockModelInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = "*"
      },
      {
        Sid    = "EKSDescribe"
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
          "eks:ListClusters",
        ]
        Resource = data.aws_eks_cluster.example.arn
      },
      {
        Sid      = "STSPresignedURL"
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/bedrock-agentcore/*"
      },
      {
        Sid    = "S3CodeArtifact"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
        ]
        Resource = "${aws_s3_bucket.agentcore_artifacts.arn}/*"
      },
      {
        Sid    = "ECRPullImage"
        Effect = "Allow"
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = aws_ecr_repository.k8s_agent.arn
      },
      {
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
    ]
  })
}

# ─── Webhook server IRSA role ─────────────────────────────────────────────────
# The webhook server pod calls AgentCore invoke_agent_runtime — needs this role.

locals {
  oidc_host = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")
}

resource "aws_iam_role" "webhook_server_irsa" {
  name = "webhook-server-irsa-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.eks.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_host}:sub" = "system:serviceaccount:alertmanager-agent:webhook-server-sa"
            "${local.oidc_host}:aud" = "sts.amazonaws.com"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "webhook_server_policy" {
  name = "webhook-server-policy"
  role = aws_iam_role.webhook_server_irsa.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AgentCoreInvoke"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime",
        ]
        Resource = "arn:aws:bedrock-agentcore:${var.aws_region}:${var.aws_account_id}:runtime/*"
      }
    ]
  })
}
