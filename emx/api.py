# emx/api.py
from .emx_system import (
    update_emx,
    update_atmosphere,
    update_cell_atmosphere,
    update_social_need,
    compute_emx_sensitivity_from_traits,
    compute_emx_base_from_traits,
    _trait_label,
    compute_npc_dyad,
    compute_emx_archetype,
    configure_runtime,
    compute_emx_context_multiplier,
    compute_emx_dependency_shift,
    compute_anchor_field
)
from .emx_composites import (
    compute_composites, 
    zero_composites, 
    apply_cancellation, 
    cancel_tick,
    compute_neutral_score,
    EMERGE_THRESHOLD,
    EMERGE_RATE,
    TIER_MAP,
    COMPOSITE_COORDS,
    COMPOSITE_COLORS,
    PRIMARY_COMPOSITES,
    SECONDARY_COMPOSITES,
    TERTIARY_COMPOSITES,
    ALL_COMPOSITES
)
from .emx_sphere import dominant_region, region_attrs
from . import emx_system as emx_runtime
