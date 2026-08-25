"""Auswahl-Algorithmus: welche Vokabel kommt als naechstes dran?

Grundidee (angelehnt an Anki, aber bewusst simpel):

1. Die Auswahl ist **zufaellig** - nie eine feste Reihenfolge.
2. Aber gewichtet. Die Prioritaet ist:
       noch nicht gefragt  >  unsicher  >  mittel  >  sicher
   Eine "sicher"-Karte kommt also weiterhin dran, nur deutlich seltener.
3. Zusaetzlich eine **Abklingzeit**: Karten, die gerade erst bewertet wurden,
   bekommen kurzzeitig weniger Gewicht - je sicherer, desto laenger die Pause.
4. Die zuletzt gezeigte Karte wird nie direkt wiederholt, die davor gezeigten
   werden stark abgewertet - damit sich nichts kurzfristig wiederholt.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Sequence

from .db import Card, parse_iso

# Grundgewicht pro Label. Verhaeltnis 24 : 12 : 5 : 1 -> eine unsichere Karte
# kommt rund 12x so oft wie eine sichere.
BASE_WEIGHT: dict[str | None, float] = {
    None: 24.0,        # noch nicht gefragt
    "unsicher": 12.0,
    "mittel": 5.0,
    "sicher": 1.0,
}

# Stunden, nach denen eine Karte wieder ihr volles Gewicht hat.
COOLDOWN_HOURS: dict[str | None, float] = {
    None: 0.0,
    "unsicher": 3.0,
    "mittel": 12.0,
    "sicher": 48.0,
}

# Selbst "frisch bewertet" blockiert nie komplett - sonst geht bei kleinen
# Listen irgendwann gar nichts mehr.
MIN_COOLDOWN_FACTOR = 0.15

# Faktor fuer Karten, die gerade eben schon dran waren.
RECENT_PENALTY = 0.01


def recent_window(n_cards: int) -> int:
    """Wie viele der zuletzt gezeigten Karten werden gemieden?"""
    return max(0, min(10, n_cards // 3))


def cooldown_factor(card: Card, now: datetime | None = None) -> float:
    hours = COOLDOWN_HOURS.get(card.label, 0.0)
    if hours <= 0:
        return 1.0
    last = parse_iso(card.last_asked_at)
    if last is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    elapsed = (now - last).total_seconds() / 3600.0
    return max(MIN_COOLDOWN_FACTOR, min(1.0, elapsed / hours))


def weight_of(
    card: Card,
    *,
    recent_ids: Sequence[int] = (),
    now: datetime | None = None,
) -> float:
    """Auswahlgewicht einer Karte (groesser = kommt oefter dran)."""
    weight = BASE_WEIGHT.get(card.label, 1.0) * cooldown_factor(card, now)
    if card.id in recent_ids:
        weight *= RECENT_PENALTY
    return max(weight, 1e-6)


def pick_card(
    cards: Sequence[Card],
    *,
    recent_ids: Sequence[int] = (),
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> Card | None:
    """Zieht gewichtet zufaellig eine Karte. ``None``, wenn nichts da ist."""
    if not cards:
        return None
    if len(cards) == 1:
        return cards[0]
    rng = rng or random
    now = now or datetime.now(timezone.utc)

    # Die zuletzt gezeigte Karte wird hart ausgeschlossen (nie zweimal
    # direkt hintereinander), die davor nur abgewertet.
    candidates = list(cards)
    if recent_ids:
        without_last = [c for c in candidates if c.id != recent_ids[0]]
        if without_last:
            candidates = without_last
    window = recent_window(len(cards))
    avoid = tuple(recent_ids[1:window]) if window > 1 else ()

    weights = [weight_of(card, recent_ids=avoid, now=now) for card in candidates]
    return rng.choices(candidates, weights=weights, k=1)[0]


def pick_direction(mode: str, rng: random.Random | None = None) -> str:
    """``fr2de``, ``de2fr`` oder bei ``mixed`` zufaellig eines von beiden."""
    if mode in ("fr2de", "de2fr"):
        return mode
    rng = rng or random
    return rng.choice(["fr2de", "de2fr"])
