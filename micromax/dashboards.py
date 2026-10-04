"""Moved to mm_core.dashboards, which every site on the bench has.

Kept so existing imports (micromax.pnl_simulator's COST_OF_SALES) and the old API paths
(micromax.dashboards.get_dashboard / get_drilldown) keep working: the functions are the same
objects, so they stay whitelisted under this name too.
"""

from mm_core.dashboards import *  # noqa: F401,F403
from mm_core.dashboards import COST_OF_SALES, get_dashboard, get_dashboard_filters, get_drilldown  # noqa: F401
