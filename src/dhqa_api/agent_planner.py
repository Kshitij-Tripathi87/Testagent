"""Deterministic, evidence-first incident response plan builder."""
from __future__ import annotations
from typing import Any

def build_incident_response_steps(incident: Any, dataset: Any, run: Any,
                                  connections: list[Any], workflow_id: str) -> list[dict[str, Any]]:
    details = incident.details_json or {}
    trace = details.get("trace", [])
    evidence_lines = [
        f"- {item.get('urn')}: " + (
            "unverified — " + str(item.get("detail", "No row data available"))
            if not item.get("checked") else
            ("failed — " if not item.get("passed") else "passed — ") + str(item.get("detail", ""))
        ) for item in trace[:30]
    ]
    context = {
        "incident_id": incident.id, "incident_title": incident.title,
        "dataset_id": dataset.id, "dataset_name": dataset.name, "dataset_urn": dataset.urn,
        "quality_run_id": run.id, "summary": details.get("summary") or incident.title,
        "origin_urn": details.get("origin_urn"), "hop_distance": details.get("hop_distance"),
        "trace": trace[:30], "evidence_lines": evidence_lines,
    }
    marker = f"testagent-workflow:{workflow_id}"
    issue_title = f"Data quality incident: {dataset.name} — {incident.title}"[:240]
    github_body = "\n".join([
        "## Data quality incident", f"Incident: {incident.title}", f"Incident ID: {incident.id}",
        f"Dataset: {dataset.name}", f"Dataset URN: {dataset.urn}", f"Quality run: {run.id}", "",
        "## Finding", str(context["summary"]), "",
        f"Deepest confirmed origin: {context['origin_urn'] or 'Not established'}",
        f"Lineage hops: {context['hop_distance'] if context['hop_distance'] is not None else 'Unknown'}",
        "", "## Lineage evidence", *(evidence_lines or ["No lineage trace was recorded."]),
        "", "Created by the Testagent incident-response workflow.", f"<!-- {marker} -->",
    ])
    jira_description = "\n".join([
        f"Incident: {incident.title}", f"Incident ID: {incident.id}",
        f"Dataset: {dataset.name} ({dataset.urn})", f"Quality run: {run.id}", "",
        "Finding:", str(context["summary"]), "",
        f"Deepest confirmed origin: {context['origin_urn'] or 'Not established'}",
        "", "Lineage evidence:", *(evidence_lines or ["No lineage trace was recorded."]),
    ])
    slack_text = "\n".join([
        f":warning: *Testagent data quality incident: {incident.title}*",
        f"Dataset: {dataset.name}", f"Finding: {context['summary']}",
        f"Root cause: {context['origin_urn'] or 'Not established'}",
        f"Quality run: {run.id}",
        "Related engineering ticket links are included below when those connectors are selected.",
    ])
    steps: list[dict[str, Any]] = [{
        "step_key": "analyze_incident", "provider": "testagent",
        "title": "Collect incident evidence",
        "description": "Read the saved quality result and bounded lineage trace; preserve unverified ancestors as unverified.",
        "connection_id": None, "payload": context,
    }]
    order = {"github": 0, "jira": 1, "slack": 2}
    for connection in sorted(connections, key=lambda item: order[item.provider]):
        if connection.provider == "github":
            title, payload = "Create GitHub engineering issue", {"title": issue_title, "body": github_body}
        elif connection.provider == "jira":
            title, payload = "Create Jira tracking ticket", {"summary": issue_title[:250], "description": jira_description}
        elif connection.provider == "slack":
            title, payload = "Notify Slack incident channel", {"text": slack_text}
        else:
            continue
        config = connection.config_json or {}
        destination = {"github": config.get("repository"), "jira": config.get("project_key"),
                       "slack": config.get("channel_id")}[connection.provider]
        steps.append({
            "step_key": connection.provider, "provider": connection.provider, "title": title,
            "description": f"Create the approved incident follow-up in {connection.name} ({destination}).",
            "connection_id": connection.id, "payload": payload,
        })
    return steps
