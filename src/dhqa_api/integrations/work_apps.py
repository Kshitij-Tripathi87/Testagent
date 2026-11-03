"""Narrow, workspace-scoped adapters for the first Testagent work-app connectors."""
from __future__ import annotations
import re
from typing import Any
from urllib.parse import urlsplit
import httpx

class IntegrationError(RuntimeError):
    """A connector request failed or returned an unusable response."""

def validate_integration_config(provider: str, config: dict[str, str]) -> dict[str, str]:
    clean = {str(key): str(value).strip() for key, value in config.items()}
    if provider == "github":
        repository = clean.get("repository", "")
        parts = repository.split("/")
        if (len(parts) != 2 or any(part in {"", ".", ".."} for part in parts)
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)):
            raise ValueError("GitHub repository must use owner/repository format")
        return {"repository": repository}
    if provider == "slack":
        channel_id = clean.get("channel_id", "")
        if not re.fullmatch(r"[A-Z0-9]{2,40}", channel_id):
            raise ValueError("Slack channel must be a channel ID such as C0123456789")
        return {"channel_id": channel_id}
    if provider == "jira":
        base_url = clean.get("base_url", "").rstrip("/")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.lower().endswith(".atlassian.net")
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Jira URL must be an HTTPS Atlassian Cloud site URL")
        project_key = clean.get("project_key", "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", project_key):
            raise ValueError("Jira project key must be 2-20 letters, numbers or underscores")
        issue_type = clean.get("issue_type", "Task") or "Task"
        if not re.fullmatch(r"[A-Za-z][A-Za-z -]{0,39}", issue_type):
            raise ValueError("Jira issue type contains unsupported characters")
        email = clean.get("email", "")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Jira account email is required")
        return {"base_url": base_url, "project_key": project_key, "issue_type": issue_type, "email": email}
    raise ValueError("Supported integrations are github, slack and jira")

def validate_integration_credentials(provider: str, credentials: dict[str, str]) -> dict[str, str]:
    clean = {str(key): str(value).strip() for key, value in credentials.items()}
    required = {"github": ("access_token",), "slack": ("bot_token",), "jira": ("api_token",)}.get(provider)
    if not required:
        raise ValueError("Unsupported integration provider")
    missing = [key for key in required if not clean.get(key)]
    if missing:
        raise ValueError("Missing required credential: " + ", ".join(missing))
    return {key: clean[key] for key in required}

def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False)

def _raise_response(response: httpx.Response, provider: str) -> None:
    if response.is_error:
        excerpt = response.text[:500].replace("\n", " ")
        raise IntegrationError(f"{provider} returned HTTP {response.status_code}: {excerpt}")

def test_integration(provider: str, config: dict[str, str], credentials: dict[str, str]) -> dict[str, Any]:
    with _client() as client:
        if provider == "github":
            response = client.get(
                "https://api.github.com/repos/" + config["repository"],
                headers={"Accept": "application/vnd.github+json",
                         "Authorization": "Bearer " + credentials["access_token"],
                         "X-GitHub-Api-Version": "2022-11-28"},
            )
            _raise_response(response, "GitHub")
            payload = response.json()
            return {"account": payload.get("full_name"), "default_branch": payload.get("default_branch")}
        if provider == "slack":
            response = client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": "Bearer " + credentials["bot_token"]},
            )
            _raise_response(response, "Slack")
            payload = response.json()
            if not payload.get("ok"):
                raise IntegrationError("Slack rejected the token: " + str(payload.get("error", "unknown error")))
            return {"account": payload.get("user"), "workspace": payload.get("team")}
        if provider == "jira":
            response = client.get(
                config["base_url"] + "/rest/api/3/myself",
                auth=(config["email"], credentials["api_token"]),
                headers={"Accept": "application/json"},
            )
            _raise_response(response, "Jira")
            payload = response.json()
            return {"account": payload.get("displayName"), "account_id": payload.get("accountId")}
    raise IntegrationError("Unsupported integration provider")

def execute_action(provider: str, config: dict[str, str], credentials: dict[str, str],
                   payload: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    """Execute one approved action. No arbitrary URL/tool calls are accepted."""
    with _client() as client:
        if provider == "github":
            body = str(payload["body"]).rstrip() + "\n\n<!-- testagent-workflow:" + workflow_id + " -->"
            response = client.post(
                "https://api.github.com/repos/" + config["repository"] + "/issues",
                headers={"Accept": "application/vnd.github+json",
                         "Authorization": "Bearer " + credentials["access_token"],
                         "X-GitHub-Api-Version": "2026-03-10"},
                json={"title": payload["title"], "body": body},
            )
            _raise_response(response, "GitHub")
            created = response.json()
            return {"id": created.get("number"), "title": created.get("title"), "url": created.get("html_url")}
        if provider == "slack":
            response = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": "Bearer " + credentials["bot_token"]},
                json={"channel": config["channel_id"],
                      "text": payload["text"] + "\n\n_Testagent workflow " + workflow_id + "_"},
            )
            _raise_response(response, "Slack")
            created = response.json()
            if not created.get("ok"):
                raise IntegrationError("Slack could not post the message: " + str(created.get("error", "unknown error")))
            timestamp = str(created.get("ts", ""))
            permalink = ("https://app.slack.com/archives/" + config["channel_id"] + "/p" + timestamp.replace(".", "")
                         if timestamp else None)
            return {"channel": created.get("channel"), "timestamp": timestamp, "url": permalink}
        if provider == "jira":
            description = str(payload["description"])
            adf = {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Testagent workflow: " + workflow_id}]},
            ]}
            response = client.post(
                config["base_url"] + "/rest/api/3/issue",
                auth=(config["email"], credentials["api_token"]),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"fields": {
                    "project": {"key": config["project_key"]},
                    "summary": payload["summary"],
                    "description": adf,
                    "issuetype": {"name": config.get("issue_type", "Task")},
                    "labels": ["testagent", "testagent-" + workflow_id.replace("-", "")[:12]],
                }},
            )
            _raise_response(response, "Jira")
            created = response.json()
            key = created.get("key")
            return {"id": created.get("id"), "key": key,
                    "url": config["base_url"] + "/browse/" + key if key else created.get("self")}
    raise IntegrationError("Unsupported integration provider")
