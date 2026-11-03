"""Sequential, approval-gated multi-step incident response worker."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from dhqa_api.db import SessionLocal
from dhqa_api.encryption import decrypt_secret
from dhqa_api.integrations.work_apps import execute_action
from dhqa_api.models import AgentWorkflow, AgentWorkflowStep, IntegrationConnection, utcnow

def execute_agent_workflow(workflow_id: str) -> dict[str, Any]:
    """RQ entry point. Successful steps are persisted and skipped on retry."""
    with SessionLocal() as db:
        workflow = db.get(AgentWorkflow, workflow_id)
        if not workflow:
            raise ValueError(f"Agent workflow not found: {workflow_id}")
        if workflow.status == "completed":
            return {"workflow_id": workflow_id, "status": "completed", "idempotent": True}
        if workflow.status not in {"queued", "running"}:
            raise ValueError("Agent workflow must be approved before execution")
        workflow.status = "running"
        workflow.started_at = workflow.started_at or utcnow()
        db.commit()
        steps = db.scalars(
            select(AgentWorkflowStep).where(AgentWorkflowStep.workflow_id == workflow_id)
            .order_by(AgentWorkflowStep.position)
        ).all()
        completed = 0
        failed_step: str | None = None
        for step in steps:
            if step.status == "completed":
                completed += 1
                continue
            if failed_step:
                step.status = "skipped"
                step.error_message = "Skipped because an earlier workflow step failed"
                db.commit()
                continue
            step.status = "running"
            step.error_message = None
            db.commit()
            try:
                if step.provider == "testagent":
                    result = step.input_json or {}
                else:
                    connection = db.get(IntegrationConnection, step.connection_id)
                    if not connection or connection.workspace_id != workflow.workspace_id:
                        raise RuntimeError("The selected integration connection is no longer available")
                    if connection.provider != step.provider:
                        raise RuntimeError("Integration provider changed after the plan was prepared")
                    credentials = json.loads(decrypt_secret(connection.secret_ciphertext))
                    action_payload = dict(step.input_json or {})
                    if step.provider in {"jira", "slack"}:
                        ticket_links = []
                        for previous in steps:
                            if previous.position >= step.position or previous.status != "completed":
                                continue
                            if step.provider == "jira" and previous.provider != "github":
                                continue
                            if step.provider == "slack" and previous.provider not in {"github", "jira"}:
                                continue
                            url = (previous.result_json or {}).get("url")
                            if url:
                                ticket_links.append(f"{previous.provider.upper()}: {url}")
                        if ticket_links and step.provider == "jira":
                            action_payload["description"] = (
                                str(action_payload.get("description", ""))
                                + "\n\nGitHub engineering issue:\n"
                                + "\n".join(ticket_links)
                            )
                        elif ticket_links:
                            action_payload["text"] = (
                                str(action_payload.get("text", ""))
                                + "\n\n*Created follow-up tickets*\n"
                                + "\n".join(ticket_links)
                            )
                    result = execute_action(step.provider, connection.config_json or {},
                                            credentials, action_payload, workflow_id)
                step.result_json = result
                step.status = "completed"
                step.finished_at = utcnow()
                completed += 1
                db.commit()
            except Exception as exc:
                step.status = "failed"
                step.error_message = str(exc)[:1200]
                step.finished_at = utcnow()
                failed_step = step.step_key
                db.commit()
        workflow = db.get(AgentWorkflow, workflow_id)
        workflow.finished_at = utcnow()
        workflow.status = ("partial_failed" if completed else "failed") if failed_step else "completed"
        workflow.result_json = {
            "completed_steps": completed, "total_steps": len(steps), "failed_step": failed_step,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        db.commit()
        return {"workflow_id": workflow_id, "status": workflow.status, **workflow.result_json}
