"""Hourly scores from ward.scores, one row per encounter and ICU hour.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scores",
        sa.Column("encounter_id", sa.String(64), primary_key=True),
        sa.Column("icu_hour", sa.Integer(), primary_key=True),
        sa.Column("mrn", sa.String(64), nullable=False),
        sa.Column("hour_ending", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("news2_total", sa.Integer(), nullable=False),
        sa.Column("news2", JSONB(), nullable=False),
        sa.Column("raw_score", sa.Float()),
        sa.Column("calibrated_probability", sa.Float()),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("alerted", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_scores_mrn", "scores", ["mrn"])


def downgrade() -> None:
    op.drop_table("scores")
