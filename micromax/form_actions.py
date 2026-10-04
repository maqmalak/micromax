"""Moved to mm_core.form_actions (Duplicate / Create ... actions on every site's record pages).

Kept so the old API paths (micromax.form_actions.duplicate / make_mapped) keep working: same function objects,
still whitelisted under these names.
"""

from mm_core.form_actions import duplicate, make_mapped  # noqa: F401
