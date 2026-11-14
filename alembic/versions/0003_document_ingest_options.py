"""documents.ingest_options：保存入库参数，供重试 / 重建复用。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_document_ingest_options"
down_revision: str | None = "0002_memory_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("documents", sa.Column("ingest_options", JSONType, nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "ingest_options")
