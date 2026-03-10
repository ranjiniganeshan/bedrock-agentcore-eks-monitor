#!/usr/bin/env python3
"""
Scenario 4 — Node NotReady due to MemoryPressure.
Agent will: get node status → cordon node → create JIRA ticket + Slack notification.
"""
import subprocess, json

# Get first node name from the cluster
node = subprocess.check_output(
    ['kubectl', 'get', 'nodes', '-o', 'jsonpath={.items[0].metadata.name}']
).decode().strip()
print(f"Node: {node}")

payload = json.dumps({"alerts": [{
    "status": "firing",
    "labels": {
        "alertname": "KubeNodeNotReady",
        "severity": "critical",
        "node": node,
        "reason": "NodeNotReady"
    },
    "annotations": {
        "summary": f"Node {node} is NotReady",
        "description": f"Node {node} has MemoryPressure — risk of pod evictions and OOMKill."
    }
}]})

result = subprocess.check_output([
    'curl', '-s', 'http://localhost:8091/api/investigate',
    '-H', 'Content-Type: application/json',
    '-d', payload
])
data = json.loads(result)
inner = json.loads(data.get('response', '{}'))
print(inner.get('response', data))
