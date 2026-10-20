"""Database implementation. API and UI must not bypass the service boundary.

The provisioning tables (SCIM attribute overlay, organization audit ledger,
approval policies and approval vote bindings) live in their own module so the
existing assessment tables stay untouched. They are imported here so that every
``app.db.*`` import registers them on the shared metadata: ``create_all`` and
``alembic check`` must see the whole schema, not only the part that a
particular module happens to import.
"""

from app.db import provisioning_models  # noqa: F401  (registers the tables)
