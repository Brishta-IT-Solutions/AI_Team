"""foundation schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-28 15:31:19.530556
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE lease_fencing_seq START 1 NO CYCLE")
    op.create_table('audit_events',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('actor_kind', sa.String(length=10), nullable=False),
    sa.Column('actor_id', sa.String(length=255), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=False),
    sa.Column('object_id', sa.String(length=64), nullable=True),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('before_hash', sa.String(length=64), nullable=True),
    sa.Column('after_hash', sa.String(length=64), nullable=True),
    sa.Column('policy_version', sa.Integer(), nullable=True),
    sa.Column('correlation_id', sa.String(length=64), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('event_id')
    )
    op.create_index(op.f('ix_audit_events_project_id'), 'audit_events', ['project_id'], unique=False)
    op.create_index('ix_audit_project_time', 'audit_events', ['project_id', 'created_at'], unique=False)
    op.create_table('idempotency_records',
    sa.Column('principal_id', sa.String(length=255), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('principal_id', 'key')
    )
    op.create_table('outbox_events',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('aggregate_type', sa.String(length=40), nullable=False),
    sa.Column('aggregate_id', sa.String(length=64), nullable=False),
    sa.Column('aggregate_version', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=80), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('event_id')
    )
    op.create_index('ix_outbox_project_seq', 'outbox_events', ['project_id', 'seq'], unique=False)
    op.create_index('ix_outbox_undelivered', 'outbox_events', ['seq'], unique=False, postgresql_where=sa.text('delivered_at IS NULL'))
    op.create_table('processed_events',
    sa.Column('consumer', sa.String(length=80), nullable=False),
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('consumer', 'event_id')
    )
    op.create_table('projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('classification', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('policy_version', sa.Integer(), nullable=False),
    sa.Column('policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('task_seq', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("key ~ '^[A-Z][A-Z0-9]{1,9}$'", name='key_format'),
    sa.CheckConstraint("status IN ('DRAFT','ACTIVE','DISABLED','ARCHIVED')", name='status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('workspace_admin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subject')
    )
    op.create_table('agent_configs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('endpoint_ref', sa.String(length=500), nullable=True),
    sa.Column('auth_method', sa.String(length=40), nullable=False),
    sa.Column('adapter_version', sa.String(length=40), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=False),
    sa.Column('prompt_hash', sa.String(length=64), nullable=False),
    sa.Column('permissions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('limits', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('secret_ref', sa.String(length=500), nullable=True),
    sa.Column('output_schema_version', sa.String(length=16), nullable=False),
    sa.Column('connection_test', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'role', 'version')
    )
    op.create_index(op.f('ix_agent_configs_project_id'), 'agent_configs', ['project_id'], unique=False)
    op.create_table('budgets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('period', sa.String(length=16), nullable=False),
    sa.Column('cap', sa.Numeric(precision=14, scale=6), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.CheckConstraint("scope IN ('PROJECT_MONTH','TICKET','RUN')", name='scope'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'scope', 'period')
    )
    op.create_index(op.f('ix_budgets_project_id'), 'budgets', ['project_id'], unique=False)
    op.create_table('memberships',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('roles', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'project_id')
    )
    op.create_index(op.f('ix_memberships_project_id'), 'memberships', ['project_id'], unique=False)
    op.create_table('repositories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('github_id', sa.BigInteger(), nullable=False),
    sa.Column('installation_id', sa.BigInteger(), nullable=False),
    sa.Column('owner', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('base_branch', sa.String(length=255), nullable=False),
    sa.Column('verification', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id')
    )
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('priority', sa.String(length=2), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('execution_status', sa.String(length=16), nullable=False),
    sa.Column('status_reason', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('current_spec_id', sa.UUID(), nullable=True),
    sa.Column('approved_spec_id', sa.UUID(), nullable=True),
    sa.Column('head_sha', sa.String(length=40), nullable=True),
    sa.Column('base_sha', sa.String(length=40), nullable=True),
    sa.Column('current_qa_report_id', sa.UUID(), nullable=True),
    sa.Column('repair_count', sa.Integer(), nullable=False),
    sa.Column('repair_limit', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("priority IN ('P0','P1','P2','P3')", name='priority'),
    sa.CheckConstraint('repair_count >= 0 AND repair_limit >= 0', name='repairs'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'key')
    )
    op.create_index(op.f('ix_tasks_project_id'), 'tasks', ['project_id'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('release_id', sa.UUID(), nullable=True),
    sa.Column('gate', sa.String(length=16), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('scope_hash', sa.String(length=64), nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('human_id', sa.UUID(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('supersedes_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("decision IN ('APPROVED','CHANGES_REQUESTED','REJECTED','REVOKED')", name='decision'),
    sa.CheckConstraint("gate IN ('REQUIREMENTS','MERGE','RELEASE')", name='gate'),
    sa.CheckConstraint('(task_id IS NULL) <> (release_id IS NULL)', name='one_subject'),
    sa.ForeignKeyConstraint(['human_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['supersedes_id'], ['approvals.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approvals_project_id'), 'approvals', ['project_id'], unique=False)
    op.create_index(op.f('ix_approvals_task_id'), 'approvals', ['task_id'], unique=False)
    op.create_table('branch_leases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('repository_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('branch', sa.String(length=255), nullable=False),
    sa.Column('fencing_token', sa.BigInteger(), nullable=False),
    sa.Column('holder', sa.String(length=255), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('release_reason', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fencing_token')
    )
    op.create_index(op.f('ix_branch_leases_project_id'), 'branch_leases', ['project_id'], unique=False)
    op.create_index('ux_branch_active_lease', 'branch_leases', ['repository_id', 'branch'], unique=True, postgresql_where=sa.text('released_at IS NULL'))
    op.create_table('requirement_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by_kind', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'version')
    )
    op.create_index(op.f('ix_requirement_versions_project_id'), 'requirement_versions', ['project_id'], unique=False)
    op.create_table('task_dependencies',
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('depends_on_id', sa.UUID(), nullable=False),
    sa.CheckConstraint('task_id <> depends_on_id', name='no_self_dependency'),
    sa.ForeignKeyConstraint(['depends_on_id'], ['tasks.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('task_id', 'depends_on_id')
    )
    op.create_table('acceptance_criteria',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('spec_id', sa.UUID(), nullable=False),
    sa.Column('stable_key', sa.String(length=24), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('verification', sa.Text(), nullable=False),
    sa.Column('mandatory', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['spec_id'], ['requirement_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('spec_id', 'stable_key')
    )
    op.create_index(op.f('ix_acceptance_criteria_spec_id'), 'acceptance_criteria', ['spec_id'], unique=False)
    op.create_foreign_key("fk_task_current_spec", "tasks", "requirement_versions",
                          ["current_spec_id"], ["id"])
    op.create_foreign_key("fk_task_approved_spec", "tasks", "requirement_versions",
                          ["approved_spec_id"], ["id"])

    # Append-only records (FR-15, FR-22, FR-27): no role may UPDATE or DELETE them.
    # Retention deletion will run through a dedicated, audited maintenance function.
    op.execute("""
        CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    for table in ("audit_events", "approvals", "requirement_versions", "acceptance_criteria"):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
        """)


def downgrade() -> None:
    for table in ("audit_events", "approvals", "requirement_versions", "acceptance_criteria"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation()")
    op.drop_constraint("fk_task_approved_spec", "tasks", type_="foreignkey")
    op.drop_constraint("fk_task_current_spec", "tasks", type_="foreignkey")
    op.drop_index(op.f('ix_acceptance_criteria_spec_id'), table_name='acceptance_criteria')
    op.drop_table('acceptance_criteria')
    op.drop_table('task_dependencies')
    op.drop_index(op.f('ix_requirement_versions_project_id'), table_name='requirement_versions')
    op.drop_table('requirement_versions')
    op.drop_index('ux_branch_active_lease', table_name='branch_leases', postgresql_where=sa.text('released_at IS NULL'))
    op.drop_index(op.f('ix_branch_leases_project_id'), table_name='branch_leases')
    op.drop_table('branch_leases')
    op.drop_index(op.f('ix_approvals_task_id'), table_name='approvals')
    op.drop_index(op.f('ix_approvals_project_id'), table_name='approvals')
    op.drop_table('approvals')
    op.drop_index(op.f('ix_tasks_project_id'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('repositories')
    op.drop_index(op.f('ix_memberships_project_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_budgets_project_id'), table_name='budgets')
    op.drop_table('budgets')
    op.drop_index(op.f('ix_agent_configs_project_id'), table_name='agent_configs')
    op.drop_table('agent_configs')
    op.drop_table('users')
    op.drop_table('projects')
    op.drop_table('processed_events')
    op.drop_index('ix_outbox_undelivered', table_name='outbox_events', postgresql_where=sa.text('delivered_at IS NULL'))
    op.drop_index('ix_outbox_project_seq', table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_table('idempotency_records')
    op.drop_index('ix_audit_project_time', table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_project_id'), table_name='audit_events')
    op.drop_table('audit_events')
    op.execute("DROP SEQUENCE IF EXISTS lease_fencing_seq")
