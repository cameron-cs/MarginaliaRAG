"""who settled each question, for the admin's questions log

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('questions', sa.Column('answered_by', sa.String(length=16), nullable=True))
    op.create_index('ix_questions_answered_by', 'questions', ['answered_by'])
    op.create_index('ix_questions_created_at', 'questions', ['created_at'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""
            UPDATE questions SET answered_by = CASE
                WHEN status = 'refused' THEN 'guard'
                WHEN (details::jsonb) ? 'cached' THEN 'cache'
                WHEN status IN ('done', 'failed') THEN 'agent' END""")


def downgrade():
    op.drop_index('ix_questions_created_at', 'questions')
    op.drop_index('ix_questions_answered_by', 'questions')
    op.drop_column('questions', 'answered_by')
