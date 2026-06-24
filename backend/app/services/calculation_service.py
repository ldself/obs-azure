"""Server-side calculation service.

The authoritative calculation service is the only source of trusted calculation
results (RULE 3). It reads all rates from the database (RULE 4) and is built out
from Phase 4 (Workforce Planning compensation) with 100% line coverage required
by the phase gate.

Phase 3 adds the recalculation stub called when compensation component mappings
change (Build Plan v1.2 §4.3). Phase 4 replaces the stub with live logic.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def stub_recalculate_open_fiscal_years(component_code: str) -> None:
    """Phase 3 stub — full recalculation wired in Phase 4 (RULE 4).

    Called synchronously inside the component-mapping mutation transaction after
    the COMPENSATION_MAPPING_UPDATED audit event is written. Does not commit or
    rollback independently of the caller's transaction.
    """
    _log.info(
        "Recalculation stub: COMPENSATION_MAPPING_UPDATED component_code=%s",
        component_code,
    )
