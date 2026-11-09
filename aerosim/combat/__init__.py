"""Air combat: the geometry, an adversary that flies it, and the scoring.

The bandit runs the same flight model the player does. Nothing in here gives
it a concession, and there is no weapon model -- what is scored is tracking
time inside a geometric envelope, which is how air-combat training is
assessed and needs no weapon to mean something.
"""

from .adversary import SKILLS, Adversary, AdversaryState, Manoeuvre, Skill
from .engagement import (
    DECISIVE_TRACK,
    SETUP_LABELS,
    Engagement,
    EngagementState,
    Score,
    Setup,
    in_envelope,
)
from .geometry import (
    Geometry,
    available_load_factor,
    corner_speed,
    instantaneous_turn,
    observe,
    specific_energy,
)

__all__ = [
    "SKILLS",
    "Adversary",
    "AdversaryState",
    "DECISIVE_TRACK",
    "Engagement",
    "EngagementState",
    "Geometry",
    "Manoeuvre",
    "SETUP_LABELS",
    "Score",
    "Setup",
    "Skill",
    "available_load_factor",
    "corner_speed",
    "in_envelope",
    "instantaneous_turn",
    "observe",
    "specific_energy",
]
