"""Signup quiz answers → starting in-app chip wallet.

Tokens are entertainment chips only. Signup never locks a skins pot;
players pick tokens per hole each round.
"""

from __future__ import annotations

import json
from typing import Any

INTENT_SKINS = "skins"
INTENT_SCORE = "score"
INTENT_PRACTICE = "practice"

STYLE_SOLO = "solo"
STYLE_BUDDY = "buddy"
STYLE_GROUP = "group"
STYLE_MIX = "mix"

FREQ_NEVER = "never"
FREQ_LEARNING = "learning"
FREQ_SOMETIMES = "sometimes"
FREQ_MOST = "most"

FEEL_SMALL = "small"
FEEL_WEEKEND = "weekend"
FEEL_SERIOUS = "serious"
FEEL_HIGH = "high_table"

POT_LOW = "low"
POT_MEDIUM = "medium"
POT_HIGH = "high"
POT_VARIES = "varies"

INTENTS = frozenset({INTENT_SKINS, INTENT_SCORE, INTENT_PRACTICE})
STYLES = frozenset({STYLE_SOLO, STYLE_BUDDY, STYLE_GROUP, STYLE_MIX})
FREQUENCIES = frozenset({FREQ_NEVER, FREQ_LEARNING, FREQ_SOMETIMES, FREQ_MOST})
FEELS = frozenset({FEEL_SMALL, FEEL_WEEKEND, FEEL_SERIOUS, FEEL_HIGH})
POTS = frozenset({POT_LOW, POT_MEDIUM, POT_HIGH, POT_VARIES})

# Listed grid. Unlisted feel+pot pairs fall back to 600.
_SKINS_GRID: dict[tuple[str, str], int] = {
    (FEEL_SMALL, POT_LOW): 250,
    (FEEL_SMALL, POT_MEDIUM): 400,
    (FEEL_SMALL, POT_HIGH): 400,
    (FEEL_WEEKEND, POT_LOW): 400,
    (FEEL_WEEKEND, POT_MEDIUM): 600,
    (FEEL_WEEKEND, POT_HIGH): 900,
    (FEEL_SERIOUS, POT_MEDIUM): 1000,
    (FEEL_SERIOUS, POT_HIGH): 1500,
    (FEEL_HIGH, POT_MEDIUM): 1800,
    (FEEL_HIGH, POT_HIGH): 2500,
}

SKINS_TOPUP_HOLES = 18


class SignupProfileError(ValueError):
    pass


def parse_profile(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            return data
    return None


def dump_profile(profile: dict[str, Any]) -> str:
    return json.dumps(profile, separators=(",", ":"))


def needs_skins_detail(intent: str, frequency: str) -> bool:
    return intent == INTENT_SKINS and frequency != FREQ_NEVER


def starting_tokens(
    intent: str,
    style: str,
    frequency: str,
    feel: str | None = None,
    pot_band: str | None = None,
) -> int:
    """Wallet size from signup answers. Never used as a locked skins pot."""
    if intent == INTENT_PRACTICE:
        return 200
    if intent == INTENT_SCORE:
        return 150
    if intent != INTENT_SKINS:
        return 150
    if frequency == FREQ_NEVER:
        return 150
    if not pot_band:
        return 600
    if pot_band == POT_VARIES:
        return 800
    resolved_feel = feel or FEEL_WEEKEND
    if frequency == FREQ_LEARNING:
        resolved_feel = FEEL_SMALL
    return _SKINS_GRID.get((resolved_feel, pot_band), 600)


def first_skins_topup_target(pot_per_hole: int) -> int:
    pot = max(0, int(pot_per_hole))
    return pot * SKINS_TOPUP_HOLES


def validate_answers(
    *,
    play_intent: str,
    play_style: str,
    skins_frequency: str,
    skins_feel: str | None = None,
    skins_pot_band: str | None = None,
) -> dict[str, Any]:
    intent = (play_intent or "").strip()
    style = (play_style or "").strip()
    freq = (skins_frequency or "").strip()
    feel = (skins_feel or "").strip() or None
    pot = (skins_pot_band or "").strip() or None
    if intent not in INTENTS:
        raise SignupProfileError("Choose what you want SkinsCaddy for.")
    if style not in STYLES:
        raise SignupProfileError("Choose how you usually play.")
    if freq not in FREQUENCIES:
        raise SignupProfileError("Choose how often you play skins.")
    if needs_skins_detail(intent, freq):
        if feel and feel not in FEELS:
            raise SignupProfileError("Choose a skins feel.")
        if pot and pot not in POTS:
            raise SignupProfileError("Choose a typical pot per hole.")
        if not pot:
            pot = None
    else:
        feel = None
        pot = None
    start = starting_tokens(intent, style, freq, feel, pot)
    return {
        "play_intent": intent,
        "play_style": style,
        "skins_frequency": freq,
        "skins_feel": feel,
        "skins_pot_band": pot,
        "starting_tokens": int(start),
        "skins_topup_done": False,
    }
