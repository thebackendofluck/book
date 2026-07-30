# Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 4: Online Poker Platform Architecture
Payment Processing Implementation

This module contains payment and compliance classes:
- PaymentProcessor: Multi-provider payment processing for deposits/withdrawals
- AMLCompliance: Anti-money laundering checks and regulatory reporting
- ComplianceManager: Player eligibility verification across jurisdictions
- ResponsibleGaming: Player limits and self-exclusion enforcement

Reference: Chapter 4 - Payment Processing and Compliance sections
"""

import asyncio


class PaymentProcessor:
    def __init__(self):
        self.payment_providers = {
            'credit_card': CreditCardProcessor(),  # ty:ignore[unresolved-reference]
            'bank_transfer': BankTransferProcessor(),  # ty:ignore[unresolved-reference]
            'e_wallet': EWalletProcessor(),  # ty:ignore[unresolved-reference]
            'crypto': CryptoProcessor()  # ty:ignore[unresolved-reference]
        }

    async def process_deposit(self, player_id, amount, method, details):
        """Process player deposit"""
        # Validate player and amount
        if not self.validate_deposit_request(player_id, amount):  # ty:ignore[possibly-missing-attribute]
            return {'status': 'FAILED', 'reason': 'Validation failed'}

        # Process with appropriate provider
        provider = self.payment_providers[method]

        try:
            # Initiate transaction
            transaction_id = self.create_transaction_record(  # ty:ignore[possibly-missing-attribute]
                player_id, amount, method, 'DEPOSIT'
            )

            # Process payment
            result = await provider.process_payment(amount, details)

            if result['status'] == 'SUCCESS':
                # Update player balance
                await self.update_player_balance(player_id, amount)  # ty:ignore[possibly-missing-attribute]
                await self.update_transaction_status(  # ty:ignore[possibly-missing-attribute]
                    transaction_id, 'COMPLETED'
                )

                # Send confirmation
                await self.send_deposit_confirmation(player_id, amount)  # ty:ignore[possibly-missing-attribute]

            return result

        except Exception as e:
            await self.handle_payment_error(transaction_id, e)  # ty:ignore[possibly-missing-attribute]
            return {'status': 'FAILED', 'reason': str(e)}


class AMLCompliance:
    def __init__(self):
        self.suspicious_patterns = []
        self.reporting_threshold = 10000  # USD

    async def check_transaction(self, transaction):
        """Check transaction for AML compliance"""
        checks = [
            self.check_velocity(transaction),  # ty:ignore[possibly-missing-attribute]
            self.check_amount_threshold(transaction),  # ty:ignore[possibly-missing-attribute]
            self.check_suspicious_pattern(transaction),  # ty:ignore[possibly-missing-attribute]
            self.check_source_of_funds(transaction)  # ty:ignore[possibly-missing-attribute]
        ]

        results = await asyncio.gather(*checks)

        if any(result['suspicious'] for result in results):
            await self.flag_for_review(transaction)  # ty:ignore[possibly-missing-attribute]

        if transaction['amount'] >= self.reporting_threshold:
            await self.file_regulatory_report(transaction)  # ty:ignore[possibly-missing-attribute]

        return {'approved': all(result['approved'] for result in results)}


class ComplianceManager:
    def __init__(self):
        self.jurisdictions = self.load_jurisdictions()  # ty:ignore[possibly-missing-attribute]
        self.age_verifier = AgeVerificationService()  # ty:ignore[unresolved-reference]
        self.identity_verifier = IdentityVerificationService()  # ty:ignore[unresolved-reference]

    async def verify_player_eligibility(self, player_data):
        """Verify player meets regulatory requirements"""
        checks = {
            'age_verification': await self.age_verifier.verify(player_data),
            'identity_verification': await self.identity_verifier.verify(player_data),
            'jurisdiction_check': self.check_jurisdiction(player_data['country']),  # ty:ignore[possibly-missing-attribute]
            'self_exclusion_check': await self.check_self_exclusion(player_data),  # ty:ignore[possibly-missing-attribute]
            'responsible_gaming_limits': self.check_gaming_limits(player_data)  # ty:ignore[possibly-missing-attribute]
        }

        return all(checks.values())


class ResponsibleGaming:
    def __init__(self):
        self.limit_types = ['deposit', 'loss', 'session_time', 'weekly_spend']

    async def set_player_limits(self, player_id, limits):
        """Set responsible gaming limits"""
        for limit_type, value in limits.items():
            await self.store_limit(player_id, limit_type, value)  # ty:ignore[possibly-missing-attribute]

    async def check_limits(self, player_id, action_type, amount=None):
        """Check if action exceeds player limits"""
        limits = await self.get_player_limits(player_id)  # ty:ignore[possibly-missing-attribute]
        current_usage = await self.get_current_usage(player_id)  # ty:ignore[possibly-missing-attribute]

        if action_type == 'deposit' and limits.get('deposit'):
            if current_usage['daily_deposits'] + amount > limits['deposit']:
                return False

        if action_type == 'session_time' and limits.get('session_time'):
            if current_usage['session_duration'] > limits['session_time']:
                await self.trigger_cooloff_period(player_id)  # ty:ignore[possibly-missing-attribute]
                return False

        return True
