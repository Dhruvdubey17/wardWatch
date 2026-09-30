"""Patients, encounters, observations, alerts and the append-only alert_events table.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("mrn", sa.String(64), nullable=False),
        sa.Column("family", sa.String(128), nullable=False),
        sa.Column("given", sa.String(128), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("gender", sa.String(16), nullable=False),
        sa.Column("resource", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_patients_mrn", "patients", ["mrn"], unique=True)
    op.create_table(
        "encounters",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("patient_id", sa.String(64), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("bed", sa.String(64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("resource", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_encounters_patient_id", "encounters", ["patient_id"])
    op.create_table(
        "observations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("patient_id", sa.String(64), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("encounter_id", sa.String(64), sa.ForeignKey("encounters.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("effective", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("unit", sa.String(32)),
        sa.Column("message_time", sa.String(64), nullable=False),
        sa.Column("resource", JSONB(), nullable=False),
    )
    op.create_index("ix_observations_patient_id", "observations", ["patient_id"])
    op.create_index("ix_observations_encounter_id", "observations", ["encounter_id"])
    op.create_index(
        "ix_observations_patient_code_effective",
        "observations",
        ["patient_id", "code", "effective"],
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mrn", sa.String(64), nullable=False),
        sa.Column("encounter_id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("icu_hour", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("calibrated_probability", sa.Float()),
        sa.Column("news2", JSONB(), nullable=False),
        sa.Column("top_factors", JSONB(), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("message_time", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(128)),
    )
    op.create_index("ix_alerts_mrn", "alerts", ["mrn"])
    op.create_index("ix_alerts_encounter_id", "alerts", ["encounter_id"])
    op.create_index("ix_alerts_raised_at", "alerts", ["raised_at"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("alerts.id"), nullable=False),
        sa.Column("transition", sa.String(16), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=False),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("note", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_events_alert_id", "alert_events", ["alert_id"])
    # The audit trail is append-only: the database itself refuses updates and deletes.
    op.execute(
        """
        CREATE FUNCTION alert_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'alert_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER alert_events_no_change
        BEFORE UPDATE OR DELETE ON alert_events
        FOR EACH ROW EXECUTE FUNCTION alert_events_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER alert_events_no_change ON alert_events")
    op.execute("DROP FUNCTION alert_events_append_only()")
    op.drop_table("alert_events")
    op.drop_table("alerts")
    op.drop_table("observations")
    op.drop_table("encounters")
    op.drop_table("patients")
