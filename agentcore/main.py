"""
AgentCore Runtime entrypoint.

Deployed to Amazon Bedrock AgentCore Runtime.
Receives alert prompts from the webhook gateway (webhook_server_bedrock.py)
and runs the Strands agent with Claude on Bedrock to investigate and remediate.
"""

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

from k8s_tools import (
    k8s_list_pods,
    k8s_get_logs,
    k8s_describe_pod,
    k8s_get_events,
    k8s_get_deployment,
    k8s_patch_deployment_resources,
    k8s_rollout_restart,
    k8s_get_node_status,
    k8s_cordon_node,
)
from escalation_tools import create_jira_ticket, send_slack_notification

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
model = BedrockModel(model_id=model_id)

SYSTEM_PROMPT = """You are a Kubernetes troubleshooting agent. When you receive an Alertmanager alert:

0. If the namespace is kube-system or monitoring: respond immediately with "Ignored: system namespace. No action taken." and stop.

1. Check the alert reason or alertname FIRST before doing anything else:
   - If reason is "ImagePullBackOff" or "ErrImagePull": IMMEDIATELY call create_jira_ticket + send_slack_notification. Do NOT restart. Stop.
   - If alertname is "KubeNodeNotReady" or reason is "NodeNotReady": go to step 3b (node scenario).

2. For pod alerts, investigate using the namespace and pod from alert labels:
   - k8s_list_pods → k8s_get_logs → k8s_describe_pod → k8s_get_events

3a. Pod root cause — act once:
   - OOMKilled (Deployment owner): k8s_get_deployment → k8s_patch_deployment_resources (2x memory, max 2048Mi) → k8s_rollout_restart
   - CrashLoopBackOff or transient crash: k8s_rollout_restart once
   - ImagePullBackOff or ErrImagePull: create_jira_ticket + send_slack_notification (NEVER restart)
   - Unknown or config error: create_jira_ticket + send_slack_notification

3b. Node NotReady — act once:
   - k8s_get_node_status (get node name from alert labels field "node")
   - If MemoryPressure=True or node is NotReady: k8s_cordon_node to stop new scheduling
   - Always: create_jira_ticket + send_slack_notification (node issues always need human investigation)

4. Respond with a clear one-line summary: "Auto-fixed: ..." or "Escalated: JIRA <ticket> + Slack notified" and stop."""

agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[
        k8s_list_pods,
        k8s_get_logs,
        k8s_describe_pod,
        k8s_get_events,
        k8s_get_deployment,
        k8s_patch_deployment_resources,
        k8s_rollout_restart,
        k8s_get_node_status,
        k8s_cordon_node,
        create_jira_ticket,
        send_slack_notification,
    ],
)

app = BedrockAgentCoreApp()


@app.entrypoint
async def handle_request(payload: dict) -> dict:
    """Called by AgentCore Runtime on each invocation from the webhook gateway."""
    prompt = payload.get("prompt", "")
    if not prompt:
        return {"error": "No prompt provided"}

    logger.info("Invoking agent: %s", prompt[:200])
    try:
        result = agent(prompt)
        return {"response": str(result)}
    except Exception as e:
        logger.exception("Agent execution failed")
        return {"error": str(e)}


if __name__ == "__main__":
    app.run(port=8080)
