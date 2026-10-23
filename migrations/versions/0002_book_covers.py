"""book covers

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 10:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('library_books', sa.Column('cover', sa.String(length=40), nullable=True))
    op.add_column('library_books', sa.Column('cover_source', sa.String(length=16), nullable=True))


def downgrade():
    op.drop_column('library_books', 'cover_source')
    op.drop_column('library_books', 'cover')
