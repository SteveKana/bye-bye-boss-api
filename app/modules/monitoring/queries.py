"""Read-only table handles for the other modules' data.

The dashboard only ever reads, and importing other modules' ORM models would
break the module boundaries (tests/test_architecture.py). These lightweight
`sa.table` handles name just the columns the dashboard needs; if a column is
renamed in its owner module, tests/modules/monitoring catches it.
"""

from __future__ import annotations

import sqlalchemy as sa

users = sa.table(
    "users",
    sa.column("id", sa.Uuid),
    sa.column("email"),
    sa.column("first_name"),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("is_verified", sa.Boolean),
    sa.column("is_active", sa.Boolean),
    sa.column("isadmin", sa.Boolean),
    sa.column("google_id"),
)
profiles = sa.table(
    "candidate_profiles",
    sa.column("id", sa.Uuid),
    sa.column("user_id", sa.Uuid),
    sa.column("status"),
    sa.column("verification_completed_at", sa.DateTime(timezone=True)),
)
matches = sa.table(
    "candidate_matches",
    sa.column("id", sa.Uuid),
    sa.column("candidate_profile_id", sa.Uuid),
    sa.column("job_offer_id", sa.Uuid),
    sa.column("status"),
    sa.column("prefilter_score", sa.Integer),
    sa.column("career_score", sa.Integer),
    sa.column("application_status"),
    sa.column("application_status_updated_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("computed_at", sa.DateTime(timezone=True)),
)
offers = sa.table(
    "job_offers",
    sa.column("id", sa.Uuid),
    sa.column("title"),
    sa.column("company_name"),
    sa.column("source"),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
brief_entries = sa.table(
    "notification_brief_entries",
    sa.column("user_id", sa.Uuid),
    sa.column("sent_at", sa.DateTime(timezone=True)),
    sa.column("channels_sent", sa.JSON),
)
preferences = sa.table(
    "notification_preferences",
    sa.column("user_id", sa.Uuid),
    sa.column("email_enabled", sa.Boolean),
    sa.column("discord_enabled", sa.Boolean),
    sa.column("whatsapp_enabled", sa.Boolean),
)
emails = sa.table(
    "email_messages",
    sa.column("subject"),
    sa.column("status"),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
batches = sa.table(
    "matching_batches",
    sa.column("status"),
    sa.column("stage"),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
