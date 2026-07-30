# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Backup Cost Optimizer - Chapter 24: Data Residency and Backup/Recovery Strategy

Cost model calculator for backup infrastructure, analyzing storage tiers,
lifecycle optimization opportunities, and total cost of ownership.

Part of the iGaming Platform Engineering book.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class StorageTier:
    name: str
    cost_per_tb_month: Decimal
    retrieval_cost_per_tb: Optional[Decimal] = None
    minimum_duration_days: Optional[int] = None


@dataclass
class BackupProfile:
    data_type: str
    size_tb: Decimal
    retention_days: int
    backup_frequency_days: int
    storage_tier: str


class BackupCostOptimizer:
    def __init__(self):
        self.storage_tiers = {
            'hot': StorageTier('hot', Decimal('150'), None, None),
            'warm': StorageTier('warm', Decimal('50'), Decimal('10'), 30),
            'cold': StorageTier('cold', Decimal('10'), Decimal('50'), 90),
            'archive': StorageTier('archive', Decimal('2'), Decimal('100'), 365)
        }

    def calculate_monthly_cost(self, backup_profiles: List[BackupProfile]) -> Dict:
        """Calculate total monthly backup costs with optimization recommendations"""
        total_cost = Decimal('0')
        optimization_opportunities = []

        for profile in backup_profiles:
            tier = self.storage_tiers[profile.storage_tier]

            # Calculate storage cost
            monthly_storage_cost = (profile.size_tb * tier.cost_per_tb_month)
            total_cost += monthly_storage_cost

            # Check for optimization opportunities
            if profile.retention_days > 365 and profile.storage_tier == 'warm':
                savings = monthly_storage_cost * Decimal('0.8')  # 80% savings moving to cold
                optimization_opportunities.append({
                    'profile': profile.data_type,
                    'recommendation': f"Move to cold storage after 90 days",
                    'monthly_savings': savings,
                    'annual_savings': savings * 12
                })

            # Check backup frequency optimization
            if profile.backup_frequency_days == 1 and profile.data_type != 'transaction_logs':
                optimization_opportunities.append({
                    'profile': profile.data_type,
                    'recommendation': f"Increase backup frequency to reduce storage costs",
                    'current_frequency': f"{profile.backup_frequency_days} days",
                    'suggested_frequency': "7 days"
                })

        return {
            'total_monthly_cost': total_cost,
            'total_annual_cost': total_cost * 12,
            'optimization_opportunities': optimization_opportunities,
            'potential_savings': sum(opp['monthly_savings'] for opp in optimization_opportunities if 'monthly_savings' in opp)
        }


# Example usage
if __name__ == "__main__":
    optimizer = BackupCostOptimizer()

    backup_profiles = [
        BackupProfile('database', Decimal('5'), 2555, 1, 'hot'),  # 7 years retention
        BackupProfile('user_uploads', Decimal('20'), 2555, 1, 'warm'),
        BackupProfile('logs', Decimal('50'), 2555, 7, 'cold'),
        BackupProfile('archives', Decimal('100'), 2555, 30, 'archive')
    ]

    cost_analysis = optimizer.calculate_monthly_cost(backup_profiles)
    print(f"Total Monthly Cost: ${cost_analysis['total_monthly_cost']:,.2f}")
    print(f"Potential Monthly Savings: ${cost_analysis.get('potential_savings', 0):,.2f}")
