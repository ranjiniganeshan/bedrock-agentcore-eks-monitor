"""
Webhook Gateway — runs on EKS in the monitoring namespace.

Receives Alertmanager POST payloads, builds a prompt,
and invokes the Bedrock AgentCore Runtime agent to investigate.
"""

import asyncio
import json
import logging
import os
import uuid

import boto3
from fastapi import FastAPI, HTTPException, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alertmanager → AgentCore Bridge", version="1.0.0")

RUNTIME_ID = os.environ.get("BEDROCK_AGENT_RUNTIME_ID", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "020930354342")

RUNTIME_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{RUNTIME_ID}"

agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)


def _build_prompt(payload: dict) -> str:
    """Convert Alertmanager JSON payload into a text prompt for the agent."""
    status = payload.get("status", "unknown")
    alerts = payload.get("alerts", [])
    common_annotations = payload.get("commonAnnotations", {})

    lines = [
        "A Prometheus Alertmanager alert was received. Investigate and remediate.",
        f"Overall status: {status}",
        f"Common annotations: {json.dumps(common_annotations)}",
        "",
        "Alerts:",
    ]
    for i, a in enumerate(alerts, 1):
        lines.append(
            f"  [{i}] status={a.get('status')} "
            f"labels={a.get('labels')} "
            f"annotations={a.get('annotations')}"
        )
    return "\n".join(lines)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/investigate")
async def investigate(request: Request):
    """Receive Alertmanager webhook, forward to AgentCore Runtime agent."""
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    alerts = body.get("alerts", [])
    logger.info("Webhook received: status=%s alerts=%d", body.get("status"), len(alerts))

    # Skip system namespaces — these are Prometheus/k8s internals, not app issues
    for a in alerts:
        ns = (a.get("labels") or {}).get("namespace", "")
        if ns in ("kube-system", "monitoring"):
            logger.info("Ignored: system namespace %s", ns)
            return {"status": "ok", "response": f"Ignored {ns} namespace."}

    if not RUNTIME_ID:
        logger.error("BEDROCK_AGENT_RUNTIME_ID not set")
        return {"status": "error", "response": "AgentCore Runtime ID not configured."}

    prompt = _build_prompt(body)

    # Session ID scoped per alert fingerprint so agent has context continuity
    # Must be at least 33 characters per AgentCore API requirement
    fingerprint = alerts[0].get("fingerprint", "") if alerts else ""
    if fingerprint and len(f"alert-{fingerprint}") >= 33:
        session_id = f"alert-{fingerprint}"
    else:
        session_id = f"alert-{fingerprint}-{uuid.uuid4().hex}" if fingerprint else f"alert-{uuid.uuid4().hex}"

    try:
        logger.info("Invoking AgentCore Runtime %s session=%s", RUNTIME_ARN, session_id)

        # Run the blocking boto3 call in a thread so the event loop stays free
        # (keeps health check probes responsive during long agent runs)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=RUNTIME_ARN,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            ),
        )

        # Response is a streaming body — read all chunks
        result_body = await loop.run_in_executor(None, lambda: response["response"].read().decode("utf-8"))
        logger.info("Agent response: %s", result_body[:300])
        return {"status": "ok", "session_id": session_id, "response": result_body}

    except Exception as e:
        logger.exception("AgentCore invocation failed")
        # Return 200 so Alertmanager does not retry
        return {"status": "agent_failed", "response": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
