# Bedrock AgentCore EKS Monitor

Autonomous Kubernetes troubleshooting agent powered by **Amazon Bedrock AgentCore** and **Claude**. Receives Prometheus Alertmanager webhooks and auto-remediates OOMKilled and CrashLoopBackOff issues, or escalates ImagePullBackOff to JIRA and Slack.

```
Alertmanager → Webhook Server (EKS) → AgentCore Runtime (ARM64) → Claude Haiku
                                                                         │
                                              ┌──────────────────────────┤
                                              ▼                          ▼
                                     Auto-remediate                  Escalate
                                  (patch memory + restart)      (JIRA ticket + Slack)
```

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| AWS CLI v2 | Configured with credentials for your account |
| eksctl | >= 0.100 |
| Terraform | >= 1.3 |
| kubectl | >= 1.28 |
| Helm | >= 3.12 |
| Docker | Any recent version (with `buildx` for ARM64) |
| Python 3 + pip | `pyyaml` must be installed (`pip install pyyaml`) |
| Bedrock model access | `anthropic.claude-3-haiku-20240307-v1:0` enabled in your account |

---

## Full Stack Setup (Fresh Install)

These are the exact commands to bring up the entire stack from scratch.

### Step 0 — Clone the Repository

```bash
git clone git@github.com:ranjiniganeshan/bedrock-agentcore-eks-monitor.git
cd bedrock-agentcore-eks-monitor
```

---

### Step 1 — Create the EKS Cluster

```bash
eksctl create cluster \
  --name demo-cluster \
  --region us-east-1 \
  --nodegroup-name demo-nodes \
  --node-type t3.medium \
  --nodes 2 \
  --nodes-min 1 \
  --nodes-max 3 \
  --managed \
  --with-oidc
```

This takes ~15–20 minutes. Once complete, update your kubeconfig:

```bash
aws eks update-kubeconfig --region us-east-1 --name demo-cluster
kubectl get nodes   # verify both nodes are Ready
```

---

### Step 2 — Create the STS VPC Endpoint

Required for IRSA (pod-level IAM) to work inside the cluster.

```bash
# Get cluster VPC and subnets
VPC_ID=$(aws eks describe-cluster --name demo-cluster --region us-east-1 \
  --query 'cluster.resourcesVpcConfig.vpcId' --output text)

SG=$(aws eks describe-cluster --name demo-cluster --region us-east-1 \
  --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId' --output text)

# List subnets and pick one per AZ (no duplicates allowed)
aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[*].{ID:SubnetId,AZ:AvailabilityZone}' --output table

# Create the interface endpoint (replace subnet IDs with one per AZ)
aws ec2 create-vpc-endpoint \
  --vpc-id "$VPC_ID" \
  --service-name "com.amazonaws.us-east-1.sts" \
  --vpc-endpoint-type Interface \
  --subnet-ids <subnet-in-az1> <subnet-in-az2> \
  --security-group-ids "$SG" \
  --private-dns-enabled \
  --region us-east-1

# Wait for it to become available (~30–60s)
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$VPC_ID" \
           "Name=service-name,Values=com.amazonaws.us-east-1.sts" \
  --query 'VpcEndpoints[*].{ID:VpcEndpointId,State:State}' --output table
```

---

### Step 3 — Install Python Dependency

The deploy script uses `pyyaml` to patch the EKS `aws-auth` ConfigMap.

```bash
pip install --break-system-packages pyyaml
# or: pip install --user pyyaml
```

---

### Step 4 — Create ECR Repository and Push the Agent Image

AgentCore Runtime runs on **Graviton (ARM64)**. The image must be built for `linux/arm64`.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/k8s-agent:latest"

# Create ECR repository
aws ecr create-repository --repository-name k8s-agent --region $REGION

# Authenticate Docker to ECR
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin \
    ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# Build for ARM64 and push
docker build \
  --platform linux/arm64 \
  --provenance=false \
  -t $IMAGE \
  agentcore/

