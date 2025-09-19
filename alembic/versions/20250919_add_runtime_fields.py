"""add runtime fields to checks and checks_version to users

Revision ID: 20250919_add_runtime_fields
Revises: 
Create Date: 2025-09-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250919_add_runtime_fields'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('checks') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(), nullable=False, server_default='new'))
        batch_op.add_column(sa.Column('last_ping', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_start', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('last_duration_seconds', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_index('ix_checks_status', ['status'])
        batch_op.create_index('ix_checks_last_ping', ['last_ping'])

    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('checks_version', sa.Integer(), nullable=False, server_default='0'))

    # Remove server_default now that existing rows initialized
    with op.batch_alter_table('checks') as batch_op:
        batch_op.alter_column('status', server_default=None)
        batch_op.alter_column('failure_count', server_default=None)
    with op.batch_alter_table('users') as batch_op:
        batch_op.alter_column('checks_version', server_default=None)


def downgrade():
    with op.batch_alter_table('checks') as batch_op:
        try:
            batch_op.drop_index('ix_checks_status')
        except Exception:
            pass
        try:
            batch_op.drop_index('ix_checks_last_ping')
        except Exception:
            pass
        batch_op.drop_column('failure_count')
        batch_op.drop_column('last_duration_seconds')
        batch_op.drop_column('last_start')
        batch_op.drop_column('last_ping')
        batch_op.drop_column('status')

    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('checks_version')
