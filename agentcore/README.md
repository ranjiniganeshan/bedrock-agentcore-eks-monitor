# Bedrock AgentCore Runtime Agent

This directory holds the **agent code** intended to run on **Amazon Bedrock AgentCore Runtime**. The same troubleshooting logic (K8s tools, escalation, auto-fix for OOM/CrashLoopBackOff) from `local-agent-test/` is adapted here for direct code deployment to AgentCore.

## Purpose

- **Local development**: Use `local-agent-test/` with the Strands agent + webhook server against Docker Desktop K8s.
- **Production (EKS)**: Deploy this agent to Bedrock AgentCore Runtime; the **webhook server** (in `webhook-server/`) runs on EKS and forwards Alertmanager payloads to the agent via the AgentCore API.

## Bedrock AgentCore Direct Code Deployment

AgentCore expects:

1. **Entrypoint**: A Python module with either:
   - `@app.entrypoint` from the [Bedrock AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python), or
   - HTTP endpoints: `POST /invocations` and `GET /ping`.

2. **Dependencies**: Package agent code + dependencies (e.g. `bedrock-agentcore`, `strands-agents`, K8s client) into a `.zip` for deployment.

3. **Tools**: The same tools as in `local-agent-test/` (list pods, get logs, describe pod, events, patch deployment, rollout restart, JIRA, Slack) are registered with the agent so it can investigate and remediate alerts.

## Setup (from AWS docs)

1. **Prerequisites**: AWS credentials, [uv](https://docs.astral.sh/uv/getting-started/installation/), Python 3.10+.

2. **Scaffold** (optional, if starting fresh):
   ```bash
   uv init agentcore_runtime_direct_deploy --python 3.13
   cd agentcore_runtime_direct_deploy
   uv add bedrock-agentcore strands-agents
   uv add --dev bedrock-agentcore-starter-toolkit
   agentcore create   # choose Strands Agents, project name, template
   ```

3. **Migrate from local-agent-test**: Copy or adapt:
   - Agent system prompt and tool list from `local-agent-test/agent.py`.
   - Tool implementations from `local-agent-test/k8s_tools.py` and `local-agent-test/escalation_tools.py`.
   - Wire the entrypoint to your Strands agent and expose `/invocations` and `/ping` per [AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html).

4. **Test locally**:
   ```bash
   agentcore dev
   curl -X POST http://localhost:8080/invocations -H "Content-Type: application/json" -d '{"prompt": "..."}'
   ```

5. **Deploy**: Use the starter toolkit (`agentcore launch`) or custom zip + boto3 to deploy to AgentCore Runtime. The webhook server (in `webhook-server/`) will call the runtime with the same prompt shape built from Alertmanager webhooks.

## References

- [Get started with AgentCore Runtime direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html)
- [AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)
- [Strands Agents](https://strandsagents.com/latest/) (used in `local-agent-test/`)