docker push $IMAGE
```

---

### Step 5 — Deploy the Stack

`deploy.sh` runs `terraform init`, imports any pre-existing resources (ECR, OIDC provider, IAM roles), applies all infrastructure, patches `aws-auth`, and writes the AgentCore Runtime ID.

```bash
bash deploy.sh
```

To include JIRA and Slack escalation, set credentials before running:

```bash
export TF_VAR_jira_base_url=https://<your-org>.atlassian.net/
export TF_VAR_jira_project_key=<PROJECT_KEY>
export TF_VAR_jira_email=<your-email>
export TF_VAR_jira_api_token=<your-jira-api-token>
export TF_VAR_slack_webhook_url=https://hooks.slack.com/services/...
bash deploy.sh
```

> **JIRA and Slack are optional.** Without them, OOM and CrashLoopBackOff auto-remediation still works.

Expected outputs:

```
agentcore_runtime_id        = "k8s_troubleshooter-<id>"
agentcore_runtime_role_arn  = "arn:aws:iam::<account>:role/agentcore-k8s-troubleshooter-role"
agentcore_artifacts_bucket  = "agentcore-artifacts-<account>-us-east-1"
webhook_server_irsa_role_arn= "arn:aws:iam::<account>:role/webhook-server-irsa-role"
webhook_alertmanager_url    = "http://alertmanager-webhook-server.alertmanager-agent.svc.cluster.local/api/investigate"
```

---

### Step 6 — Install the Monitoring Stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  -f monitoring/values.yaml
```

Verify all pods are Running:

```bash
kubectl get pods -n monitoring
```

---

### Step 7 — Apply Alert Rules and Demo App

```bash
# PrometheusRules: OOMKilled, CrashLoopBackOff, ImagePullBackOff
kubectl apply -f monitoring/demo-app-alerts.yaml

# Demo app with a low 64Mi memory limit (easy to OOMKill)
kubectl apply -f app/deployment.yml
kubectl get pods   # wait for Running
```

---

### Step 8 — Configure Alertmanager

```bash
kubectl apply -f monitoring/alertmanager-secret.yaml

kubectl -n monitoring rollout restart \
  statefulset/alertmanager-monitoring-kube-prometheus-alertmanager
```

---

### Verify the Full Stack

```bash
kubectl get nodes
kubectl get pods -n monitoring
kubectl get pods -n alertmanager-agent
kubectl get pods -n default -l app=demo-app
```

All components should show `Running`. The stack is ready to handle alerts.

---

## Repository Layout

```
bedrock-agentcore-eks-monitor/
├── agentcore/                    # Agent runtime code (runs inside AgentCore)
│   ├── main.py                   # Strands agent entrypoint
│   ├── k8s_tools.py              # K8s tools: patch memory, rollout restart
│   ├── escalation_tools.py       # JIRA + Slack escalation tools
│   ├── Dockerfile                # linux/arm64 (AgentCore runs on Graviton)
│   └── requirements.txt
├── app/                          # Demo apps for testing
│   ├── test-crash.yaml           # Deployment that OOMKills repeatedly
│   ├── test-crashloop.yaml       # Deployment that exits immediately (CrashLoopBackOff)
│   └── test-imagepull.yaml       # Deployment with bad image (ImagePullBackOff)
├── infra/                        # Terraform — deploy from here
│   ├── agentcore_runtime.tf      # AgentCore Runtime + S3 artifact
│   ├── agentcore_iam.tf          # IAM roles (AgentCore execution + IRSA)
│   ├── ecr_private.tf            # Private ECR repository
│   ├── webhook_k8s.tf            # Webhook server K8s resources
│   ├── eks_cluster.tf            # EKS data sources
│   ├── variables.tf
│   ├── outputs.tf
│   └── scripts/
│       └── deploy_agentcore_runtime.py
├── webhook-server/               # Alertmanager → AgentCore bridge
│   ├── webhook_server_bedrock.py # FastAPI server
│   └── Dockerfile
├── monitoring/                   # Alertmanager + Prometheus configs
│   ├── alertmanager-secret.yaml  # Alertmanager routes + webhook config
│   ├── demo-app-alerts.yaml      # Prometheus alert rules for demo apps
│   └── values.yaml
└── demo.sh                       # Run all 3 scenarios end-to-end (automated)
```

---

---

## Testing the Stack

Prometheus detects pod failures, Alertmanager fires the webhook, and AgentCore remediates — no manual trigger needed.

Alert timing (tuned for demo speed):
- Prometheus evaluates every **30s**
- All alert rules fire immediately (`for: 0m`)
- Alertmanager `group_wait` is **10s**
- **Total time from pod crash to agent action: ~60–90 seconds**

