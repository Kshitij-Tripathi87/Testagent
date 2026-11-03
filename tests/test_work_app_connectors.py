import httpx
import pytest

from dhqa_api.integrations import work_apps


def test_validate_work_app_connector_targets():
    assert work_apps.validate_integration_config("github", {"repository": "octo/project"}) == {
        "repository": "octo/project"
    }
    assert work_apps.validate_integration_config("slack", {"channel_id": "C0123456789"}) == {
        "channel_id": "C0123456789"
    }
    jira = work_apps.validate_integration_config(
        "jira", {"base_url": "https://example.atlassian.net/", "email": "qa@example.test",
                 "project_key": "data", "issue_type": "Task"},
    )
    assert jira["project_key"] == "DATA"
    for provider, config in [
        ("github", {"repository": "https://attacker.example/path"}),
        ("slack", {"channel_id": "general"}),
        ("jira", {"base_url": "https://attacker.example", "email": "a@b.test", "project_key": "DATA"}),
    ]:
        with pytest.raises(ValueError):
            work_apps.validate_integration_config(provider, config)


def test_connector_actions_use_fixed_provider_endpoints(monkeypatch):
    requests = []
    def factory():
        def handler(request):
            requests.append(request)
            if request.url.host == "api.github.com":
                return httpx.Response(201, json={
                    "number": 17, "title": "Incident",
                    "html_url": "https://github.com/acme/repo/issues/17",
                })
            if request.url.host == "slack.com":
                return httpx.Response(200, json={"ok": True, "channel": "C0123456789", "ts": "123.456"})
            if request.url.host == "example.atlassian.net":
                return httpx.Response(201, json={"id": "20001", "key": "DATA-42"})
            return httpx.Response(404)
        return httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(work_apps, "_client", factory)
    github = work_apps.execute_action(
        "github", {"repository": "acme/repo"}, {"access_token": "ghp_test"},
        {"title": "Incident", "body": "Evidence"}, "workflow-123",
    )
    jira = work_apps.execute_action(
        "jira", {"base_url": "https://example.atlassian.net", "email": "qa@example.test",
                 "project_key": "DATA", "issue_type": "Task"},
        {"api_token": "jira-test"}, {"summary": "Incident", "description": "Evidence"}, "workflow-123",
    )
    slack = work_apps.execute_action(
        "slack", {"channel_id": "C0123456789"}, {"bot_token": "xoxb-test"},
        {"text": "Incident"}, "workflow-123",
    )
    assert github["id"] == 17
    assert jira["key"] == "DATA-42"
    assert slack["channel"] == "C0123456789"
    assert [request.url.host for request in requests] == [
        "api.github.com", "example.atlassian.net", "slack.com"
    ]
    assert requests[0].url.path == "/repos/acme/repo/issues"
    assert requests[1].url.path == "/rest/api/3/issue"
    assert requests[2].url.path == "/api/chat.postMessage"



def test_connector_credentials_are_provider_specific():
    assert work_apps.validate_integration_credentials("github", {"access_token": "token"}) == {
        "access_token": "token"
    }
    assert work_apps.validate_integration_credentials("jira", {"api_token": "token"}) == {
        "api_token": "token"
    }
    assert work_apps.validate_integration_credentials("slack", {"bot_token": "token"}) == {
        "bot_token": "token"
    }
    with pytest.raises(ValueError):
        work_apps.validate_integration_credentials("jira", {})
