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
| AWS account | EKS cluster named `demo-cluster` running in `us-east-1` |
| Terraform | >= 1.3 |
| kubectl | Configured for `demo-cluster` |
| Docker | For building the agent container image |
| Python 3 + boto3 | For the deploy script |
| Bedrock model access | `anthropic.claude-3-haiku-20240307-v1:0` enabled in your account |

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
├── test2.py                      # Deploy crashloop app → trigger CrashLoopBackOff scenario
├── test3.py                      # Direct webhook: trigger ImagePullBackOff escalation
├── test4.py                      # Direct webhook: trigger Node NotReady escalation
└── demo.sh                       # Run all 3 scenarios end-to-end (automated)
```

---

## Step 1 — Build and Push the Agent Container Image

AgentCore Runtime runs on **Graviton (ARM64)**. The image must be built for `linux/arm64`.

```bash
# Authenticate to ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
    <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Build for ARM64 (required — AgentCore is Graviton-based)
cd agentcore/
docker build \
  --platform linux/arm64 \
  --provenance=false \
  -t <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/k8s-agent:latest \
  .

# Push
docker push <AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/k8s-agent:latest
```

> **Note:** The ECR repository (`k8s-agent`) is created by Terraform in Step 3. If it doesn't exist yet, create it first or run `terraform apply` once without `agentcore_container_image` to create the repo, then build and push, then apply again with the image.

---

## Step 2 — Fix the STS VPC Endpoint Security Group

The EKS nodes use IRSA (IAM Roles for Service Accounts). IRSA requires pods to call AWS STS to exchange tokens. The STS VPC endpoint security group needs an inbound HTTPS rule from the VPC CIDR.

```bash
# Get the STS VPC endpoint security group ID
STS_SG=$(aws ec2 describe-vpc-endpoints --region us-east-1 \
  --filters "Name=service-name,Values=com.amazonaws.us-east-1.sts" \
  --query 'VpcEndpoints[0].Groups[0].GroupId' --output text)

# Allow HTTPS from VPC CIDR (172.31.0.0/16 for default VPC)
aws ec2 authorize-security-group-ingress --region us-east-1 \
  --group-id $STS_SG \
  --protocol tcp --port 443 --cidr 172.31.0.0/16
```

> This is a **one-time fix** — the rule persists across Terraform destroy/apply cycles.

---

## Step 3 — Deploy the Stack

```bash
cd infra/

# Initialize Terraform
terraform init

# If ECR repo already exists (image preserved from previous run), import it
terraform import -var="aws_account_id=<AWS_ACCOUNT_ID>" \
  aws_ecr_repository.k8s_agent k8s-agent

# Deploy
terraform apply \
  -var="aws_account_id=<AWS_ACCOUNT_ID>" \
  -var="agentcore_container_image=<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/k8s-agent:latest" \
  -var='jira_base_url=https://<your-org>.atlassian.net/' \
  -var='jira_project_key=<PROJECT_KEY>' \
  -var='jira_email=<your-email>' \
  -var='jira_api_token=<your-jira-api-token>' \
  -var='slack_webhook_url=https://hooks.slack.com/services/...' \
  -auto-approve
```

> **JIRA and Slack are optional.** Omit those vars to deploy without escalation (OOM + CrashLoop auto-fix still works).

### Terraform Outputs

| Output | Description |
|--------|-------------|
| `agentcore_runtime_id` | AgentCore Runtime ID (e.g. `k8s_troubleshooter-abc123`) |
| `agentcore_runtime_role_arn` | IAM role used by the runtime |
| `agentcore_artifacts_bucket` | S3 bucket for code artifacts |
| `webhook_server_irsa_role_arn` | IRSA role for the webhook pod |
| `webhook_alertmanager_url` | Internal K8s URL for Alertmanager config |

---

## Step 4 — Add AgentCore Role to EKS aws-auth

Allow the AgentCore Runtime to call the K8s API:

```bash
kubectl edit configmap aws-auth -n kube-system
```

Add under `mapRoles`:

```yaml
- rolearn: arn:aws:iam::<AWS_ACCOUNT_ID>:role/agentcore-k8s-troubleshooter-role
  username: agentcore-agent
  groups:
    - system:masters
```

---

## Testing the Stack

There are **two ways** to trigger scenarios:

| Method | How it works | Speed |
|--------|-------------|-------|
| **Automated** (via Prometheus) | Deploy a broken pod → Prometheus detects it → Alertmanager fires the webhook → AgentCore remediates | ~60–90s |
| **Direct webhook** (`curl`) | Send a crafted alert payload directly to `/api/investigate` | Instant |

Alert timing (tuned for demo speed):
- Prometheus evaluates every **30s**
- All alert rules fire immediately (`for: 0m`)
- Alertmanager `group_wait` is **10s**

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

`demo.sh` deploys the test app, sends all three webhook payloads, prints agent responses, and cleans up.

```bash
bash demo.sh
```

---

### Scenario 1 — OOMKilled

Deploys a pod (`polinux/stress`) with a `32Mi` memory limit that immediately exceeds it. AgentCore patches the limit to `256Mi` and restarts the pod.

**Automated — via Prometheus (~60–90s):**
```bash
kubectl apply -f app/test-crash.yaml
kubectl get pods -n default -w
```

**Direct webhook (instant):**
```bash
POD=$(kubectl get pods -n default -l app=demo-app-crashing \
  -o jsonpath='{.items[0].metadata.name}')

