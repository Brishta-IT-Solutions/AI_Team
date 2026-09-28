"""Local pilot projects and per-project clone URLs.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('local_pilot', sa.Boolean(), server_default=sa.text('false'),
                                        nullable=False))
    op.add_column('repositories', sa.Column('clone_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('repositories', 'clone_url')
    op.drop_column('projects', 'local_pilot')
