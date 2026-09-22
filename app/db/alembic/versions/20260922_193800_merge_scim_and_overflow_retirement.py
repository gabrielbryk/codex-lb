"""Join the parallel SCIM and subscription-overflow retirement revisions."""

from __future__ import annotations

revision = "20260922_193800_merge_scim_and_overflow_retirement"
down_revision = (
    "20260914_000000_add_scim_tokens",
    "20260914_000000_drop_subscription_overflow_schema",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
