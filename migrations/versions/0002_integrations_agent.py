"""Add workspace work-app connectors and approval-gated agent workflows."""
import sqlalchemy as sa
from alembic import op
from dhqa_api import models  # noqa: F401 - register model metadata
from dhqa_api.models import AgentWorkflow, AgentWorkflowStep, IntegrationConnection

revision = "0002_integrations_agent"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    IntegrationConnection.__table__.create(bind=bind, checkfirst=True)
    AgentWorkflow.__table__.create(bind=bind, checkfirst=True)
    AgentWorkflowStep.__table__.create(bind=bind, checkfirst=True)
    with op.batch_alter_table("datasets") as batch:
        batch.alter_column("connection_id", existing_type=sa.String(length=36), nullable=True)

def downgrade() -> None:
    bind = op.get_bind()
    AgentWorkflowStep.__table__.drop(bind=bind, checkfirst=True)
    AgentWorkflow.__table__.drop(bind=bind, checkfirst=True)
    IntegrationConnection.__table__.drop(bind=bind, checkfirst=True)
    with op.batch_alter_table("datasets") as batch:
        batch.alter_column("connection_id", existing_type=sa.String(length=36), nullable=False)
