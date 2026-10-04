"""Moved to mm_core.connections (every site has the record pages' connections panel).

Kept so the old API path (micromax.connections.get_connections) keeps working: same function object, still
whitelisted under this name.
"""

from mm_core.connections import *  # noqa: F401,F403
from mm_core.connections import get_connections  # noqa: F401
