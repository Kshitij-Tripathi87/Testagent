"""Centralised configuration constants for dhqa.

Previously these were scattered as hardcoded literals across modules
(``max_hops=5``, ``max_staleness_hours=24``, join key ``"id"``,
source name ``"raw"``). Centralising them here makes the agent's
behaviour tunable without a code-change per call site, and documents
the defaults in one place.
"""

from __future__ import annotations


# Lineage tracing
DEFAULT_MAX_HOPS: int = 5
# How many ancestor datasets the lineage walker will visit before giving up.

# Freshness / SLA
DEFAULT_SLA_HOURS: int = 24
# Default freshness SLA in hours. A dataset whose timestamp column max value
# is older than this is considered stale.

# Referential integrity
DEFAULT_JOIN_KEY: str = "id"
# Default join column for referential checks when a dataset's primary key
# cannot be inferred from its schema (see dataset_model.derive_primary_key).

# Codegen
DEFAULT_SOURCE_NAME: str = "raw"
# The dbt source() name used in generated models (e.g. {{ source('raw', '<model>') }}).

DEFAULT_INCREMENTAL_KEY: str = "id"
# Default unique_key for dbt incremental materialisation.

# CI gate
DEFAULT_PYTHON_VERSION: str = "3.11"
