# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 32: Financial Operations
Real-Time Revenue and Margin Analysis System

This module provides enterprise-grade revenue monitoring and margin analysis
for high-volume gambling platforms. It tracks real-time metrics via Redis,
calculates margins by game/jurisdiction/payment method, and generates
optimization opportunities.

Usage:
    config = {'redis_url': 'redis://localhost:6379'}
    system = RevenueAnalyticsSystem(config)
    await system.process_transaction(transaction)
    dashboard = await system.get_realtime_dashboard()
"""

import asyncio
import redis.asyncio as redis
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from decimal import Decimal
import pandas as pd
import numpy as np


@dataclass
class RevenueMetric:
    timestamp: datetime
    total_revenue: Decimal
    gross_gaming_revenue: Decimal
    net_gaming_revenue: Decimal
    bonus_costs: Decimal
    payment_fees: Decimal
    operational_costs: Decimal
    net_profit: Decimal
    margin_percentage: Decimal
    active_players: int
    arpu: Decimal  # Average Revenue Per User
    arppu: Decimal  # Average Revenue Per Paying User


@dataclass
class MarginAnalysis:
    period_start: datetime
    period_end: datetime
    overall_margin: Decimal
    margin_by_game: Dict[str, Decimal]
    margin_by_jurisdiction: Dict[str, Decimal]
    margin_by_payment_method: Dict[str, Decimal]
    cost_breakdown: Dict[str, Decimal]
    profitability_thresholds: Dict[str, bool]
    optimization_opportunities: List[Dict]


class RevenueAnalyticsSystem:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.redis = redis.from_url(config['redis_url'])

    async def process_transaction(self, transaction: Dict) -> bool:
        """Process a transaction and update revenue metrics"""
        try:
            # Extract transaction data
            transaction_type = transaction.get('type')
            amount = Decimal(str(transaction.get('amount', 0)))
            user_id = transaction.get('user_id')
            game_id = transaction.get('game_id')
            jurisdiction = transaction.get('jurisdiction')
            payment_method = transaction.get('payment_method')

            # Update real-time metrics
            await self._update_realtime_metrics(transaction)

            # Update user lifetime value
            if user_id:
                await self._update_user_ltv(user_id, amount, transaction_type)  # ty:ignore[invalid-argument-type]

            # Update game performance
            if game_id:
                await self._update_game_performance(game_id, amount, transaction_type)  # ty:ignore[unresolved-attribute]

            # Update jurisdictional metrics
            if jurisdiction:
                await self._update_jurisdictional_metrics(jurisdiction, amount, transaction_type)  # ty:ignore[unresolved-attribute]

            # Update payment method analytics
            if payment_method:
                await self._update_payment_method_analytics(payment_method, amount, transaction_type)  # ty:ignore[unresolved-attribute]

            return True

        except Exception as e:
            self.logger.error(f"Failed to process transaction: {e}")
            return False

    async def _update_realtime_metrics(self, transaction: Dict):
        """Update real-time revenue metrics"""
        current_minute = datetime.now().replace(second=0, microsecond=0)

        # Update minute-level metrics
        minute_key = f"revenue:minute:{current_minute.strftime('%Y%m%d%H%M')}"

        amount = Decimal(str(transaction.get('amount', 0)))
        transaction_type = transaction.get('type')

        # Use Redis atomic operations for real-time updates
        if transaction_type == 'deposit':
            await self.redis.hincrbyfloat(minute_key, 'deposits', float(amount))  # ty:ignore[invalid-await]
            await self.redis.hincrby(minute_key, 'deposit_count', 1)  # ty:ignore[invalid-await]
        elif transaction_type == 'withdrawal':
            await self.redis.hincrbyfloat(minute_key, 'withdrawals', float(amount))  # ty:ignore[invalid-await]
            await self.redis.hincrby(minute_key, 'withdrawal_count', 1)  # ty:ignore[invalid-await]
        elif transaction_type == 'bet':
            await self.redis.hincrbyfloat(minute_key, 'bets', float(amount))  # ty:ignore[invalid-await]
            await self.redis.hincrby(minute_key, 'bet_count', 1)  # ty:ignore[invalid-await]
        elif transaction_type == 'win':
            await self.redis.hincrbyfloat(minute_key, 'wins', float(amount))  # ty:ignore[invalid-await]
            await self.redis.hincrby(minute_key, 'win_count', 1)  # ty:ignore[invalid-await]
        elif transaction_type == 'bonus':
            await self.redis.hincrbyfloat(minute_key, 'bonus_costs', float(amount))  # ty:ignore[invalid-await]
            await self.redis.hincrby(minute_key, 'bonus_count', 1)  # ty:ignore[invalid-await]

        # Set expiration (keep data for 7 days)
        await self.redis.expire(minute_key, 86400 * 7)

    async def _update_user_ltv(self, user_id: str, amount: Decimal, transaction_type: str):
        """Update user lifetime value"""
        ltv_key = f"user_ltv:{user_id}"

        if transaction_type in ['deposit', 'bet']:
            await self.redis.hincrbyfloat(ltv_key, 'total_deposits', float(amount))  # ty:ignore[invalid-await]
        elif transaction_type == 'bonus':
            await self.redis.hincrbyfloat(ltv_key, 'bonus_received', float(amount))  # ty:ignore[invalid-await]

        # Update last activity
        await self.redis.hset(ltv_key, 'last_activity', datetime.now().isoformat())  # ty:ignore[invalid-await]

        # Set expiration (keep user data for 2 years)
        await self.redis.expire(ltv_key, 86400 * 730)

    async def get_realtime_dashboard(self) -> Dict:
        """Get real-time revenue dashboard data"""
        try:
            current_minute = datetime.now().replace(second=0, microsecond=0)
            minute_key = f"revenue:minute:{current_minute.strftime('%Y%m%d%H%M')}"

            # Get current minute data
            current_data = await self.redis.hgetall(minute_key)  # ty:ignore[invalid-await]

            # Get last hour data for trends
            last_hour_data = await self._get_last_hour_metrics()

            # Calculate real-time metrics
            deposits = Decimal(str(current_data.get('deposits', 0)))
            withdrawals = Decimal(str(current_data.get('withdrawals', 0)))
            bets = Decimal(str(current_data.get('bets', 0)))
            wins = Decimal(str(current_data.get('wins', 0)))
            bonus_costs = Decimal(str(current_data.get('bonus_costs', 0)))

            gross_revenue = bets - wins
            net_revenue = gross_revenue - bonus_costs

            # Calculate margins
            house_margin = (gross_revenue / bets * 100) if bets > 0 else Decimal('0')
            net_margin = (net_revenue / bets * 100) if bets > 0 else Decimal('0')

            return {
                'timestamp': current_minute.isoformat(),
                'current_minute': {
                    'deposits': deposits,
                    'withdrawals': withdrawals,
                    'bets': bets,
                    'wins': wins,
                    'bonus_costs': bonus_costs,
                    'gross_revenue': gross_revenue,
                    'net_revenue': net_revenue,
                    'house_margin_percent': house_margin,
                    'net_margin_percent': net_margin
                },
                'last_hour_trend': last_hour_data,
                'active_users': await self._get_active_users_count(),  # ty:ignore[unresolved-attribute]
                'top_games': await self._get_top_games_revenue(),  # ty:ignore[unresolved-attribute]
                'alerts': await self._check_revenue_alerts()
            }

        except Exception as e:
            self.logger.error(f"Failed to get real-time dashboard: {e}")
            return {}

    async def _get_last_hour_metrics(self) -> Dict:
        """Get metrics for the last hour"""
        metrics = {
            'deposits': [],
            'withdrawals': [],
            'bets': [],
            'wins': [],
            'bonus_costs': []
        }

        # Get data for last 60 minutes
        for i in range(60):
            minute_time = datetime.now() - timedelta(minutes=i)
            minute_key = f"revenue:minute:{minute_time.strftime('%Y%m%d%H%M')}"

            minute_data = await self.redis.hgetall(minute_key)  # ty:ignore[invalid-await]
            if minute_data:
                for metric in metrics.keys():
                    value = float(minute_data.get(metric, 0))
                    metrics[metric].append(value)

        # Calculate averages
        return {
            'avg_deposits_per_minute': np.mean(metrics['deposits']) if metrics['deposits'] else 0,
            'avg_withdrawals_per_minute': np.mean(metrics['withdrawals']) if metrics['withdrawals'] else 0,
            'avg_bets_per_minute': np.mean(metrics['bets']) if metrics['bets'] else 0,
            'avg_wins_per_minute': np.mean(metrics['wins']) if metrics['wins'] else 0,
            'avg_bonus_costs_per_minute': np.mean(metrics['bonus_costs']) if metrics['bonus_costs'] else 0,
            'trend_direction': self._calculate_trend_direction(metrics)
        }

    def _calculate_trend_direction(self, metrics: Dict) -> str:
        """Calculate if metrics are trending up or down"""
        if not metrics['bets']:
            return 'stable'

        # Simple linear trend calculation
        bets = metrics['bets']
        if len(bets) < 10:
            return 'insufficient_data'

        # Calculate slope of last 10 points
        recent_bets = bets[-10:]
        x = np.arange(len(recent_bets))
        slope = np.polyfit(x, recent_bets, 1)[0]

        if slope > 0.1:
            return 'increasing'
        elif slope < -0.1:
            return 'decreasing'
        else:
            return 'stable'

    async def analyze_margins(self, start_date: datetime, end_date: datetime) -> MarginAnalysis:
        """Analyze margins across different dimensions"""
        try:
            # Get transaction data for the period
            transactions = await self._get_transaction_data(start_date, end_date)  # ty:ignore[unresolved-attribute]

            # Calculate overall margin
            total_bets = sum(t['amount'] for t in transactions if t['type'] == 'bet')
            total_wins = sum(t['amount'] for t in transactions if t['type'] == 'win')
            total_bonus_costs = sum(t['amount'] for t in transactions if t['type'] == 'bonus')

            gross_margin = ((total_bets - total_wins) / total_bets * 100) if total_bets > 0 else 0
            net_margin = ((total_bets - total_wins - total_bonus_costs) / total_bets * 100) if total_bets > 0 else 0

            # Calculate margins by game
            margin_by_game = await self._calculate_margin_by_dimension(transactions, 'game_id')

            # Calculate margins by jurisdiction
            margin_by_jurisdiction = await self._calculate_margin_by_dimension(transactions, 'jurisdiction')

            # Calculate margins by payment method
            margin_by_payment = await self._calculate_margin_by_dimension(transactions, 'payment_method')

            # Analyze cost breakdown
            cost_breakdown = await self._analyze_cost_breakdown(start_date, end_date)

            # Identify profitability thresholds
            profitability_thresholds = {
                'overall_profitable': net_margin > 5,  # 5% net margin threshold
                'games_profitable': len([g for g in margin_by_game.values() if g > 0]) / len(margin_by_game) > 0.8,
                'jurisdictions_profitable': len([j for j in margin_by_jurisdiction.values() if j > 0]) / len(margin_by_jurisdiction) > 0.9
            }

            # Generate optimization opportunities
            optimization_opportunities = await self._generate_margin_optimizations(
                margin_by_game, margin_by_jurisdiction, cost_breakdown
            )

            return MarginAnalysis(
                period_start=start_date,
                period_end=end_date,
                overall_margin=Decimal(str(net_margin)),
                margin_by_game={k: Decimal(str(v)) for k, v in margin_by_game.items()},
                margin_by_jurisdiction={k: Decimal(str(v)) for k, v in margin_by_jurisdiction.items()},
                margin_by_payment_method={k: Decimal(str(v)) for k, v in margin_by_payment.items()},
                cost_breakdown={k: Decimal(str(v)) for k, v in cost_breakdown.items()},
                profitability_thresholds=profitability_thresholds,
                optimization_opportunities=optimization_opportunities
            )

        except Exception as e:
            self.logger.error(f"Failed to analyze margins: {e}")
            raise

    async def _calculate_margin_by_dimension(self, transactions: List[Dict], dimension: str) -> Dict[str, float]:
        """Calculate margins grouped by a specific dimension"""
        dimension_data = {}

        for transaction in transactions:
            dim_value = transaction.get(dimension, 'unknown')

            if dim_value not in dimension_data:
                dimension_data[dim_value] = {'bets': 0, 'wins': 0, 'bonuses': 0}

            amount = transaction['amount']
            trans_type = transaction['type']

            if trans_type == 'bet':
                dimension_data[dim_value]['bets'] += amount
            elif trans_type == 'win':
                dimension_data[dim_value]['wins'] += amount
            elif trans_type == 'bonus':
                dimension_data[dim_value]['bonuses'] += amount

        # Calculate margins
        margins = {}
        for dim_value, data in dimension_data.items():
            bets = data['bets']
            if bets > 0:
                gross_margin = ((bets - data['wins']) / bets) * 100
                net_margin = ((bets - data['wins'] - data['bonuses']) / bets) * 100
                margins[dim_value] = net_margin
            else:
                margins[dim_value] = 0

        return margins

    async def _analyze_cost_breakdown(self, start_date: datetime, end_date: datetime) -> Dict[str, float]:
        """Analyze cost breakdown for the period"""
        # This would integrate with FinOps data
        # Simplified example
        return {
            'payment_processing': 2.5,  # 2.5% of revenue
            'bonus_costs': 15.0,       # 15% of revenue
            'infrastructure': 8.0,     # 8% of revenue
            'marketing': 12.0,         # 12% of revenue
            'operations': 5.0,         # 5% of revenue
            'regulatory': 3.0          # 3% of revenue
        }

    async def _generate_margin_optimizations(
        self, margin_by_game: Dict, margin_by_jurisdiction: Dict, cost_breakdown: Dict
    ) -> List[Dict]:
        """Generate margin optimization opportunities"""
        optimizations = []

        # Game margin optimizations
        low_margin_games = [(game, margin) for game, margin in margin_by_game.items() if margin < 2]
        if low_margin_games:
            optimizations.append({
                'type': 'game_optimization',
                'title': 'Optimize low-margin games',
                'description': f"{len(low_margin_games)} games have margins below 2%",
                'impact': 'high',
                'recommendation': 'Review RTP settings and bonus allocations'
            })

        # Jurisdiction optimizations
        unprofitable_jurisdictions = [(juris, margin) for juris, margin in margin_by_jurisdiction.items() if margin < 0]
        if unprofitable_jurisdictions:
            optimizations.append({
                'type': 'jurisdiction_optimization',
                'title': 'Address unprofitable jurisdictions',
                'description': f"{len(unprofitable_jurisdictions)} jurisdictions are unprofitable",
                'impact': 'critical',
                'recommendation': 'Review local competition and cost structures'
            })

        # Cost optimizations
        if cost_breakdown.get('bonus_costs', 0) > 20:
            optimizations.append({
                'type': 'cost_optimization',
                'title': 'Reduce bonus costs',
                'description': f"Bonus costs at {cost_breakdown['bonus_costs']}% of revenue",
                'impact': 'high',
                'recommendation': 'Optimize bonus targeting and reduce high-cost promotions'
            })

        return optimizations

    async def get_financial_forecast(self, months: int = 6) -> Dict:  # ty:ignore[empty-body]
        """Generate financial forecast based on current trends"""
        # Implementation for financial forecasting
        pass

    async def _check_revenue_alerts(self) -> List[Dict]:
        """Check for revenue-related alerts"""
        alerts = []

        # Check for unusual withdrawal patterns
        withdrawal_ratio = await self._calculate_withdrawal_ratio()  # ty:ignore[unresolved-attribute]
        if withdrawal_ratio > 0.8:
            alerts.append({
                'severity': 'high',
                'title': 'High withdrawal ratio',
                'description': f'Withdrawal ratio at {withdrawal_ratio:.1%}, potential money laundering concern',
                'recommendation': 'Review withdrawal patterns and enhance monitoring'
            })

        # Check for revenue drops
        revenue_trend = await self._calculate_revenue_trend()  # ty:ignore[unresolved-attribute]
        if revenue_trend < -0.1:  # 10% drop
            alerts.append({
                'severity': 'medium',
                'title': 'Revenue decline detected',
                'description': f'Revenue trending down by {abs(revenue_trend):.1%}',
                'recommendation': 'Investigate cause and implement recovery measures'
            })

        return alerts
