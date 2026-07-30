# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
gameservice.models.launch_request — Game Launch Parameters
===========================================================

A :class:`LaunchRequest` encapsulates everything the platform needs to
construct a game-launch URL and validate that the player is eligible to
play the requested game.

The object is constructed by the API layer from the incoming HTTP request and
validated before any supplier communication occurs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from acmetocasino.gameservice.models.enums import GameMode
from acmetocasino.gameservice.models.player_context import PlayerContext


class LaunchRequest(BaseModel):
    """Validated parameters for launching a game session.

    Attributes
    ----------
    player:
        The authenticated player context, including jurisdiction and currency.
    game_id:
        The platform's internal identifier for the game.  This maps to a
        specific game in the :class:`~acmetocasino.gameservice.registry.GameRegistry`.
    supplier_id:
        The supplier that hosts the game (e.g. ``"netent"``, ``"pragmatic"``).
        Must match the ``game_id``'s registered supplier.
    mode:
        Whether to launch in real-money, demo, or free-round mode.
    channel:
        The delivery channel (``"web"``, ``"mobile"``, ``"native-app"``).
        Suppliers may return different launch URLs per channel.
    return_url:
        Optional URL the player should be redirected to after leaving the
        game lobby.  Passed verbatim to the supplier.
    extra_params:
        Arbitrary key-value pairs forwarded to the supplier adapter.  Useful
        for supplier-specific extensions without polluting the core model.
    """

    model_config = {"frozen": True}

    player: PlayerContext
    game_id: str = Field(..., min_length=1, description="Platform game identifier.")
    supplier_id: str = Field(..., min_length=1, description="Supplier identifier.")
    mode: GameMode = Field(
        default=GameMode.REAL_MONEY,
        description="Session funding mode.",
    )
    channel: str = Field(
        default="web",
        description='Delivery channel: "web", "mobile", or "native-app".',
    )
    return_url: str | None = Field(
        default=None,
        description="URL to redirect the player to after leaving the game.",
    )
    extra_params: dict[str, str] = Field(
        default_factory=dict,
        description="Supplier-specific extension parameters.",
    )

    @field_validator("channel")
    @classmethod
    def _valid_channel(cls, v: str) -> str:
        allowed = {"web", "mobile", "native-app"}
        if v not in allowed:
            raise ValueError(f"channel must be one of {allowed!r}, got {v!r}")
        return v

    def is_real_money(self) -> bool:
        """Return ``True`` if this is a real-money session."""
        return self.mode == GameMode.REAL_MONEY

    def __repr__(self) -> str:
        return (
            f"LaunchRequest(game={self.game_id!r}, "
            f"supplier={self.supplier_id!r}, "
            f"mode={self.mode.value!r}, "
            f"player={self.player.player_id!r})"
        )


__all__ = ["LaunchRequest"]
