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
gameservice.suppliers.relax.config — Relax Gaming Configuration
================================================================
"""

from __future__ import annotations

from pydantic import Field

from acmetocasino.gameservice.suppliers.settings import SupplierConfig


class RelaxConfig(SupplierConfig):
    """Relax Gaming configuration.

    Attributes
    ----------
    partner_id:
        Relax Gaming partner/operator identifier.
    environment:
        ``"production"`` or ``"staging"``.
    silver_bullet_enabled:
        Whether Silver Bullet (partner studio) content is enabled.
    """

    partner_id: str = Field(default="", description="Relax Gaming partner ID.")
    environment: str = Field(default="staging", description="production or staging.")
    silver_bullet_enabled: bool = Field(
        default=True,
        description="Whether Silver Bullet partner studio content is enabled.",
    )


__all__ = ["RelaxConfig"]
