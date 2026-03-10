"""
Kubernetes tools for the AgentCore agent.

Auth priority:
  1. In-cluster ServiceAccount (webhook pod running inside EKS)
  2. Local kubeconfig (local development)
  3. EKS IAM token (AgentCore Runtime running in AWS, outside the cluster)
     Requires: EKS_CLUSTER_NAME and AWS_REGION env vars.
     The AgentCore execution role must be in the EKS aws-auth ConfigMap.
"""

import base64
import datetime
import json
import logging
import os
import re
import tempfile
from typing import Any

import boto3
from botocore.signers import RequestSigner
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from strands import tool

logger = logging.getLogger(__name__)


def _get_eks_bearer_token(cluster_name: str, region: str) -> str:
    """Generate an EKS bearer token via STS presigned URL (same mechanism as aws-iam-authenticator)."""
    session = boto3.Session()
    sts_client = session.client("sts", region_name=region)
    service_id = sts_client.meta.service_model.service_id

    signer = RequestSigner(
        service_id, region, "sts", "v4",
        session.get_credentials(), session.events,
    )
    params = {
        "method": "GET",
        "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        "body": {},
        "headers": {"x-k8s-aws-id": cluster_name},
        "context": {},
    }
    signed_url = signer.generate_presigned_url(
        params, region_name=region, expires_in=60, operation_name=""
    )
    b64 = base64.urlsafe_b64encode(signed_url.encode("utf-8")).decode("utf-8")
    return "k8s-aws-v1." + re.sub(r"=*", "", b64)


def _load_config() -> None:
    """Load K8s config: in-cluster → kubeconfig → EKS IAM token."""
    # 1. In-cluster (when this code runs inside the EKS cluster)
    try:
        config.load_incluster_config()
        return
    except config.ConfigException:
        pass

    # 2. Local kubeconfig (local development)
    try:
        config.load_kube_config()
        return
    except config.ConfigException:
        pass

    # 3. EKS IAM token (AgentCore Runtime running in AWS outside the cluster)
    cluster_name = os.environ.get("EKS_CLUSTER_NAME", "demo-cluster")
    region = os.environ.get("AWS_REGION", "us-east-1")

    eks = boto3.client("eks", region_name=region)
    cluster_info = eks.describe_cluster(name=cluster_name)["cluster"]
    endpoint = cluster_info["endpoint"]
    ca_data = cluster_info["certificateAuthority"]["data"]

    token = _get_eks_bearer_token(cluster_name, region)

    ca_bytes = base64.b64decode(ca_data)
    ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    ca_file.write(ca_bytes)
    ca_file.flush()

    cfg = client.Configuration()
    cfg.host = endpoint
    cfg.ssl_ca_cert = ca_file.name
    cfg.api_key = {"authorization": f"Bearer {token}"}
    client.Configuration.set_default(cfg)
    logger.info("K8s configured via EKS IAM token for cluster: %s", cluster_name)


def _get_core_v1() -> client.CoreV1Api:
    """Build CoreV1Api using local kubeconfig (e.g. Docker Desktop)."""
    _load_config()
    return client.CoreV1Api()


def _get_apps_v1() -> client.AppsV1Api:
    """Build AppsV1Api for Deployment/StatefulSet operations."""
    _load_config()
    return client.AppsV1Api()


# Strategic-merge PATCH: Kubernetes Python client does not accept _content_type;
# use call_api with Content-Type set so PATCH uses application/strategic-merge-patch+json.
def _patch_deployment_strategic(namespace: str, name: str, body: dict[str, Any]) -> None:
    api = _get_apps_v1()
    path = "/apis/apps/v1/namespaces/{namespace}/deployments/{name}"
    header_params = {
        "Accept": "application/json",
        "Content-Type": "application/strategic-merge-patch+json",
    }
    api.api_client.call_api(
        path,
        "PATCH",
        path_params={"namespace": namespace, "name": name},
        query_params=[],
        header_params=header_params,
        body=body,
        response_type="V1Deployment",
        auth_settings=["BearerToken"],
    )


def _patch_stateful_set_strategic(namespace: str, name: str, body: dict[str, Any]) -> None:
    api = _get_apps_v1()
    path = "/apis/apps/v1/namespaces/{namespace}/statefulsets/{name}"
    header_params = {
        "Accept": "application/json",
        "Content-Type": "application/strategic-merge-patch+json",
    }
    api.api_client.call_api(
        path,
        "PATCH",
        path_params={"namespace": namespace, "name": name},
        query_params=[],
        header_params=header_params,
        body=body,
        response_type="V1StatefulSet",
        auth_settings=["BearerToken"],
    )


def _patch_daemon_set_strategic(namespace: str, name: str, body: dict[str, Any]) -> None:
    api = _get_apps_v1()
    path = "/apis/apps/v1/namespaces/{namespace}/daemonsets/{name}"
    header_params = {
        "Accept": "application/json",
        "Content-Type": "application/strategic-merge-patch+json",
    }
    api.api_client.call_api(
        path,
        "PATCH",
        path_params={"namespace": namespace, "name": name},
        query_params=[],
        header_params=header_params,
        body=body,
        response_type="V1DaemonSet",
        auth_settings=["BearerToken"],
    )