curl -s http://localhost:8091/api/investigate \
  -H 'Content-Type: application/json' \
  -d "{\"alerts\":[{
    \"status\":\"firing\",
    \"labels\":{
      \"alertname\":\"KubePodCrashLooping\",
      \"severity\":\"critical\",
      \"namespace\":\"default\",
      \"pod\":\"$POD\",
      \"reason\":\"OOMKilled\"
    },
    \"annotations\":{\"summary\":\"Pod OOMKilled\"}
  }]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.loads(d.get('response', '{}')).get('response', d))
"
```

**Verify the fix:**
```bash
kubectl get deployment demo-app-crashing -n default \
  -o jsonpath='Memory limit after fix: {.spec.template.spec.containers[0].resources.limits.memory}{"\n"}'
kubectl get pods -n default -l app=demo-app-crashing
```

Expected: memory limit updated to `256Mi`, pod status `Running`.

---

### Scenario 2 — CrashLoopBackOff

Deploys a pod that exits immediately (no-op container). AgentCore triggers a rollout restart.

**Automated — via Prometheus (~60–90s):**
```bash
# test2.py applies app/test-crashloop.yaml and prints watch commands
python3 test2.py
kubectl get pods -n default -w
```

**Direct webhook (instant):**
```bash
POD=$(kubectl get pods -n default -l app=demo-app-crashloop \
  -o jsonpath='{.items[0].metadata.name}')

curl -s http://localhost:8091/api/investigate \
  -H 'Content-Type: application/json' \
  -d "{\"alerts\":[{
    \"status\":\"firing\",
    \"labels\":{
      \"alertname\":\"KubePodCrashLooping\",
      \"severity\":\"critical\",
      \"namespace\":\"default\",
      \"pod\":\"$POD\",
      \"reason\":\"CrashLoopBackOff\"
    },
    \"annotations\":{\"summary\":\"Pod CrashLoopBackOff\"}
  }]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.loads(d.get('response', '{}')).get('response', d))
"
```

**Watch agent logs:**
```bash
kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f
```

Expected: agent runs `kubectl rollout restart`, pod recovers to `Running`.

---

### Scenario 3 — ImagePullBackOff (JIRA + Slack escalation)

Simulates a pod stuck on a bad image. AgentCore cannot self-heal this — it creates a JIRA ticket and sends a Slack notification.

**Automated — via Prometheus (~60–90s):**
```bash
kubectl apply -f app/test-imagepull.yaml
kubectl get pods -n default -w
```

**Direct webhook — same as `test3.py`:**
```bash
python3 test3.py
```

Or manually with curl:
```bash
curl -s http://localhost:8091/api/investigate \
  -H 'Content-Type: application/json' \
  -d '{
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "KubePodNotReady",
        "severity": "critical",
        "namespace": "default",
        "pod": "payments-service-abc123",
        "reason": "ImagePullBackOff"
      },
      "annotations": {
        "summary": "Pod cannot pull image",
        "description": "bad-registry/payments:nonexistent"
      }
    }]
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.loads(d.get('response', '{}')).get('response', d))
"
```

**Watch escalation:**
```bash
kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f
```

Expected: agent creates a JIRA ticket and sends a Slack notification (~30s).

---

### Scenario 4 — Node NotReady / MemoryPressure (JIRA + Slack escalation)

Simulates a node under MemoryPressure. AgentCore cordons the node and escalates to JIRA and Slack.

**Direct webhook — same as `test4.py`:**
```bash
python3 test4.py
```

Or manually with curl:
```bash
NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')

curl -s http://localhost:8091/api/investigate \
  -H 'Content-Type: application/json' \
  -d "{\"alerts\":[{
    \"status\":\"firing\",
    \"labels\":{
      \"alertname\":\"KubeNodeNotReady\",
      \"severity\":\"critical\",
      \"node\":\"$NODE\",
      \"reason\":\"NodeNotReady\"
    },
    \"annotations\":{
      \"summary\":\"Node $NODE is NotReady\",
      \"description\":\"Node $NODE has MemoryPressure — risk of pod evictions and OOMKill.\"
    }
  }]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.loads(d.get('response', '{}')).get('response', d))
"
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
# bedrock-agentcore-eks-monitor
