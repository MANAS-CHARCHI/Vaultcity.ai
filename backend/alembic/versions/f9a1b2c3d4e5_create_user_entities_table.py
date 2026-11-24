"""create user_entities table for identity knowledge graph

Revision ID: f9a1b2c3d4e5
Revises: e7b4c9d1a2f8
Create Date: 2026-11-24 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f9a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e7b4c9d1a2f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_entities table for fast (0ms) multi-hop entity resolution."""
    op.create_table(
        "user_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("entity_key", sa.String(64), nullable=False, index=True),
        sa.Column("entity_value", sa.String(255), nullable=False),
        sa.Column("entity_label", sa.String(128), nullable=True),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "entity_key", name="uq_user_entity_key"),
    )


def downgrade() -> None:
    """Drop user_entities table."""
    op.drop_table("user_entities")
