"""
Dataclass models for the structured vision result.

These are plain Python dataclasses — not database models.
The poker engine consumes VisionGameState objects after the
vision pipeline produces them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlayerState:
    """Represents a single player's observable state at the table."""
    seat:             int
    name:             str | None       = None
    stack:            float | None     = None
    stack_confidence: float            = 0.0
    status:           str              = "active"   # active | folded | all_in | sitting_out | empty
    current_bet:      float | None     = None
    is_hero:          bool             = False


@dataclass
class TimerState:
    """A visible countdown timer."""
    seat:               int
    seconds_remaining:  float


@dataclass
class VisionGameState:
    """
    Fully structured, validated game state extracted from a screenshot.

    All confidence fields range from 0.0 (uncertain) to 1.0 (certain).
    Null fields mean the information was not observable in the image.
    """
    # -- Hero hand -----------------------------------------------------------
    hero_cards:             list[str]       = field(default_factory=list)
    hero_cards_confidence:  float           = 0.0

    # -- Community cards -----------------------------------------------------
    board:                  list[str]       = field(default_factory=list)
    board_confidence:       float           = 0.0

    # -- Street --------------------------------------------------------------
    street:                 str | None      = None
    street_confidence:      float           = 0.0

    # -- Pot / bet -----------------------------------------------------------
    pot:                    float | None    = None
    pot_confidence:         float           = 0.0

    call_amount:            float | None    = None
    call_amount_confidence: float           = 0.0

    current_bet:            float | None    = None
    current_bet_confidence: float           = 0.0

    # -- Stacks --------------------------------------------------------------
    hero_stack:             float | None    = None
    hero_stack_confidence:  float           = 0.0
    hero_seat:              int | None      = None

    # -- Positions -----------------------------------------------------------
    dealer_position:                int | None  = None
    dealer_position_confidence:     float       = 0.0

    small_blind_position:           int | None  = None
    small_blind_position_confidence: float      = 0.0

    big_blind_position:             int | None  = None
    big_blind_position_confidence:  float       = 0.0

    # -- Blinds --------------------------------------------------------------
    small_blind_amount:     float | None    = None
    big_blind_amount:       float | None    = None
    blinds_confidence:      float           = 0.0

    # -- Players -------------------------------------------------------------
    player_count:           int | None      = None
    acting_seat:            int | None      = None
    players:                list[PlayerState] = field(default_factory=list)

    # -- Special states ------------------------------------------------------
    all_in_seats:           list[int]       = field(default_factory=list)
    folded_seats:           list[int]       = field(default_factory=list)
    empty_seats:            list[int]       = field(default_factory=list)

    # -- Meta ----------------------------------------------------------------
    game_type:              str | None      = None   # "cash" | "tournament"
    game_type_confidence:   float           = 0.0
    table_name:             str | None      = None
    timer:                  TimerState | None = None

    # -- Overall quality -----------------------------------------------------
    overall_confidence:     float           = 0.0

    # -- Validation metadata (not sent to the poker engine) -----------------
    validation_warnings:    list[str]       = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VisionGameState":
        """
        Build a VisionGameState from a validated raw dict.

        Unknown keys are silently ignored for forward compatibility.
        """
        # Parse nested players
        raw_players = data.get("players") or []
        players = [
            PlayerState(
                seat             = p.get("seat", 0),
                name             = p.get("name"),
                stack            = p.get("stack"),
                stack_confidence = float(p.get("stack_confidence") or 0.0),
                status           = p.get("status", "active"),
                current_bet      = p.get("current_bet"),
                is_hero          = bool(p.get("is_hero", False)),
            )
            for p in raw_players
            if isinstance(p, dict)
        ]

        # Parse optional timer
        raw_timer = data.get("timers")
        timer = None
        if isinstance(raw_timer, dict):
            try:
                timer = TimerState(
                    seat              = int(raw_timer.get("seat", 0)),
                    seconds_remaining = float(raw_timer.get("seconds_remaining", 0)),
                )
            except (TypeError, ValueError):
                timer = None

        return cls(
            hero_cards             = data.get("hero_cards") or [],
            hero_cards_confidence  = float(data.get("hero_cards_confidence") or 0.0),
            board                  = data.get("board") or [],
            board_confidence       = float(data.get("board_confidence") or 0.0),
            street                 = data.get("street"),
            street_confidence      = float(data.get("street_confidence") or 0.0),
            pot                    = data.get("pot"),
            pot_confidence         = float(data.get("pot_confidence") or 0.0),
            call_amount            = data.get("call_amount"),
            call_amount_confidence = float(data.get("call_amount_confidence") or 0.0),
            current_bet            = data.get("current_bet"),
            current_bet_confidence = float(data.get("current_bet_confidence") or 0.0),
            hero_stack             = data.get("hero_stack"),
            hero_stack_confidence  = float(data.get("hero_stack_confidence") or 0.0),
            hero_seat              = data.get("hero_seat"),
            dealer_position        = data.get("dealer_position"),
            dealer_position_confidence       = float(data.get("dealer_position_confidence") or 0.0),
            small_blind_position             = data.get("small_blind_position"),
            small_blind_position_confidence  = float(data.get("small_blind_position_confidence") or 0.0),
            big_blind_position               = data.get("big_blind_position"),
            big_blind_position_confidence    = float(data.get("big_blind_position_confidence") or 0.0),
            small_blind_amount     = data.get("small_blind_amount"),
            big_blind_amount       = data.get("big_blind_amount"),
            blinds_confidence      = float(data.get("blinds_confidence") or 0.0),
            player_count           = data.get("player_count"),
            acting_seat            = data.get("acting_seat"),
            players                = players,
            all_in_seats           = list(data.get("all_in_seats") or []),
            folded_seats           = list(data.get("folded_seats") or []),
            empty_seats            = list(data.get("empty_seats") or []),
            game_type              = data.get("game_type"),
            game_type_confidence   = float(data.get("game_type_confidence") or 0.0),
            table_name             = data.get("table_name"),
            timer                  = timer,
            overall_confidence     = float(data.get("overall_confidence") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "hero_cards":                     self.hero_cards,
            "hero_cards_confidence":          self.hero_cards_confidence,
            "board":                          self.board,
            "board_confidence":               self.board_confidence,
            "street":                         self.street,
            "street_confidence":              self.street_confidence,
            "pot":                            self.pot,
            "pot_confidence":                 self.pot_confidence,
            "call_amount":                    self.call_amount,
            "call_amount_confidence":         self.call_amount_confidence,
            "current_bet":                    self.current_bet,
            "current_bet_confidence":         self.current_bet_confidence,
            "hero_stack":                     self.hero_stack,
            "hero_stack_confidence":          self.hero_stack_confidence,
            "hero_seat":                      self.hero_seat,
            "dealer_position":                self.dealer_position,
            "dealer_position_confidence":     self.dealer_position_confidence,
            "small_blind_position":           self.small_blind_position,
            "small_blind_position_confidence": self.small_blind_position_confidence,
            "big_blind_position":             self.big_blind_position,
            "big_blind_position_confidence":  self.big_blind_position_confidence,
            "small_blind_amount":             self.small_blind_amount,
            "big_blind_amount":              self.big_blind_amount,
            "blinds_confidence":             self.blinds_confidence,
            "player_count":                  self.player_count,
            "acting_seat":                   self.acting_seat,
            "players": [
                {
                    "seat":             p.seat,
                    "name":             p.name,
                    "stack":            p.stack,
                    "stack_confidence": p.stack_confidence,
                    "status":           p.status,
                    "current_bet":      p.current_bet,
                    "is_hero":          p.is_hero,
                }
                for p in self.players
            ],
            "all_in_seats":          self.all_in_seats,
            "folded_seats":          self.folded_seats,
            "empty_seats":           self.empty_seats,
            "game_type":             self.game_type,
            "game_type_confidence":  self.game_type_confidence,
            "table_name":            self.table_name,
            "timer": {
                "seat":               self.timer.seat,
                "seconds_remaining":  self.timer.seconds_remaining,
            } if self.timer else None,
            "overall_confidence":    self.overall_confidence,
            "validation_warnings":   self.validation_warnings,
        }
