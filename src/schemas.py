"""Typed schemas for validating LLM output before it drives any decision.

The strategy prompt asks the model for a strict JSON block. Validating that
block with Pydantic means a malformed or hallucinated plan fails loudly at the
boundary instead of silently producing a bad roster move.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TransactionType = Literal["add", "drop", "activate", "move"]


class Transaction(BaseModel):
    """A single proposed roster action."""

    model_config = ConfigDict(extra="ignore")

    type: TransactionType
    player: str = Field(..., min_length=1, description="Player to add/activate/move")
    drop: Optional[str] = Field(
        default=None, description="Player to drop or move to IL, if any"
    )
    player_id: Optional[str] = None
    drop_id: Optional[str] = None
    instruction: Optional[str] = None
    reason: Optional[str] = None

    @field_validator("drop", "player_id", "drop_id", "instruction", "reason", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        """Normalise the many ways a model expresses "no value"."""
        if isinstance(value, str) and value.strip().lower() in {
            "", "null", "none", "n/a", "-", "na",
        }:
            return None
        return value

    @field_validator("player", mode="before")
    @classmethod
    def _strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class TransactionPlan(BaseModel):
    """The full set of transactions proposed for this week.

    Used for whole-plan validation in tests. The strategy node validates
    entry-by-entry so a single malformed transaction cannot void a good plan.
    """

    model_config = ConfigDict(extra="ignore")

    transactions: List[Transaction] = Field(default_factory=list)

