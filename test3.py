#!/usr/bin/env python3
import subprocess, json

payload = json.dumps({"alerts":[{
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
}]})

result = subprocess.check_output([
    'curl','-s','http://localhost:8091/api/investigate',
    '-H','Content-Type: application/json',
    '-d', payload
])
data = json.loads(result)
inner = json.loads(data.get('response', '{}'))
print(inner.get('response', data))
