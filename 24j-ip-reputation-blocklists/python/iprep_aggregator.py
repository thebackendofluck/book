#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24j, IP Reputation and Blocklist Integration for iGaming Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
iprep_aggregator.py — Multi-source aggregation with consensus scoring.

Conflict resolution logic for merging IP reputation entries from
multiple threat intelligence feeds. Applies consensus bonus scoring
when an IP appears in multiple sources.

This module is extracted from the aggregation section of the iprep pipeline.
"""

from typing import List, Tuple


def compute_consensus_score(entries_for_ip: list) -> tuple:
    """
    Given all RepEntries for a single IP from different sources,
    return (category, final_score) applying consensus bonus.
    """
    if not entries_for_ip:
        raise ValueError("Empty entry list")

    # Sort by score descending
    sorted_entries = sorted(entries_for_ip, key=lambda e: e.score, reverse=True)

    best_entry = sorted_entries[0]
    base_score = best_entry.score
    source_count = len(set(e.source for e in entries_for_ip))

    # Consensus bonus: +10 for 3+ sources, +5 for 2 sources
    if source_count >= 3:
        bonus = 10
    elif source_count == 2:
        bonus = 5
    else:
        bonus = 0

    final_score = min(127, base_score + bonus)

    return best_entry.category, final_score
