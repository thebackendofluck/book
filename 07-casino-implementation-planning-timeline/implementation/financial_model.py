# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Financial Model - Chapter 22: Casino Implementation Planning and Timeline

Financial modeling utilities for casino implementation planning, including
user growth projections, ARPU calculations, and break-even analysis.

Part of the iGaming Platform Engineering book.
"""

import math


class FinancialModel:
    def __init__(self, initial_investment, monthly_burn_rate):
        self.initial_investment = initial_investment
        self.monthly_burn_rate = monthly_burn_rate
        self.revenue_streams = {}
        self.cost_centers = {}

    def project_revenue(self, months):
        """Project revenue based on user acquisition and ARPU"""
        monthly_users = self.calculate_user_growth(months)
        arpu = self.calculate_arpu(monthly_users)
        return monthly_users * arpu * 0.25  # 25% margin

    def calculate_user_growth(self, months):
        """S-curve user acquisition model"""
        max_users = 100000
        growth_rate = 0.15  # 15% monthly growth
        return max_users * (1 - math.exp(-growth_rate * months))

    def calculate_arpu(self, users):
        """Average revenue per user with scale effects"""
        base_arpu = 50  # €50 monthly
        scale_factor = min(1.0, users / 10000)  # Scale bonus for large user base
        return base_arpu * scale_factor


def calculate_break_even(monthly_burn_rate, average_deposit, deposit_conversion):
    """
    Calculate months to break even
    monthly_burn_rate: Total monthly costs
    average_deposit: Average deposit amount
    deposit_conversion: Percentage of visitors who deposit
    """
    monthly_revenue = visitors_per_month * deposit_conversion * average_deposit * 0.25  # 25% margin  # ty:ignore[unresolved-reference]
    months_to_breakeven = monthly_burn_rate / (monthly_revenue - monthly_burn_rate)
    return months_to_breakeven


# Example calculation
monthly_burn = 200000  # $200K monthly burn
avg_deposit = 50  # $50 average deposit
conversion = 0.05  # 5% of visitors deposit
monthly_visitors = 100000

if __name__ == "__main__":
    # Demonstrate financial model usage
    model = FinancialModel(
        initial_investment=2500000,
        monthly_burn_rate=monthly_burn
    )

    for month in [3, 6, 12, 18, 24]:
        projected = model.project_revenue(month)
        users = model.calculate_user_growth(month)
        print(f"Month {month}: {users:.0f} users, €{projected:,.0f} projected revenue")
