# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Mathematics Engine for iGaming
=======================================
Chapter 13: Casino Mathematics and Game Economy

Comprehensive casino mathematics library providing:
- House edge and RTP calculations for roulette (European/American), blackjack,
  baccarat, and slot machines with configurable paytables
- Progressive jackpot mathematics including optimal sizing and risk analysis
- Bonus mathematics with wagering completion probability and operator cost
- Value at Risk (VaR) and Conditional Value at Risk (CVaR) calculations
- Volatility analysis with EWMA time-varying volatility modeling
- Game selection optimization based on player profile and risk tolerance
- Portfolio-level risk management with correlation and diversification analysis

Supported Game Types:
    ROULETTE:       European (2.70% house edge) and American (5.26%)
    BLACKJACK:      Standard, Atlantic City, European, and 6:5 variants
    BACCARAT:       8-deck banker bet (1.06% house edge)
    SLOTS:          Configurable reel/paytable with RTP targeting

Dependencies:
    pip install numpy pandas scipy matplotlib seaborn redis asyncpg
"""

# Comprehensive casino mathematics library
import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import minimize
import matplotlib.pyplot as plt  # ty:ignore[unresolved-import]
import seaborn as sns  # ty:ignore[unresolved-import]
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging
import json
from datetime import datetime, timedelta
import asyncio
import redis.asyncio as redis
import asyncpg  # ty:ignore[unresolved-import]

class GameType(Enum):
    ROULETTE = "roulette"
    BLACKJACK = "blackjack"
    SLOTS = "slots"
    BACCARAT = "baccarat"
    CRAPS = "craps"
    POKER = "poker"
    SPORTS_BETTING = "sports_betting"

class VolatilityLevel(Enum):
    LOW = "low"      # σ < 2
    MEDIUM = "medium" # 2 ≤ σ < 5
    HIGH = "high"    # 5 ≤ σ < 10
    VERY_HIGH = "very_high" # σ ≥ 10

@dataclass
class GameMathematics:
    game_id: str
    game_type: GameType
    house_edge: float
    rtp: float
    volatility_index: float
    max_payout: float
    probability_distribution: Dict[str, float]
    expected_value: float
    standard_deviation: float
    confidence_intervals: Dict[str, Tuple[float, float]]

class CasinoMathematicsEngine:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Mathematical constants
        self.Z_SCORES = {
            0.90: 1.645,
            0.95: 1.960,
            0.99: 2.576
        }

    def calculate_roulette_mathematics(self, roulette_type: str = "european") -> GameMathematics:
        """Calculate complete mathematics for roulette games"""

        if roulette_type == "european":
            # European roulette: 37 numbers (0-36)
            total_numbers = 37
            house_edge_single = 1/37  # 2.70%

            # Probability distributions for different bets
            probabilities = {
                'single_number': 1/total_numbers,
                'split': 2/total_numbers,
                'street': 3/total_numbers,
                'corner': 4/total_numbers,
                'six_line': 6/total_numbers,
                'dozen': 12/total_numbers,
                'column': 12/total_numbers,
                'red_black': 18/total_numbers,
                'even_odd': 18/total_numbers,
                'low_high': 18/total_numbers
            }

            # Payouts (standard roulette payouts)
            payouts = {
                'single_number': 35,
                'split': 17,
                'street': 11,
                'corner': 8,
                'six_line': 5,
                'dozen': 2,
                'column': 2,
                'red_black': 1,
                'even_odd': 1,
                'low_high': 1
            }

            # Calculate expected value for each bet type
            expected_values = {}
            for bet_type in probabilities:
                prob_win = probabilities[bet_type]
                payout = payouts[bet_type]
                expected_values[bet_type] = (prob_win * payout) + ((1 - prob_win) * -1)

            # Overall house edge and RTP
            house_edge = abs(house_edge_single)
            rtp = 1 - house_edge

            # Calculate volatility (standard deviation) for single number bet
            prob_win = probabilities['single_number']
            payout = payouts['single_number']
            variance = (prob_win * (payout - expected_values['single_number'])**2 +
                       (1 - prob_win) * (-1 - expected_values['single_number'])**2)
            std_deviation = np.sqrt(variance)

            # Confidence intervals for different sample sizes
            confidence_intervals = {}
            for confidence, z_score in self.Z_SCORES.items():
                margin_error = z_score * (std_deviation / np.sqrt(1000))  # For 1000 bets
                confidence_intervals[f"{confidence}"] = (
                    expected_values['single_number'] - margin_error,
                    expected_values['single_number'] + margin_error
                )

            return GameMathematics(
                game_id=f"roulette_{roulette_type}",
                game_type=GameType.ROULETTE,
                house_edge=house_edge,
                rtp=rtp,
                volatility_index=std_deviation,
                max_payout=35,
                probability_distribution=probabilities,
                expected_value=expected_values['single_number'],
                standard_deviation=std_deviation,
                confidence_intervals=confidence_intervals
            )

        elif roulette_type == "american":
            # American roulette: 38 numbers (0, 00, 1-36)
            total_numbers = 38
            house_edge_single = 2/38  # 5.26%

            # Similar calculations with adjusted probabilities
            probabilities = {
                'single_number': 1/total_numbers,
                'split': 2/total_numbers,
                'street': 3/total_numbers,
                'corner': 4/total_numbers,
                'six_line': 6/total_numbers,
                'dozen': 12/total_numbers,
                'column': 12/total_numbers,
                'red_black': 18/total_numbers,
                'even_odd': 18/total_numbers,
                'low_high': 18/total_numbers
            }

            # Higher house edge affects all calculations
            rtp = 1 - house_edge_single

            return GameMathematics(
                game_id=f"roulette_{roulette_type}",
                game_type=GameType.ROULETTE,
                house_edge=house_edge_single,
                rtp=rtp,
                volatility_index=np.sqrt(35.0),  # Higher volatility
                max_payout=35,
                probability_distribution=probabilities,
                expected_value=-house_edge_single,
                standard_deviation=np.sqrt(35.0),
                confidence_intervals={}
            )

        raise ValueError(f"Unsupported roulette type: {roulette_type}")

    def calculate_slot_mathematics(self,
                                 reels: List[List[str]],
                                 paytable: Dict[str, int],
                                 rtp_target: float = 0.96) -> GameMathematics:
        """Calculate mathematics for slot games with configurable RTP"""

        # Calculate total possible combinations
        total_combinations = 1
        for reel in reels:
            total_combinations *= len(reel)

        # Analyze all possible combinations
        combination_results = []
        payout_distribution = []

        for combination_index in range(total_combinations):
            # Convert index to reel positions
            positions = []
            temp_index = combination_index

            for reel in reels:
                positions.append(temp_index % len(reel))
                temp_index //= len(reel)

            # Get symbols at positions
            symbols = [reel[pos] for reel, pos in zip(reels, positions)]

            # Check for winning combinations
            payout = self._calculate_slot_payout(symbols, paytable)  # ty:ignore[unresolved-attribute]
            combination_results.append({
                'symbols': symbols,
                'payout': payout
            })
            payout_distribution.append(payout)

        # Calculate probabilities and expected value
        total_payout = sum(payout_distribution)
        expected_payout = total_payout / total_combinations
        house_edge = 1 - (expected_payout / 1)  # Assuming $1 bet
        actual_rtp = 1 - house_edge

        # Adjust for target RTP if needed
        if abs(actual_rtp - rtp_target) > 0.001:
            # Adjust paytable to meet target RTP
            adjusted_paytable = self._adjust_slot_paytable(
                paytable,
                combination_results,
                rtp_target
            )

            # Recalculate with adjusted paytable
            adjusted_payouts = []
            for result in combination_results:
                adjusted_payout = self._calculate_slot_payout(  # ty:ignore[unresolved-attribute]
                    result['symbols'],
                    adjusted_paytable
                )
                adjusted_payouts.append(adjusted_payout)

            total_payout = sum(adjusted_payouts)
            expected_payout = total_payout / total_combinations
            house_edge = 1 - (expected_payout / 1)
            actual_rtp = 1 - house_edge
            payout_distribution = adjusted_payouts

        # Calculate volatility
        variance = np.var(payout_distribution)
        std_deviation = np.sqrt(variance)
        volatility_index = std_deviation / expected_payout if expected_payout > 0 else 0

        # Determine volatility level
        if volatility_index < 2:
            volatility_level = VolatilityLevel.LOW
        elif volatility_index < 5:
            volatility_level = VolatilityLevel.MEDIUM
        elif volatility_index < 10:
            volatility_level = VolatilityLevel.HIGH
        else:
            volatility_level = VolatilityLevel.VERY_HIGH

        # Calculate hit frequency
        winning_combinations = sum(1 for payout in payout_distribution if payout > 0)
        hit_frequency = winning_combinations / total_combinations

        # Build probability distribution
        probability_distribution = {
            'total_combinations': total_combinations,
            'winning_combinations': winning_combinations,
            'hit_frequency': hit_frequency,
            'volatility_index': volatility_index,
            'volatility_level': volatility_level.value
        }

        return GameMathematics(
            game_id=f"slot_{len(reels)}reels",
            game_type=GameType.SLOTS,
            house_edge=house_edge,
            rtp=actual_rtp,
            volatility_index=volatility_index,
            max_payout=max(payout_distribution),
            probability_distribution=probability_distribution,  # ty:ignore[invalid-argument-type]
            expected_value=expected_payout - 1,  # Net expected value
            standard_deviation=std_deviation,
            confidence_intervals={}
        )

    def calculate_blackjack_mathematics(self, rules_variant: str = "standard") -> GameMathematics:
        """Calculate mathematics for blackjack with optimal strategy"""

        # Define rule variations and their effects
        rule_effects = {
            'dealer_stands_soft_17': -0.002,  # Reduces house edge
            'double_after_split': -0.0014,
            'resplit_aces': -0.0008,
            'late_surrender': -0.0008,
            'blackjack_pays_6to5': 0.014,  # Increases house edge significantly
            'dealer_hits_soft_17': 0.002,
            'no_double_after_split': 0.0014,
            'no_resplit': 0.0005
        }

        # Base house edge with optimal player strategy
        base_house_edge = 0.005  # 0.5% with perfect basic strategy

        # Apply rule variations
        if rules_variant == "standard":
            # Standard Vegas rules
            house_edge = base_house_edge + rule_effects['dealer_stands_soft_17']
        elif rules_variant == "atlantic_city":
            # Atlantic City rules
            house_edge = (base_house_edge +
                         rule_effects['dealer_stands_soft_17'] +
                         rule_effects['late_surrender'] +
                         rule_effects['double_after_split'])
        elif rules_variant == "european":
            # European rules (no hole card)
            house_edge = base_house_edge + 0.011  # Higher house edge
        elif rules_variant == "6to5":
            # 6:5 blackjack (avoid if possible)
            house_edge = (base_house_edge +
                         rule_effects['blackjack_pays_6to5'] +
                         rule_effects['dealer_hits_soft_17'])
        else:
            house_edge = base_house_edge

        # RTP calculation
        rtp = 1 - house_edge

        # Calculate variance for blackjack
        # Blackjack has much lower variance than most casino games
        blackjack_variance = 1.3  # Standard deviation of ~1.14

        # Calculate probabilities for different outcomes
        probabilities = {
            'blackjack': 0.048,  # ~4.8% chance of blackjack
            'win': 0.43,         # ~43% win rate including blackjacks
            'loss': 0.48,        # ~48% loss rate
            'push': 0.09         # ~9% push rate
        }

        # Expected value per hand
        expected_value = -house_edge  # Negative house edge

        return GameMathematics(
            game_id=f"blackjack_{rules_variant}",
            game_type=GameType.BLACKJACK,
            house_edge=house_edge,
            rtp=rtp,
            volatility_index=np.sqrt(blackjack_variance),
            max_payout=1.5,  # Blackjack pays 3:2
            probability_distribution=probabilities,
            expected_value=expected_value,
            standard_deviation=np.sqrt(blackjack_variance),
            confidence_intervals={}
        )

    def calculate_baccarat_mathematics(self) -> GameMathematics:
        """Calculate mathematics for baccarat"""

        # Baccarat probabilities with 8 decks
        probabilities = {
            'banker_win': 0.458597,
            'player_win': 0.446247,
            'tie': 0.095156
        }

        # House edges for different bets
        house_edges = {
            'banker': 0.010579,  # 1.06% (after 5% commission)
            'player': 0.012351,  # 1.24%
            'tie': 0.1436        # 14.36% (avoid this bet!)
        }

        # RTP for banker bet (best bet in baccarat)
        rtp_banker = 1 - house_edges['banker']

        # Calculate variance for banker bet
        # Win: +0.95 (after commission), Loss: -1, Tie: 0
        outcomes = [0.95, -1, 0]
        outcome_probs = [probabilities['banker_win'],
                        probabilities['player_win'] + probabilities['tie'],
                        0]  # Tie is a push for banker bet

        expected_value = sum(outcome * prob for outcome, prob in zip(outcomes, outcome_probs))
        variance = sum(prob * (outcome - expected_value)**2 for outcome, prob in zip(outcomes, outcome_probs))
        std_deviation = np.sqrt(variance)

        return GameMathematics(
            game_id="baccarat_banker_bet",
            game_type=GameType.BACCARAT,
            house_edge=house_edges['banker'],
            rtp=rtp_banker,
            volatility_index=std_deviation,
            max_payout=0.95,  # After commission
            probability_distribution=probabilities,
            expected_value=expected_value,
            standard_deviation=std_deviation,
            confidence_intervals={}
        )

    def _adjust_slot_paytable(self, original_paytable: Dict[str, int],
                            combination_results: List[Dict],
                            target_rtp: float) -> Dict[str, int]:
        """Adjust slot paytable to achieve target RTP"""

        # Calculate current RTP
        total_combinations = len(combination_results)
        current_payout = sum(result['payout'] for result in combination_results)
        current_rtp = current_payout / total_combinations

        # Calculate adjustment factor
        adjustment_factor = target_rtp / current_rtp

        # Adjust payouts while maintaining integer values
        adjusted_paytable = {}
        for symbol, original_payout in original_paytable.items():
            adjusted_payout = int(original_payout * adjustment_factor)
            # Ensure minimum payout of 1 for winning combinations
            if original_payout > 0:
                adjusted_payout = max(1, adjusted_payout)
            adjusted_paytable[symbol] = adjusted_payout

        return adjusted_paytable

    def calculate_progressive_jackpot_mathematics(self,
                                                seed_amount: float,
                                                contribution_rate: float,
                                                expected_hit_frequency: float,
                                                current_jackpot: float,
                                                number_of_machines: int = 1) -> Dict:
        """Calculate mathematics for progressive jackpot systems"""

        # Expected value of jackpot
        jackpot_ev = current_jackpot * expected_hit_frequency

        # Contribution to jackpot per bet
        contribution_per_bet = contribution_rate

        # Time to expected hit (in bets)
        expected_bets_to_hit = 1 / expected_hit_frequency

        # Variance of jackpot timing
        jackpot_variance = (1 - expected_hit_frequency) / (expected_hit_frequency ** 2)
        jackpot_std_dev = np.sqrt(jackpot_variance)

        # Risk analysis
        # Probability of jackpot being hit before reaching certain thresholds
        threshold_analysis = {}
        for threshold in [0.5, 0.75, 1.0, 1.5, 2.0]:  # Multiples of seed amount
            threshold_amount = seed_amount * threshold
            if current_jackpot < threshold_amount:
                # Probability of reaching this threshold before being hit
                prob_reach_threshold = np.exp(-(threshold_amount - current_jackpot) /
                                              (contribution_rate * expected_bets_to_hit))
                threshold_analysis[f"{threshold}x_seed"] = {
                    'threshold_amount': threshold_amount,
                    'probability_reach': prob_reach_threshold,
                    'expected_bets': (threshold_amount - current_jackpot) / contribution_rate
                }

        # Optimal jackpot size analysis
        # Balance between player attraction and operator risk
        optimal_size = self._calculate_optimal_jackpot_size(
            seed_amount,
            contribution_rate,
            expected_hit_frequency,
            number_of_machines
        )

        return {
            'seed_amount': seed_amount,
            'current_jackpot': current_jackpot,
            'contribution_rate': contribution_rate,
            'expected_hit_frequency': expected_hit_frequency,
            'expected_value': jackpot_ev,
            'expected_bets_to_hit': expected_bets_to_hit,
            'jackpot_variance': jackpot_variance,
            'jackpot_standard_deviation': jackpot_std_dev,
            'threshold_analysis': threshold_analysis,
            'optimal_jackpot_size': optimal_size,
            'risk_metrics': {
                'probability_of_loss': expected_hit_frequency,
                'expected_loss_amount': current_jackpot - seed_amount,
                'maximum_exposure': current_jackpot
            }
        }

    def _calculate_optimal_jackpot_size(self, seed: float, contribution_rate: float,
                                      hit_freq: float, machines: int) -> Dict:
        """Calculate optimal jackpot size balancing attraction and risk"""

        # Player attraction function (diminishing returns)
        def attraction_function(jackpot_size):
            # Attraction increases with jackpot but at decreasing rate
            return np.log(1 + jackpot_size / seed) / np.log(2)

        # Operator risk function (increasing with jackpot size)
        def risk_function(jackpot_size):
            # Risk increases exponentially with jackpot size
            return (jackpot_size / seed) ** 2

        # Optimization function to maximize
        def objective(jackpot_size):
            attraction = attraction_function(jackpot_size)
            risk = risk_function(jackpot_size)
            # Maximize attraction while minimizing risk
            return attraction - 0.1 * risk

        # Find optimal jackpot size
        from scipy.optimize import minimize_scalar
        result = minimize_scalar(lambda x: -objective(x), bounds=(seed, seed * 10), method='bounded')

        optimal_size = result.x
        optimal_attraction = attraction_function(optimal_size)
        optimal_risk = risk_function(optimal_size)

        return {
            'optimal_size': optimal_size,
            'attraction_score': optimal_attraction,
            'risk_score': optimal_risk,
            'net_benefit': objective(optimal_size)
        }

    def calculate_bonus_mathematics(self,
                                  bonus_amount: float,
                                  wagering_requirement: int,
                                  game_contributions: Dict[str, float],
                                  house_edges: Dict[str, float],
                                  expected_bet_size: float = 10.0) -> Dict:
        """Calculate expected value and cost of bonus offers"""

        # Calculate expected wagering
        total_wagering_needed = bonus_amount * wagering_requirement

        # Calculate expected loss during wagering
        expected_loss = 0

        # Weighted average house edge based on game contributions
        weighted_house_edge = 0
        total_contribution = 0

        for game_type, contribution in game_contributions.items():
            if game_type in house_edges:
                weighted_house_edge += house_edges[game_type] * contribution
                total_contribution += contribution

        # Normalize contributions
        if total_contribution > 0:
            weighted_house_edge /= total_contribution

        # Expected loss from wagering
        expected_loss = total_wagering_needed * weighted_house_edge

        # Expected value of bonus
        expected_value = bonus_amount - expected_loss

        # Calculate probability of completing wagering
        # Using normal approximation to binomial distribution
        num_bets = int(total_wagering_needed / expected_bet_size)
        expected_winnings = num_bets * expected_bet_size * (1 - weighted_house_edge)
        variance = num_bets * (expected_bet_size ** 2) * weighted_house_edge * (1 - weighted_house_edge)
        std_dev = np.sqrt(variance)

        # Probability of having enough balance to complete wagering
        probability_completion = self._calculate_wagering_completion_probability(
            bonus_amount,
            expected_loss,
            std_dev
        )

        # Calculate bonus cost to operator
        bonus_cost = expected_value * probability_completion

        # Risk analysis
        risk_analysis = self._analyze_bonus_risk(
            bonus_amount,
            wagering_requirement,
            weighted_house_edge,
            num_bets
        )

        return {
            'bonus_amount': bonus_amount,
            'wagering_requirement': wagering_requirement,
            'total_wagering_needed': total_wagering_needed,
            'expected_loss': expected_loss,
            'expected_value': expected_value,
            'probability_completion': probability_completion,
            'bonus_cost': bonus_cost,
            'weighted_house_edge': weighted_house_edge,
            'num_bets': num_bets,
            'risk_analysis': risk_analysis,
            'profitability_metrics': {
                'roi': expected_value / bonus_amount if bonus_amount > 0 else 0,
                'break_even_probability': probability_completion,
                'expected_profit': -expected_value  # Negative because it's a cost
            }
        }

    def _calculate_wagering_completion_probability(self,
                                                 starting_balance: float,
                                                 expected_loss: float,
                                                 std_dev: float) -> float:
        """Calculate probability of completing wagering without busting"""

        # Probability of having positive balance after wagering
        # Using normal distribution approximation

        mean_final_balance = starting_balance - expected_loss

        # Probability of final balance > 0
        if std_dev > 0:
            z_score = (0 - mean_final_balance) / std_dev
            probability_positive = 1 - stats.norm.cdf(z_score)
        else:
            probability_positive = 1.0 if mean_final_balance > 0 else 0.0

        return probability_positive

    def _analyze_bonus_risk(self, bonus_amount: float, wagering_req: int,
                          house_edge: float, num_bets: int) -> Dict:
        """Analyze risk associated with bonus offer"""

        # Calculate variance of outcomes
        bet_size = (bonus_amount * wagering_req) / num_bets
        variance_per_bet = (bet_size ** 2) * house_edge * (1 - house_edge)
        total_variance = num_bets * variance_per_bet
        total_std_dev = np.sqrt(total_variance)

        # Value at Risk (VaR) calculations
        var_95 = self._calculate_var(bonus_amount, house_edge, total_std_dev, 0.95)
        var_99 = self._calculate_var(bonus_amount, house_edge, total_std_dev, 0.99)

        # Conditional Value at Risk (CVaR)
        cvar_95 = self._calculate_cvar(bonus_amount, house_edge, total_std_dev, 0.95)
        cvar_99 = self._calculate_cvar(bonus_amount, house_edge, total_std_dev, 0.99)

        # Maximum exposure
        max_exposure = bonus_amount + (bonus_amount * wagering_req * house_edge * 2)  # 2x buffer

        return {
            'var_95': var_95,
            'var_99': var_99,
            'cvar_95': cvar_95,
            'cvar_99': cvar_99,
            'maximum_exposure': max_exposure,
            'volatility': total_std_dev,
            'risk_adjusted_return': (bonus_amount * 0.1) / total_std_dev if total_std_dev > 0 else 0
        }

    def _calculate_var(self, bonus_amount: float, expected_loss: float,
                      std_dev: float, confidence: float) -> float:
        """Calculate Value at Risk"""
        z_score = stats.norm.ppf(confidence)
        var = expected_loss + z_score * std_dev
        return min(var, bonus_amount * 3)  # Cap at 3x bonus amount

    def _calculate_cvar(self, bonus_amount: float, expected_loss: float,
                       std_dev: float, confidence: float) -> float:
        """Calculate Conditional Value at Risk (Expected Shortfall)"""
        z_score = stats.norm.ppf(confidence)
        pdf_z = stats.norm.pdf(z_score)
        cvar = expected_loss + std_dev * pdf_z / (1 - confidence)
        return min(cvar, bonus_amount * 5)  # Cap at 5x bonus amount


class VolatilityAnalyzer:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.volatility_models = {}

    def calculate_volatility_index(self, game_data: Dict) -> float:
        """Calculate comprehensive volatility index for games"""

        # Get historical game outcomes
        outcomes = game_data.get('outcomes', [])
        if len(outcomes) < 100:
            return 0.0  # Insufficient data

        # Calculate basic volatility metrics
        returns = [outcome['payout'] - 1 for outcome in outcomes]  # Net returns
        volatility_metrics = {
            'standard_deviation': np.std(returns),
            'mean_absolute_deviation': np.mean(np.abs(returns - np.mean(returns))),
            'max_drawdown': self._calculate_max_drawdown(returns),  # ty:ignore[unresolved-attribute]
            'skewness': stats.skew(returns),
            'kurtosis': stats.kurtosis(returns),
            'var_95': np.percentile(returns, 5),
            'var_99': np.percentile(returns, 1)
        }

        # Calculate time-based volatility (GARCH-like model)
        time_volatility = self._calculate_time_volatility(returns)

        # Combine metrics into comprehensive volatility index
        weights = {
            'standard_deviation': 0.3,
            'mean_absolute_deviation': 0.2,
            'max_drawdown': 0.2,
            'var_95': 0.15,
            'time_volatility': 0.15
        }

        volatility_index = sum(
            volatility_metrics[metric] * weight
            for metric, weight in weights.items()
            if metric in volatility_metrics
        )

        return volatility_index

    def _calculate_time_volatility(self, returns: List[float]) -> float:
        """Calculate time-varying volatility using EWMA"""

        lambda_param = 0.94  # Decay factor (standard for EWMA)
        n = len(returns)

        if n < 2:
            return 0.0

        # Initialize with sample variance
        variances = [np.var(returns)]

        for i in range(1, n):
            # EWMA variance update
            new_variance = (lambda_param * variances[-1] +
                          (1 - lambda_param) * returns[i-1]**2)
            variances.append(new_variance)

        # Return latest volatility estimate
        return np.sqrt(variances[-1])

    def optimize_game_selection(self,
                              player_profile: Dict,
                              available_games: List[Dict],
                              bankroll: float,
                              session_duration: int,
                              risk_tolerance: str = "medium") -> List[Dict]:
        """Optimize game selection based on player profile and constraints"""

        # Define risk tolerance parameters
        risk_params = {
            'low': {'max_volatility': 2.0, 'min_rtp': 0.97, 'max_house_edge': 0.03},
            'medium': {'max_volatility': 5.0, 'min_rtp': 0.95, 'max_house_edge': 0.05},
            'high': {'max_volatility': 10.0, 'min_rtp': 0.92, 'max_house_edge': 0.08}
        }

        params = risk_params.get(risk_tolerance, risk_params['medium'])

        # Filter games based on basic criteria
        suitable_games = []
        for game in available_games:
            game_math = game.get('mathematics', {})

            # Check RTP requirement
            if game_math.get('rtp', 0) < params['min_rtp']:
                continue

            # Check house edge requirement
            if game_math.get('house_edge', 1) > params['max_house_edge']:
                continue

            # Check volatility requirement
            volatility = game_math.get('volatility_index', 0)
            if volatility > params['max_volatility']:
                continue

            suitable_games.append(game)

        if not suitable_games:
            return []  # No suitable games found

        # Calculate game scores based on multiple criteria
        game_scores = []
        for game in suitable_games:
            score = self._calculate_game_score(game, player_profile, bankroll, session_duration)  # ty:ignore[unresolved-attribute]
            game_scores.append({
                'game': game,
                'score': score,
                'reasoning': self._generate_game_selection_reasoning(game, player_profile, score)  # ty:ignore[unresolved-attribute]
            })

        # Sort by score and return top recommendations
        game_scores.sort(key=lambda x: x['score'], reverse=True)

        return game_scores[:10]  # Return top 10 recommendations

    def simulate_game_session(self, game_math: Dict, bankroll: float,
                            session_duration: int, bet_size: float) -> Dict:
        """Simulate a game session for risk assessment"""

        # Session parameters
        bets_per_hour = 60  # Average for most games
        total_bets = int(session_duration * bets_per_hour)

        # Game mathematics
        house_edge = game_math.get('house_edge', 0.05)
        volatility = game_math.get('volatility_index', 2.0)
        rtp = game_math.get('rtp', 0.95)

        # Simulate individual bets
        session_results = []
        current_bankroll = bankroll

        for bet in range(total_bets):
            # Simulate bet outcome
            # Using normal distribution approximation for simplicity
            expected_return = -house_edge * bet_size
            std_dev = bet_size * volatility

            # Generate random outcome
            outcome = np.random.normal(expected_return, std_dev)
            actual_return = max(-current_bankroll, outcome)  # Can't lose more than current bankroll

            current_bankroll += actual_return
            session_results.append({
                'bet_number': bet + 1,
                'outcome': actual_return,
                'bankroll_after': current_bankroll,
                'cumulative_return': sum(result['outcome'] for result in session_results)
            })

            # Stop if bankroll is depleted
            if current_bankroll <= 0:
                break

        # Calculate session statistics
        final_bankroll = current_bankroll
        total_return = final_bankroll - bankroll
        return_percentage = (total_return / bankroll) * 100

        # Calculate session metrics
        session_metrics = {
            'initial_bankroll': bankroll,
            'final_bankroll': final_bankroll,
            'total_return': total_return,
            'return_percentage': return_percentage,
            'bets_placed': len(session_results),
            'session_duration_hours': len(session_results) / bets_per_hour,
            'lowest_bankroll': min(result['bankroll_after'] for result in session_results),
            'highest_bankroll': max(result['bankroll_after'] for result in session_results),
            'volatility_experienced': np.std([result['outcome'] for result in session_results])
        }

        # Risk assessment
        risk_assessment = self._assess_session_risk(session_metrics, bankroll)  # ty:ignore[unresolved-attribute]
        session_metrics['risk_assessment'] = risk_assessment

        return {
            'session_results': session_results,
            'session_metrics': session_metrics,
            'risk_assessment': risk_assessment,
            'recommendations': self._generate_session_recommendations(session_metrics, game_math)  # ty:ignore[unresolved-attribute]
        }


class RiskManagementSystem:
    def __init__(self, redis_client: redis.Redis, db_pool: asyncpg.Pool):
        self.redis = redis_client
        self.db_pool = db_pool
        self.logger = logging.getLogger(__name__)

        # Risk limits configuration
        self.risk_limits = {
            'daily_exposure': 1000000,  # Maximum daily exposure across all games
            'hourly_exposure': 200000,  # Maximum hourly exposure
            'single_bet_limit': 50000,  # Maximum single bet
            'player_daily_limit': 100000,  # Maximum daily loss per player
            'game_max_exposure': 500000,  # Maximum exposure per game
            'progressive_jackpot_reserve': 2000000  # Reserve for progressive jackpots
        }

        # Risk metrics thresholds
        self.risk_thresholds = {
            'green': 0.5,   # 50% of limit
            'yellow': 0.75, # 75% of limit
            'red': 0.9,     # 90% of limit
            'critical': 1.0 # 100% of limit
        }

    async def monitor_real_time_risk(self) -> Dict:
        """Monitor real-time risk exposure across all operations"""

        current_time = datetime.now()

        # Get current risk metrics
        risk_metrics = await self._calculate_current_risk_metrics(current_time)  # ty:ignore[unresolved-attribute]

        # Check against limits
        risk_status = await self._evaluate_risk_status(risk_metrics)  # ty:ignore[unresolved-attribute]

        # Generate alerts if necessary
        if risk_status['overall_status'] in ['red', 'critical']:
            await self._trigger_risk_alert(risk_metrics, risk_status)  # ty:ignore[unresolved-attribute]

        # Store risk history
        await self._store_risk_history(risk_metrics, risk_status)  # ty:ignore[unresolved-attribute]

        return {
            'timestamp': current_time.isoformat(),
            'risk_metrics': risk_metrics,
            'risk_status': risk_status,
            'alerts_triggered': risk_status.get('alerts', []),
            'recommended_actions': self._generate_risk_recommendations(risk_status)  # ty:ignore[unresolved-attribute]
        }

    async def implement_dynamic_bet_limits(self, player_id: str, game_id: str,
                                         proposed_bet_amount: float) -> Dict:
        """Implement dynamic bet limits based on real-time risk assessment"""

        # Get player risk profile
        player_risk_profile = await self._get_player_risk_profile(player_id)  # ty:ignore[unresolved-attribute]

        # Get current game risk metrics
        game_risk_metrics = await self._get_game_risk_metrics(game_id)  # ty:ignore[unresolved-attribute]

        # Get overall system risk status
        system_risk_status = await self._get_system_risk_status()  # ty:ignore[unresolved-attribute]

        # Calculate dynamic limits
        dynamic_limits = await self._calculate_dynamic_limits(
            player_risk_profile,
            game_risk_metrics,
            system_risk_status,
            proposed_bet_amount
        )

        # Apply limits
        approved_bet_amount = min(proposed_bet_amount, dynamic_limits['max_allowed_bet'])

        # Log decision
        await self._log_bet_limit_decision(  # ty:ignore[unresolved-attribute]
            player_id,
            game_id,
            proposed_bet_amount,
            approved_bet_amount,
            dynamic_limits
        )

        return {
            'approved_bet_amount': approved_bet_amount,
            'original_bet_amount': proposed_bet_amount,
            'limit_factors': dynamic_limits['limiting_factors'],
            'risk_assessment': dynamic_limits['risk_assessment'],
            'recommendations': dynamic_limits['recommendations']
        }

    async def _calculate_dynamic_limits(self, player_profile: Dict, game_metrics: Dict,
                                      system_status: Dict, proposed_amount: float) -> Dict:
        """Calculate dynamic betting limits based on multiple risk factors"""

        limiting_factors = []
        risk_assessment = {}

        # Base limit from player profile
        base_limit = player_profile.get('approved_bet_limit', self.risk_limits['single_bet_limit'])

        # Adjust based on player risk score
        player_risk_score = player_profile.get('risk_score', 0)
        if player_risk_score > 70:
            base_limit *= 0.5
            limiting_factors.append('high_player_risk_score')
            risk_assessment['player_risk'] = 'high'
        elif player_risk_score > 50:
            base_limit *= 0.75
            limiting_factors.append('medium_player_risk_score')
            risk_assessment['player_risk'] = 'medium'
        else:
            risk_assessment['player_risk'] = 'low'

        # Adjust based on game volatility
        game_volatility = game_metrics.get('volatility_index', 0)
        if game_volatility > 8:
            base_limit *= 0.7
            limiting_factors.append('high_game_volatility')
            risk_assessment['game_volatility'] = 'high'
        elif game_volatility > 5:
            base_limit *= 0.85
            limiting_factors.append('medium_game_volatility')
            risk_assessment['game_volatility'] = 'medium'
        else:
            risk_assessment['game_volatility'] = 'low'

        # Adjust based on system risk status
        system_risk_level = system_status.get('overall_status', 'green')
        if system_risk_level == 'critical':
            base_limit *= 0.3
            limiting_factors.append('critical_system_risk')
            risk_assessment['system_risk'] = 'critical'
        elif system_risk_level == 'red':
            base_limit *= 0.5
            limiting_factors.append('high_system_risk')
            risk_assessment['system_risk'] = 'high'
        elif system_risk_level == 'yellow':
            base_limit *= 0.8
            limiting_factors.append('medium_system_risk')
            risk_assessment['system_risk'] = 'medium'
        else:
            risk_assessment['system_risk'] = 'low'

        # Apply absolute maximum limit
        max_allowed_bet = min(base_limit, self.risk_limits['single_bet_limit'])

        # Generate recommendations
        recommendations = []
        if player_risk_score > 50:
            recommendations.append("Consider responsible gaming measures")
        if system_risk_level in ['red', 'critical']:
            recommendations.append("Monitor system risk indicators closely")
        if game_volatility > 5:
            recommendations.append("High volatility game - monitor exposure")

        return {
            'max_allowed_bet': max_allowed_bet,
            'limiting_factors': limiting_factors,
            'risk_assessment': risk_assessment,
            'recommendations': recommendations
        }

    def optimize_game_portfolio(self, target_return: float, max_risk: float,
                              available_games: List[Dict]) -> Dict:
        """Optimize game portfolio for risk-return profile"""

        # Extract game mathematics
        game_returns = []
        game_risks = []
        game_ids = []

        for game in available_games:
            game_math = game.get('mathematics', {})
            game_returns.append(game_math.get('rtp', 0.95) - 1)  # Convert to return
            game_risks.append(game_math.get('volatility_index', 2.0))
            game_ids.append(game['game_id'])

        # Convert to numpy arrays
        returns = np.array(game_returns)
        risks = np.array(game_risks)

        # Number of games
        n_games = len(available_games)

        # Optimization objective: maximize return for given risk level
        def objective(weights):
            portfolio_return = np.sum(returns * weights)
            portfolio_risk = np.sqrt(np.sum((risks * weights) ** 2))  # Simplified risk calculation

            # Penalty for exceeding risk target
            risk_penalty = max(0, portfolio_risk - max_risk) * 100

            # Penalty for not meeting return target
            return_penalty = max(0, target_return - portfolio_return) * 50

            # Return negative because we want to maximize
            return -(portfolio_return - risk_penalty - return_penalty)

        # Constraints
        constraints = [
            {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0},  # Weights sum to 1
            {'type': 'ineq', 'fun': lambda w: w},  # Weights >= 0
        ]

        # Bounds for weights
        bounds = [(0, 1) for _ in range(n_games)]

        # Initial guess (equal weights)
        initial_weights = np.ones(n_games) / n_games

        # Perform optimization
        result = minimize(objective, initial_weights, method='SLSQP',
                         bounds=bounds, constraints=constraints)

        if result.success:
            optimal_weights = result.x

            # Calculate final portfolio metrics
            portfolio_return = np.sum(returns * optimal_weights)
            portfolio_risk = np.sqrt(np.sum((risks * optimal_weights) ** 2))

            # Create portfolio recommendation
            portfolio_recommendation = []
            for i, (game, weight) in enumerate(zip(available_games, optimal_weights)):
                if weight > 0.01:  # Only include games with >1% weight
                    portfolio_recommendation.append({
                        'game_id': game['game_id'],
                        'game_name': game.get('name', f'Game {i}'),
                        'optimal_weight': weight,
                        'expected_return': returns[i],
                        'volatility': risks[i]
                    })

            return {
                'optimization_success': True,
                'optimal_portfolio': portfolio_recommendation,
                'portfolio_metrics': {
                    'expected_return': portfolio_return,
                    'portfolio_risk': portfolio_risk,
                    'sharpe_ratio': portfolio_return / portfolio_risk if portfolio_risk > 0 else 0
                },
                'diversification_ratio': self._calculate_diversification_ratio(optimal_weights),  # ty:ignore[unresolved-attribute]
                'concentration_analysis': self._analyze_concentration(optimal_weights)  # ty:ignore[unresolved-attribute]
            }
        else:
            return {
                'optimization_success': False,
                'error': result.message,
                'recommendation': 'Consider relaxing constraints or adding more games'
            }


class MathematicalModelValidator:
    def __init__(self, validation_database: asyncpg.Pool):
        self.db = validation_database
        self.validation_metrics = {}

    async def validate_game_mathematics(self, game_id: str, theoretical_math: Dict) -> Dict:
        """Validate game mathematics against actual performance"""

        # Get actual game data
        actual_data = await self._get_actual_game_data(game_id, sample_size=1000000)  # ty:ignore[unresolved-attribute]

        # Compare theoretical vs actual RTP
        rtp_validation = await self._validate_rtp(
            theoretical_math['rtp'],
            actual_data['actual_rtp']
        )

        # Compare volatility
        volatility_validation = await self._validate_volatility(  # ty:ignore[unresolved-attribute]
            theoretical_math['volatility_index'],
            actual_data['actual_volatility']
        )

        # Validate house edge
        house_edge_validation = await self._validate_house_edge(  # ty:ignore[unresolved-attribute]
            theoretical_math['house_edge'],
            actual_data['actual_house_edge']
        )

        # Statistical significance testing
        statistical_tests = await self._run_statistical_tests(  # ty:ignore[unresolved-attribute]
            theoretical_math,
            actual_data
        )

        # Generate validation report
        validation_report = {
            'game_id': game_id,
            'validation_date': datetime.now().isoformat(),
            'sample_size': len(actual_data['outcomes']),
            'rtp_validation': rtp_validation,
            'volatility_validation': volatility_validation,
            'house_edge_validation': house_edge_validation,
            'statistical_tests': statistical_tests,
            'overall_validation_status': self._determine_validation_status([
                rtp_validation,
                volatility_validation,
                house_edge_validation,
                statistical_tests
            ]),
            'recommendations': self._generate_validation_recommendations(
                rtp_validation,
                volatility_validation,
                house_edge_validation,
                statistical_tests
            )
        }

        # Store validation results
        await self._store_validation_results(validation_report)  # ty:ignore[unresolved-attribute]

        return validation_report

    async def _validate_rtp(self, theoretical_rtp: float, actual_rtp: float) -> Dict:
        """Validate RTP against theoretical value"""

        # Calculate confidence interval for actual RTP
        sample_size = 1000000  # Assume large sample
        rtp_std_error = np.sqrt((theoretical_rtp * (1 - theoretical_rtp)) / sample_size)

        # 99% confidence interval
        margin_of_error = 2.576 * rtp_std_error  # z-score for 99% confidence
        confidence_interval = (
            theoretical_rtp - margin_of_error,
            theoretical_rtp + margin_of_error
        )

        # Check if actual RTP falls within confidence interval
        is_within_ci = confidence_interval[0] <= actual_rtp <= confidence_interval[1]

        # Calculate deviation
        deviation = abs(actual_rtp - theoretical_rtp)
        deviation_percentage = (deviation / theoretical_rtp) * 100

        return {
            'theoretical_rtp': theoretical_rtp,
            'actual_rtp': actual_rtp,
            'deviation': deviation,
            'deviation_percentage': deviation_percentage,
            'confidence_interval': confidence_interval,
            'is_within_confidence_interval': is_within_ci,
            'validation_status': 'pass' if is_within_ci and deviation_percentage < 0.5 else 'fail'
        }

    def _determine_validation_status(self, validations: List[Dict]) -> str:
        """Determine overall validation status"""

        failed_validations = sum(1 for validation in validations if validation.get('validation_status') == 'fail')
        total_validations = len(validations)

        if failed_validations == 0:
            return 'validated'
        elif failed_validations <= total_validations * 0.2:
            return 'conditionally_validated'
        elif failed_validations <= total_validations * 0.5:
            return 'requires_review'
        else:
            return 'not_validated'

    def _generate_validation_recommendations(self, *validations) -> List[str]:
        """Generate recommendations based on validation results"""

        recommendations = []

        for validation in validations:
            if validation.get('validation_status') == 'fail':
                if 'rtp' in str(validation).lower():
                    recommendations.append("Review RTP calculation and game logic")
                elif 'volatility' in str(validation).lower():
                    recommendations.append("Investigate volatility model accuracy")
                elif 'house_edge' in str(validation).lower():
                    recommendations.append("Verify house edge mathematics")
                else:
                    recommendations.append("Conduct detailed mathematical review")

        # General recommendations
        recommendations.extend([
            "Increase sample size for better statistical power",
            "Monitor actual performance vs theoretical expectations",
            "Consider recalibrating mathematical models",
            "Implement continuous monitoring system"
        ])

        return list(set(recommendations))  # Remove duplicates