def _safe_namespace(ns: str) -> str:
    if not ns or "/" in ns or ".." in ns:
        raise ValueError("Invalid namespace")
    return ns


def _safe_name(name: str) -> str:
    if not name or "/" in name or ".." in name:
        raise ValueError("Invalid name")
    return name


@tool
def k8s_list_pods(namespace: str = "default", label_selector: str = "") -> str:
    """List pods in a Kubernetes namespace. Default namespace is 'default'. Use label_selector to filter (e.g. app=myapp)."""
    try:
        _safe_namespace(namespace)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        api = _get_core_v1()
        resp = api.list_namespaced_pod(namespace=namespace, label_selector=label_selector or None)
        items = []
        for p in resp.items:
            items.append({
                "name": p.metadata.name,
                "phase": p.status.phase,
                "reason": getattr(p.status, "reason") or "",
                "message": getattr(p.status, "message") or "",
                "restart_count": sum(c.restart_count for c in (p.status.container_statuses or [])),
            })
        return json.dumps({"pods": items}, indent=2)
    except ApiException as e:
        logger.warning("list_pods ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("list_pods")
        return json.dumps({"error": str(e)})


@tool
def k8s_get_logs(namespace: str, pod_name: str, tail_lines: int = 100) -> str:
    """Get recent logs for a pod. Give namespace and pod_name. Use tail_lines to limit lines (max 500)."""
    try:
        _safe_namespace(namespace)
        _safe_name(pod_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    if tail_lines > 500:
        tail_lines = 500
    try:
        api = _get_core_v1()
        resp = api.read_namespaced_pod_log(
            namespace=namespace,
            name=pod_name,
            tail_lines=tail_lines,
        )
        return resp if isinstance(resp, str) else json.dumps(resp)
    except ApiException as e:
        logger.warning("get_logs ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("get_logs")
        return json.dumps({"error": str(e)})


@tool
def k8s_describe_pod(namespace: str, pod_name: str) -> str:
    """Get pod description: phase, reason, message, container statuses. Give namespace and pod_name."""
    try:
        _safe_namespace(namespace)
        _safe_name(pod_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        api = _get_core_v1()
        pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)
        status = pod.status
        owner_refs = []
        for ref in (pod.metadata.owner_references or []):
            owner_refs.append({"kind": ref.kind, "name": ref.name})
        out = {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": status.phase,
            "reason": getattr(status, "reason") or "",
            "message": getattr(status, "message") or "",
            "owner_references": owner_refs,
            "container_statuses": [
                {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count}
                for cs in (status.container_statuses or [])
            ],
        }
        return json.dumps(out, indent=2)
    except ApiException as e:
        logger.warning("describe_pod ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("describe_pod")
        return json.dumps({"error": str(e)})


@tool
def k8s_get_events(namespace: str) -> str:
    """Get recent events in a namespace (e.g. Failed, BackOff, OOMKilled). Use this to diagnose CrashLoopBackOff."""
    try:
        _safe_namespace(namespace)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        api = _get_core_v1()
        resp = api.list_namespaced_event(namespace=namespace, limit=50)
        events = []
        for e in resp.items:
            events.append({
                "reason": e.reason,
                "type": e.type,
                "message": e.message,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}" if e.involved_object else "",
                "last_timestamp": str(e.last_timestamp) if e.last_timestamp else "",
            })
        return json.dumps({"events": events}, indent=2)
    except ApiException as e:
        logger.warning("get_events ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("get_events")
        return json.dumps({"error": str(e)})


def _get_max_memory_mb() -> int:
    """Max memory limit (MB) for auto-fix; avoid setting unsafe limits."""
    val = os.environ.get("K8S_MAX_MEMORY_LIMIT_MB", "2048")
    try:
        return max(64, min(8192, int(val)))
    except ValueError:
        return 2048


@tool
def k8s_get_deployment(namespace: str, deployment_name: str) -> str:
    """Get a Deployment spec: containers, image, resources (limits/requests). Use the deployment name from the pod's owner_references (describe_pod) or infer from pod name (e.g. pod 'demo-abc123' -> deployment 'demo')."""
    try:
        _safe_namespace(namespace)
        _safe_name(deployment_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        api = _get_apps_v1()
        dep = api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        containers = []
        for c in dep.spec.template.spec.containers or []:
            limits = {}
            requests = {}
            if c.resources and c.resources.limits:
                limits = {k: str(v) for k, v in c.resources.limits.items()}
            if c.resources and c.resources.requests:
                requests = {k: str(v) for k, v in c.resources.requests.items()}
            containers.append({
                "name": c.name,
                "image": c.image,
                "resources_limits": limits,
                "resources_requests": requests,
            })
        out = {
            "name": dep.metadata.name,
            "namespace": dep.metadata.namespace,
            "containers": containers,
        }
        return json.dumps(out, indent=2)
    except ApiException as e:
        logger.warning("get_deployment ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("get_deployment")
        return json.dumps({"error": str(e)})


@tool
def k8s_patch_deployment_resources(
    namespace: str,
    deployment_name: str,
    container_name: str,
    memory_limit: str,
    memory_request: str = "",
) -> str:
    """Patch a Deployment's container memory limit and optionally request. Use for OOMKilled: set memory_limit to max(2 × current limit, 256Mi) in Mi (e.g. '256Mi', '512Mi'). memory_request is optional; if empty, set to same as memory_limit. Values are capped at K8S_MAX_MEMORY_LIMIT_MB (default 2048MB)."""
    try:
        _safe_namespace(namespace)
        _safe_name(deployment_name)
        _safe_name(container_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    max_mb = _get_max_memory_mb()
    if not memory_limit or not isinstance(memory_limit, str):
        return json.dumps({"error": "memory_limit must be a string (e.g. '256Mi')"})
    mem = memory_limit.strip()
    if not mem.endswith("Mi") and not mem.endswith("Gi"):
        return json.dumps({"error": "memory_limit must end with Mi or Gi (e.g. '512Mi')"})
    try:
        num = int(mem[:-2])
        if mem.endswith("Gi"):
            num_mb = num * 1024
        else:
            num_mb = num
        if num_mb > max_mb:
            num_mb = max_mb
            mem = f"{num_mb}Mi"
    except ValueError:
        return json.dumps({"error": f"Invalid memory format: {mem}"})
    req = (memory_request or "").strip() or mem
    try:
        api = _get_apps_v1()
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": {
                                    "limits": {"memory": mem},
                                    "requests": {"memory": req},
                                },
                            }
                        ]
                    }
                }
            }
        }
        _patch_deployment_strategic(namespace, deployment_name, patch)
        return json.dumps({"ok": True, "message": f"Patched {container_name}: limits.memory={mem}, requests.memory={req}"})
    except ApiException as e:
        logger.warning("patch_deployment_resources ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("patch_deployment_resources")
        return json.dumps({"error": str(e)})


@tool
def k8s_get_node_status(node_name: str = "") -> str:
    """Get node status and conditions (Ready, MemoryPressure, DiskPressure, PIDPressure). If node_name is empty, returns all nodes."""
    try:
        api = _get_core_v1()
        if node_name:
            _safe_name(node_name)
            nodes = [api.read_node(name=node_name)]
        else:
            nodes = api.list_node().items

        result = []
        for node in nodes:
            conditions = {}
            for c in (node.status.conditions or []):
                conditions[c.type] = {"status": c.status, "reason": c.reason or "", "message": c.message or ""}
            allocatable = {k: str(v) for k, v in (node.status.allocatable or {}).items()}
            capacity = {k: str(v) for k, v in (node.status.capacity or {}).items()}
            result.append({
                "name": node.metadata.name,
                "conditions": conditions,
                "allocatable": allocatable,
                "capacity": capacity,
                "unschedulable": node.spec.unschedulable or False,
            })
        return json.dumps({"nodes": result}, indent=2)
    except ApiException as e:
        logger.warning("get_node_status ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("get_node_status")
        return json.dumps({"error": str(e)})


@tool
def k8s_cordon_node(node_name: str) -> str:
    """Cordon a node to prevent new pods from being scheduled on it. Use when a node has MemoryPressure or is NotReady. Does NOT evict existing pods."""
    try:
        _safe_name(node_name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        api = _get_core_v1()
        body = {"spec": {"unschedulable": True}}
        api.patch_node(name=node_name, body=body)
        return json.dumps({"ok": True, "message": f"Node {node_name} cordoned — no new pods will be scheduled on it"})
    except ApiException as e:
        logger.warning("cordon_node ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("cordon_node")
        return json.dumps({"error": str(e)})


@tool
def k8s_rollout_restart(namespace: str, kind: str, name: str) -> str:
    """Restart a workload (rollout restart). kind must be 'deployment', 'statefulset', or 'daemonset'; name is the workload name. Use after patching resources or to clear transient failures. Call this at most once per workload per alert—do not retry if it succeeds or returns an error."""
    try:
        _safe_namespace(namespace)
        _safe_name(name)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    k = (kind or "").strip().lower()
    if k not in ("deployment", "statefulset", "daemonset"):
        return json.dumps({"error": "kind must be 'deployment', 'statefulset', or 'daemonset'"})
    try:
        restarted_at = datetime.datetime.utcnow().isoformat() + "Z"
        body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": restarted_at}}}}}
        if k == "deployment":
            _patch_deployment_strategic(namespace, name, body)
        elif k == "statefulset":
            _patch_stateful_set_strategic(namespace, name, body)
        else:
            _patch_daemon_set_strategic(namespace, name, body)
        return json.dumps({"ok": True, "message": f"Rollout restart triggered for {k}/{name}"})
    except ApiException as e:
        logger.warning("rollout_restart ApiException: %s", e)
        return json.dumps({"error": e.reason or str(e)})
    except Exception as e:
        logger.exception("rollout_restart")
        return json.dumps({"error": str(e)})
