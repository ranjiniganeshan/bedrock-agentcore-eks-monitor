# EKS cluster already exists — use data sources to reference it
data "aws_eks_cluster" "example" {
  name = "demo-cluster"
}

data "aws_eks_cluster_auth" "example" {
  name = "demo-cluster"
}

# ── STS VPC endpoint: allow HTTPS from VPC so IRSA token exchange works ───────

data "aws_vpc" "eks" {
  id = data.aws_eks_cluster.example.vpc_config[0].vpc_id
}

data "aws_vpc_endpoint" "sts" {
  vpc_id       = data.aws_vpc.eks.id
  service_name = "com.amazonaws.${var.aws_region}.sts"
}

resource "aws_security_group_rule" "sts_endpoint_https" {
  description       = "Allow HTTPS from VPC CIDR to STS endpoint (required for IRSA)"
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.eks.cidr_block]
  security_group_id = tolist(data.aws_vpc_endpoint.sts.security_group_ids)[0]

  lifecycle {
    # Rule may already exist from a prior setup — ignore if so
    ignore_changes = all
  }
}

# ── aws-auth: grant AgentCore Runtime role access to the K8s API ──────────────
# Idempotent — only adds the entry if it isn't already present.

resource "null_resource" "aws_auth_agentcore" {
  triggers = {
    role_arn = aws_iam_role.agentcore_runtime_role.arn
  }

  provisioner "local-exec" {
    command = <<-SCRIPT
      python3 - <<'EOF'
import subprocess, json, sys

try:
    import yaml
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml", "-q"])
    import yaml

role_arn = "${aws_iam_role.agentcore_runtime_role.arn}"
new_entry = {
    "rolearn":  role_arn,
    "username": "agentcore-agent",
    "groups":   ["system:masters"],
}

cm = json.loads(subprocess.check_output(
    ["kubectl", "get", "configmap", "aws-auth", "-n", "kube-system", "-o", "json"]
))
roles = yaml.safe_load(cm["data"].get("mapRoles", "[]")) or []

if any(r.get("rolearn") == role_arn for r in roles):
    print("[aws-auth] AgentCore role already present — skipping")
    sys.exit(0)

roles.append(new_entry)
cm["data"]["mapRoles"] = yaml.dump(roles, default_flow_style=False)
patch = json.dumps({"data": {"mapRoles": cm["data"]["mapRoles"]}})
subprocess.run(
    ["kubectl", "patch", "configmap", "aws-auth",
     "-n", "kube-system", "--patch", patch],
    check=True,
)
print("[aws-auth] AgentCore role added successfully")
EOF
    SCRIPT
  }

  depends_on = [aws_iam_role.agentcore_runtime_role]
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "cluster_endpoint" {
  value = data.aws_eks_cluster.example.endpoint
}

output "cluster_name" {
  value = data.aws_eks_cluster.example.name
}
