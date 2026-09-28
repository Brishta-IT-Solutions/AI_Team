"""Soft delete for tickets: hidden from the board, history kept.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tasks', sa.Column('deleted_by', sa.UUID(), nullable=True))
    op.create_foreign_key('tasks_deleted_by_fkey', 'tasks', 'users', ['deleted_by'], ['id'])


def downgrade() -> None:
    op.drop_constraint('tasks_deleted_by_fkey', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'deleted_by')
    op.drop_column('tasks', 'deleted_at')
