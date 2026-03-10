"""
Escalation tools for the agent: JIRA ticket creation and Slack notifications.

Used when the agent cannot auto-fix (e.g. missing secret/env, unknown cause).
Credentials from environment; if not set, tools return a clear message so the
agent can report that escalation was attempted but not configured.
"""

import json
import logging
import os
from typing import Any

import requests
from strands import tool

logger = logging.getLogger(__name__)


def _jira_configured() -> bool:
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    project = os.environ.get("JIRA_PROJECT_KEY", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    return bool(base and project and email and token)


def _slack_configured() -> bool:
    return bool(os.environ.get("SLACK_WEBHOOK_URL", "").strip())


@tool
def create_jira_ticket(
    title: str,
    description: str,
    severity: str = "High",
) -> str:
    """Create a JIRA ticket for human follow-up when the agent cannot auto-fix (e.g. missing Secret/env, unknown error). Pass a short title, full description (namespace, pod, logs snippet, root cause), and optional severity (High, Medium, Low). Returns the ticket key (e.g. PROJ-123) or an error message if JIRA is not configured."""
    if not _jira_configured():
        return json.dumps({
            "error": "JIRA not configured. Set JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_EMAIL, JIRA_API_TOKEN in environment.",
            "would_create": {"title": title, "description": description[:500], "severity": severity},
        })
    base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    project = os.environ.get("JIRA_PROJECT_KEY", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    url = f"{base}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": title[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": "Task"},
        }
    }
    if severity and severity.lower() in ("high", "medium", "low", "highest", "lowest", "critical"):
        name = severity.capitalize()
        if severity.lower() == "critical":
            name = "Highest"
        payload["fields"]["priority"] = {"name": name}
    auth = (email, token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, auth=auth, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        key = data.get("key", "")
        return json.dumps({"ok": True, "key": key, "message": f"Created JIRA ticket {key}"})
    except requests.RequestException as e:
        msg = str(e)
        if hasattr(e, "response") and e.response is not None and e.response.text:
            msg = f"{e.response.status_code}: {e.response.text[:300]}"
        logger.warning("create_jira_ticket failed: %s", msg)
        return json.dumps({"error": f"JIRA request failed: {msg}"})
    except Exception as e:
        logger.exception("create_jira_ticket")
        return json.dumps({"error": str(e)})


@tool
def send_slack_notification(
    message: str,
    channel: str = "",
    severity: str = "High",
) -> str:
    """Send a Slack notification when the agent escalates (cannot auto-fix). Pass the message (alert summary, namespace, pod, and optionally JIRA ticket key). channel is optional if webhook already targets a channel; severity can be included in the message. Requires SLACK_WEBHOOK_URL in environment."""
    if not _slack_configured():
        return json.dumps({
            "error": "Slack not configured. Set SLACK_WEBHOOK_URL in environment.",
            "would_send": message[:500],
        })
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    payload: dict[str, Any] = {"text": message}
    if channel and channel.strip():
        payload["channel"] = channel.strip()
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            return json.dumps({"error": f"Slack webhook returned {resp.status_code}: {resp.text[:200]}"})
        return json.dumps({"ok": True, "message": "Slack notification sent"})
    except requests.RequestException as e:
        logger.warning("send_slack_notification failed: %s", e)
        return json.dumps({"error": f"Slack request failed: {str(e)}"})
    except Exception as e:
        logger.exception("send_slack_notification")
        return json.dumps({"error": str(e)})