---

### Setup (run once per terminal session)

> **Required before running any scenario.**

```bash
# Port-forward the webhook server
kubectl port-forward svc/alertmanager-webhook-server -n alertmanager-agent 8091:80 &

# Verify it's up
curl -s http://localhost:8091/health
```

Expected: `{"status":"ok"}`

---

### Run All Scenarios End-to-End

`demo.sh` deploys the test app, runs all three scenarios, prints agent responses, and cleans up.

```bash
bash demo.sh
```

---

### Scenario 1 — OOMKilled

Deploys a pod (`polinux/stress`) with a `32Mi` memory limit that immediately exceeds it. AgentCore patches the limit to `256Mi` and restarts the pod.

```bash
kubectl apply -f app/test-crash.yaml
kubectl get pods -n default -w
```

Expected (~60–90s): `OOMKilled` → agent patches memory `32Mi → 256Mi` → pod `Running`.

**Verify the fix:**
```bash
kubectl get deployment demo-app-crashing -n default \
  -o jsonpath='Memory limit: {.spec.template.spec.containers[0].resources.limits.memory}{"\n"}'
```

---

### Scenario 2 — CrashLoopBackOff

Deploys a pod that exits immediately. AgentCore triggers a rollout restart.

```bash
kubectl apply -f app/test-crashloop.yaml
kubectl get pods -n default -w
```

**Watch agent logs:**
```bash
kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f
```

Expected (~60–90s): `CrashLoopBackOff` → agent runs `kubectl rollout restart` → pod `Running`.

---

### Scenario 3 — ImagePullBackOff (JIRA + Slack escalation)

Deploys a pod with a bad image. AgentCore cannot self-heal this — it creates a JIRA ticket and sends a Slack notification.

```bash
kubectl apply -f app/test-imagepull.yaml
kubectl get pods -n default -w
```

**Watch escalation:**
```bash
kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f
```

Expected (~60s): agent creates a JIRA ticket and sends a Slack notification.

---

### Scenario 4 — Node NotReady / MemoryPressure (JIRA + Slack escalation)

When a node enters `MemoryPressure` or goes `NotReady`, Prometheus fires a `KubeNodeNotReady` alert → Alertmanager sends the webhook → AgentCore cordons the node and escalates to JIRA and Slack.

No manual trigger needed — this fires automatically when a node becomes unhealthy.

**Watch it fire:**
```bash
kubectl get nodes -w
kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f
```

Expected: agent cordons the node, creates a JIRA ticket, and sends a Slack notification.

---

### Cleanup

```bash
# Delete all test deployments
kubectl delete deployment demo-app-crashing demo-app-crashloop demo-app-imagepull \
  -n default --ignore-not-found

# Stop port-forward
pkill -f "port-forward.*8091"
```

---

## Destroy the Stack

```bash
cd infra/

# Remove ECR from state to preserve the container image
terraform state rm aws_ecr_repository.k8s_agent aws_ecr_lifecycle_policy.k8s_agent

# Delete the AgentCore Runtime (not managed by terraform destroy)
python3 -c "
import boto3, time
client = boto3.client('bedrock-agentcore-control', region_name='us-east-1')
for rt in client.list_agent_runtimes().get('agentRuntimes', []):
    print(f'Deleting {rt[\"agentRuntimeName\"]}...')
    client.delete_agent_runtime(agentRuntimeId=rt['agentRuntimeId'])
"

# Destroy remaining resources
terraform destroy \
  -var="aws_account_id=<AWS_ACCOUNT_ID>" \
  -var="agentcore_container_image=<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/k8s-agent:latest" \
  -auto-approve
```

---

## Architecture Notes

| Component | Detail |
|-----------|--------|
| AgentCore Runtime | Runs on AWS Graviton (ARM64) — image must be `linux/arm64` |
| Authentication | Webhook server uses IRSA; AgentCore uses its own execution role |
| Model | `anthropic.claude-3-haiku-20240307-v1:0` (fast, cost-efficient) |
| Session ID | Per-alert fingerprint, minimum 33 characters (AgentCore requirement) |
| Cold start | Container-based deployment (~3s vs ~3min for S3 zip + pip install) |
| JIRA project | Must exist before deployment — verify with your Atlassian admin |
