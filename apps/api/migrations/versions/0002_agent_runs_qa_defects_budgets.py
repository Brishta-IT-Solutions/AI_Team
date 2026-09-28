"""agent runs qa defects budgets

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 18:24:19.377836
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('workers',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('capabilities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workers_project_id'), 'workers', ['project_id'], unique=False)
    op.create_table('runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('parent_run_id', sa.UUID(), nullable=True),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('envelope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('worker_id', sa.String(length=120), nullable=True),
    sa.Column('claim_token', sa.String(length=64), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('milestone', sa.String(length=200), nullable=True),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cost', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('cost_quality', sa.String(length=24), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('BA','DEVELOPER','QA','JUNIOR')", name='role'),
    sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','BLOCKED','CANCELLED')", name='status'),
    sa.ForeignKeyConstraint(['parent_run_id'], ['runs.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runs_project_id'), 'runs', ['project_id'], unique=False)
    op.create_index('ix_runs_queue', 'runs', ['project_id', 'role', 'created_at'], unique=False, postgresql_where=sa.text("status = 'QUEUED'"))
    op.create_index(op.f('ix_runs_task_id'), 'runs', ['task_id'], unique=False)
    op.create_index('ux_one_active_parent_run', 'runs', ['task_id'], unique=True, postgresql_where=sa.text("parent_run_id IS NULL AND status IN ('QUEUED','RUNNING')"))
    op.create_table('qa_reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('spec_id', sa.UUID(), nullable=False),
    sa.Column('head_sha', sa.String(length=40), nullable=False),
    sa.Column('base_sha', sa.String(length=40), nullable=False),
    sa.Column('verdict', sa.String(length=10), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.ForeignKeyConstraint(['spec_id'], ['requirement_versions.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_qa_reports_project_id'), 'qa_reports', ['project_id'], unique=False)
    op.create_index(op.f('ix_qa_reports_task_id'), 'qa_reports', ['task_id'], unique=False)
    op.create_table('reservations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=6), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('ACTIVE','RELEASED')", name='status'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id')
    )
    op.create_index(op.f('ix_reservations_project_id'), 'reservations', ['project_id'], unique=False)
    op.create_index(op.f('ix_reservations_task_id'), 'reservations', ['task_id'], unique=False)
    op.create_table('run_events',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('run_id', 'sequence')
    )
    op.create_index(op.f('ix_run_events_run_id'), 'run_events', ['run_id'], unique=False)
    op.create_table('defects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('signature', sa.String(length=64), nullable=False),
    sa.Column('ac_id', sa.String(length=24), nullable=True),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('first_report_id', sa.UUID(), nullable=False),
    sa.Column('last_report_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['first_report_id'], ['qa_reports.id'], ),
    sa.ForeignKeyConstraint(['last_report_id'], ['qa_reports.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'signature')
    )
    op.create_index(op.f('ix_defects_project_id'), 'defects', ['project_id'], unique=False)
    op.create_index(op.f('ix_defects_task_id'), 'defects', ['task_id'], unique=False)
    op.add_column('tasks', sa.Column('branch', sa.String(length=255), nullable=True))
    # QA reports are immutable evidence: a retest writes a new report (FR-18).
    op.execute("""
        CREATE TRIGGER qa_reports_append_only BEFORE UPDATE OR DELETE ON qa_reports
        FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS qa_reports_append_only ON qa_reports")
    op.drop_column('tasks', 'branch')
    op.drop_index(op.f('ix_defects_task_id'), table_name='defects')
    op.drop_index(op.f('ix_defects_project_id'), table_name='defects')
    op.drop_table('defects')
    op.drop_index(op.f('ix_run_events_run_id'), table_name='run_events')
    op.drop_table('run_events')
    op.drop_index(op.f('ix_reservations_task_id'), table_name='reservations')
    op.drop_index(op.f('ix_reservations_project_id'), table_name='reservations')
    op.drop_table('reservations')
    op.drop_index(op.f('ix_qa_reports_task_id'), table_name='qa_reports')
    op.drop_index(op.f('ix_qa_reports_project_id'), table_name='qa_reports')
    op.drop_table('qa_reports')
    op.drop_index('ux_one_active_parent_run', table_name='runs', postgresql_where=sa.text("parent_run_id IS NULL AND status IN ('QUEUED','RUNNING')"))
    op.drop_index(op.f('ix_runs_task_id'), table_name='runs')
    op.drop_index('ix_runs_queue', table_name='runs', postgresql_where=sa.text("status = 'QUEUED'"))
    op.drop_index(op.f('ix_runs_project_id'), table_name='runs')
    op.drop_table('runs')
    op.drop_index(op.f('ix_workers_project_id'), table_name='workers')
    op.drop_table('workers')
