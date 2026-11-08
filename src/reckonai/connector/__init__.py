"""The marketplace connector: client, field mapping, validation and ingestion.

specs/roadmap.md Phase 6, run as sub-block A3 per
`specs/2026-11-08-replan/requirements.md` R3.

Four modules, each with one job:

  - `client` - HTTP against `MARKETPLACE_BASE_URL`, the real retrieval path
  - `mapping` - the mapping document, the closed transform set, and section
    9.2's deterministic passes 1 and 2
  - `validate` - section 9.4's gate: deterministic, blocking, naming the
    offending field, and never half-activating a mapping
  - `ingest` - normalization into the canonical tables, idempotent, with a
    quarantine for anything that cannot be mapped

`sim_mapping` holds the active document for the simulated marketplace, which is
a fixture rather than configuration - see that module.
"""

from __future__ import annotations

from reckonai.connector.client import (
    MarketplaceClient,
    MarketplaceNotConfigured,
    MarketplaceUnavailable,
)
from reckonai.connector.mapping import (
    SYNONYMS,
    TRANSFORMS,
    FieldMapping,
    MappingDocument,
    MoneyMapping,
    ResourceMapping,
    TransformError,
    apply_transform,
    suggest,
)
from reckonai.connector.validate import (
    MappingInvalid,
    Problem,
    validate,
    validate_against_samples,
    validate_document,
)

__all__ = [
    "SYNONYMS",
    "TRANSFORMS",
    "FieldMapping",
    "MappingDocument",
    "MappingInvalid",
    "MarketplaceClient",
    "MarketplaceNotConfigured",
    "MarketplaceUnavailable",
    "MoneyMapping",
    "Problem",
    "ResourceMapping",
    "TransformError",
    "apply_transform",
    "suggest",
    "validate",
    "validate_against_samples",
    "validate_document",
]
