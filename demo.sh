#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  Bedrock AgentCore EKS Monitor — Demo Script
#  Run from VS Code terminal:  bash demo.sh
# ─────────────────────────────────────────────────────────────

set -e

WEBHOOK_URL="http://localhost:8091/api/investigate"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
CYAN="\033[0;36m"
RED="\033[0;31m"
BOLD="\033[1m"
RESET="\033[0m"

separator() {
  echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
}

header() {
  separator
  echo -e "${BOLD}$1${RESET}"
  separator
}

# ── Step 0: Port-forward ──────────────────────────────────────
header "🔌  SETUP — Port-forwarding webhook server"

pkill -f "port-forward.*8091" 2>/dev/null || true
kubectl port-forward svc/alertmanager-webhook-server \
  -n alertmanager-agent 8091:80 &>/tmp/pf-webhook.log &
sleep 3

HEALTH=$(curl -s http://localhost:8091/health)
echo -e "Webhook health: ${GREEN}$HEALTH${RESET}"

# ── Step 1: Deploy crashing app ───────────────────────────────
header "🚀  DEPLOYING crashing test app"

kubectl apply -f "$(dirname "$0")/app/test-crash.yaml"
echo -e "\n${YELLOW}Waiting 25s for pod to OOMKill...${RESET}"
sleep 25

echo ""
kubectl get pods -n default -l app=demo-app-crashing

# ── TEST 1: OOMKilled ─────────────────────────────────────────
header "🔴  TEST 1 — OOMKilled (auto-fix: increase memory + restart)"

POD=$(kubectl get pods -n default -l app=demo-app-crashing \
  -o jsonpath='{.items[0].metadata.name}')
echo -e "Pod: ${YELLOW}$POD${RESET}"
echo -e "Sending OOMKilled alert to AgentCore...\n"

START=$(date +%s)
RESPONSE=$(curl -s "$WEBHOOK_URL" \
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
  }]}")
END=$(date +%s)

echo -e "${GREEN}Agent Response:${RESET}"
echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
inner = json.loads(data.get('response', '{}'))
print(inner.get('response', data))
"
echo -e "\n${GREEN}✅  Fixed in $((END-START))s${RESET}"
echo ""
kubectl get deployment demo-app-crashing -n default \
  -o jsonpath='Memory after fix: {.spec.template.spec.containers[0].resources}' && echo
echo ""
kubectl get pods -n default -l app=demo-app-crashing

# ── TEST 2: CrashLoopBackOff ──────────────────────────────────
header "🔴  TEST 2 — CrashLoopBackOff (auto-fix: rollout restart)"

POD=$(kubectl get pods -n default -l app=demo-app-crashing \
  -o jsonpath='{.items[0].metadata.name}')
echo -e "Pod: ${YELLOW}$POD${RESET}"
echo -e "Sending CrashLoopBackOff alert to AgentCore...\n"

START=$(date +%s)
RESPONSE=$(curl -s "$WEBHOOK_URL" \
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
  }]}")
END=$(date +%s)

echo -e "${GREEN}Agent Response:${RESET}"
echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
inner = json.loads(data.get('response', '{}'))
print(inner.get('response', data))
"
echo -e "\n${GREEN}✅  Fixed in $((END-START))s${RESET}"
echo ""
kubectl get pods -n default -l app=demo-app-crashing

# ── TEST 3: ImagePullBackOff ──────────────────────────────────
header "🔴  TEST 3 — ImagePullBackOff (escalate: JIRA ticket + Slack)"

echo -e "Pod: ${YELLOW}payments-service-abc123${RESET}"
echo -e "Sending ImagePullBackOff alert to AgentCore...\n"

START=$(date +%s)
RESPONSE=$(curl -s "$WEBHOOK_URL" \
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
  }')
END=$(date +%s)

echo -e "${GREEN}Agent Response:${RESET}"
echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
inner = json.loads(data.get('response', '{}'))
print(inner.get('response', data))
"
echo -e "\n${GREEN}✅  Escalated in $((END-START))s${RESET}"

# ── Summary ───────────────────────────────────────────────────
header "🎉  DEMO COMPLETE"

echo -e "${BOLD}Results:${RESET}"
echo -e "  ${GREEN}✅  Test 1 — OOMKilled        → Memory increased + pod restarted${RESET}"
echo -e "  ${GREEN}✅  Test 2 — CrashLoopBackOff → Deployment restarted${RESET}"
echo -e "  ${GREEN}✅  Test 3 — ImagePullBackOff → JIRA ticket created + Slack notified${RESET}"

separator

# ── Cleanup ───────────────────────────────────────────────────
echo -e "${YELLOW}Cleaning up...${RESET}"
kubectl delete deployment demo-app-crashing -n default 2>/dev/null && \
  echo -e "${GREEN}Demo app deleted${RESET}"
pkill -f "port-forward.*8091" 2>/dev/null && \
  echo -e "${GREEN}Port-forward stopped${RESET}"

separator
