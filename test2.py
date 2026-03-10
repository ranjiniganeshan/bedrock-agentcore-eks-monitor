#!/usr/bin/env python3
"""
Scenario 2 — CrashLoopBackOff (automated).
Deploys a pod that exits immediately, triggering CrashLoopBackOff.
Prometheus detects it → Alertmanager fires webhook → AgentCore restarts the deployment.
"""
import subprocess

subprocess.run([
    'kubectl', 'apply', '-f',
    '/Users/ranjiniganeshan/bedrock-agentcore-eks-monitor/app/test-crashloop.yaml'
])
print("\nDeployed. Alertmanager will auto-trigger AgentCore in ~60s.")
print("Watch pods:  kubectl get pods -n default -w")
print("Watch agent: kubectl logs -n alertmanager-agent deployment/alertmanager-webhook-server -f")
