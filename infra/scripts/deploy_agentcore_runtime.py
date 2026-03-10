#!/usr/bin/env python3
"""
Create or update a Bedrock AgentCore Runtime via boto3.

Called by Terraform null_resource.agentcore_runtime provisioner.
Reads config from environment variables, writes the runtime ID to OUTPUT_FILE.

Required env vars:
  AWS_REGION, RUNTIME_NAME, ROLE_ARN, S3_BUCKET, S3_KEY, OUTPUT_FILE

Optional env vars:
  MODEL_ID, EKS_CLUSTER
"""

import os
import pathlib
import sys
import time


def get_client(region: str):
    import boto3
    return boto3.client("bedrock-agentcore-control", region_name=region)


def list_runtimes(client) -> list:
    runtimes = []
    kwargs = {}
    while True:
        resp = client.list_agent_runtimes(**kwargs)
        runtimes.extend(resp.get("agentRuntimes", []))
        token = resp.get("nextToken")
        if not token:
            break
        kwargs["nextToken"] = token
    return runtimes


def build_artifact(container_image: str) -> dict:
    # Container-based deployment: all deps are pre-baked into the image.
    # Startup goes from ~3 minutes (S3 zip + pip install) → seconds.
    return {
        "containerConfiguration": {
            "containerUri": container_image,
        }
    }


def build_env_vars(region: str, model_id: str, eks_cluster: str) -> dict:
    env = {
        "AWS_REGION": region,
        "BEDROCK_MODEL_ID": model_id,
        "EKS_CLUSTER_NAME": eks_cluster,
        "K8S_MAX_MEMORY_LIMIT_MB": "2048",
    }
    # Optional escalation credentials — only added when set
    for key in ("JIRA_BASE_URL", "JIRA_PROJECT_KEY", "JIRA_EMAIL",
                "JIRA_API_TOKEN", "SLACK_WEBHOOK_URL"):
        val = os.environ.get(key, "")
        if val:
            env[key] = val
    return env


def main() -> None:
    region = os.environ["AWS_REGION"]
    runtime_name = os.environ["RUNTIME_NAME"]
    role_arn = os.environ["ROLE_ARN"]
    container_image = os.environ["CONTAINER_IMAGE"]
    model_id = os.environ.get("MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
    eks_cluster = os.environ.get("EKS_CLUSTER", "demo-cluster")
    output_file = os.environ["OUTPUT_FILE"]

    client = get_client(region)
    artifact = build_artifact(container_image)
    env_vars = build_env_vars(region, model_id, eks_cluster)

    # Check whether this runtime already exists
    existing_id = None
    try:
        for rt in list_runtimes(client):
            if rt.get("agentRuntimeName") == runtime_name:
                existing_id = rt["agentRuntimeId"]
                break
    except Exception as exc:
        print(f"[warn] Could not list runtimes: {exc}", file=sys.stderr)

    if existing_id:
        # Try to update; if artifact type changed, delete and recreate.
        try:
            print(f"[info] Updating AgentCore Runtime '{runtime_name}' ({existing_id}) ...")
            resp = client.update_agent_runtime(
                agentRuntimeId=existing_id,
                agentRuntimeArtifact=artifact,
                roleArn=role_arn,
                networkConfiguration={"networkMode": "PUBLIC"},
                environmentVariables=env_vars,
            )
            runtime_id = resp["agentRuntimeId"]
        except Exception as exc:
            if "artifact type cannot be updated" in str(exc).lower():
                print(f"[info] Artifact type changed — deleting old runtime {existing_id} and recreating ...")
                client.delete_agent_runtime(agentRuntimeId=existing_id)
                # Wait for deletion to complete (name reservation released)
                for _ in range(30):
                    remaining = [
                        rt for rt in list_runtimes(client)
                        if rt.get("agentRuntimeName") == runtime_name
                    ]
                    if not remaining:
                        break
                    print(f"[info] Waiting for deletion to complete ...")
                    time.sleep(10)
                existing_id = None
            else:
                raise

    if not existing_id:
        print(f"[info] Creating AgentCore Runtime '{runtime_name}' ...")
        resp = client.create_agent_runtime(
            agentRuntimeName=runtime_name,
            description=(
                "Kubernetes troubleshooting agent — auto-remediates "
                "OOMKilled and CrashLoopBackOff alerts via Strands + Claude"
            ),
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            environmentVariables=env_vars,
        )
        runtime_id = resp["agentRuntimeId"]

    print(f"[info] AgentCore Runtime ID: {runtime_id}")

    # Write the ID so Terraform can read it back with data.local_file
    out = pathlib.Path(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(runtime_id)
    print(f"[info] Runtime ID written to {output_file}")


if __name__ == "__main__":
    main()
