# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
Lightning Network Payment Channel Manager

Manages c-lightning node operations for instant Bitcoin micro-payments
in a casino context. Provides:
- Invoice creation for player deposits with unique labels and expiry
- Payment processing for withdrawals with retry/route optimization
- Channel health monitoring (balance ratios, inactivity, fee rates)
- Automated channel maintenance (rebalancing, reconnection, fee adjustment)
- Comprehensive Lightning stats aggregation

Dependencies:
    lightning (pyln-client)
    redis.asyncio

Reference: Chapter 8 - Lightning Network Implementation section
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

import lightning
from lightning import LightningRpc, RpcError  # ty:ignore[unresolved-import]
import redis.asyncio as redis


@dataclass
class ChannelConfig:
    node_id: str
    host: str
    port: int
    capacity_sat: int
    push_sat: int
    fee_rate: int


@dataclass
class PaymentRequest:
    amount_msat: int
    description: str
    expiry: int
    user_id: str
    metadata: Dict


class LightningNetworkManager:
    def __init__(self, rpc_path: str, redis_client: redis.Redis):
        self.rpc = LightningRpc(rpc_path)
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.active_channels: Dict[str, ChannelConfig] = {}

    async def initialize_node(self) -> Dict:
        """Initialize Lightning node and return node info"""
        try:
            # Get node information
            info = self.rpc.getinfo()

            # Check if node is synced
            if not info['warning_bitcoind_sync'] and not info['warning_lightningd_sync']:
                self.logger.info(f"Lightning node ready: {info['id']}")
                return {
                    'node_id': info['id'],
                    'alias': info['alias'],
                    'blockheight': info['blockheight'],
                    'num_peers': info['num_peers'],
                    'num_channels': info['num_channels']
                }
            else:
                raise Exception("Node not synchronized")

        except RpcError as e:
            self.logger.error(f"Lightning RPC error: {e}")
            raise

    async def create_invoice(
        self,
        amount_msat: int,
        user_id: str,
        description: str = "Casino deposit",
        expiry: int = 3600
    ) -> Dict:
        """Create Lightning invoice for deposit"""
        try:
            # Generate unique label for invoice
            label = f"casino_{user_id}_{int(time.time() * 1000)}"

            # Create invoice
            invoice = self.rpc.invoice(
                amount_msat=amount_msat,
                label=label,
                description=description,
                expiry=expiry
            )

            # Store invoice metadata
            await self.store_invoice_metadata({  # ty:ignore[unresolved-attribute]
                'payment_hash': invoice['payment_hash'],
                'bolt11': invoice['bolt11'],
                'amount_msat': amount_msat,
                'user_id': user_id,
                'created_at': int(time.time()),
                'status': 'pending',
                'type': 'deposit'
            })

            return {
                'bolt11': invoice['bolt11'],
                'payment_hash': invoice['payment_hash'],
                'expires_at': int(time.time()) + expiry
            }

        except RpcError as e:
            self.logger.error(f"Failed to create invoice: {e}")
            raise

    async def pay_invoice(
        self,
        bolt11: str,
        user_id: str,
        max_fee_msat: int = 10000  # 10 sat default max fee
    ) -> Dict:
        """Pay Lightning invoice for withdrawal"""
        try:
            # Decode invoice first
            decoded = self.rpc.decodepay(bolt11)

            # Validate invoice
            if decoded['status'] == 'expired':
                raise ValueError("Invoice expired")

            if decoded['amount_msat'] is None:
                raise ValueError("Invoice amount not specified")

            # Check user balance
            user_balance = await self.get_user_lightning_balance(user_id)  # ty:ignore[unresolved-attribute]
            total_amount = decoded['amount_msat'] + max_fee_msat

            if user_balance < total_amount:
                raise ValueError("Insufficient Lightning balance")

            # Attempt payment with retry logic
            payment_result = await self._attempt_payment_with_retry(
                bolt11,
                max_fee_msat,
                retry_count=3
            )

            if payment_result['status'] == 'complete':
                # Update user balance
                await self.update_user_lightning_balance(  # ty:ignore[unresolved-attribute]
                    user_id,
                    -total_amount
                )

                # Store payment record
                await self.store_payment_record({  # ty:ignore[unresolved-attribute]
                    'payment_hash': payment_result['payment_hash'],
                    'bolt11': bolt11,
                    'amount_msat': decoded['amount_msat'],
                    'fee_msat': payment_result['fee_msat'],
                    'user_id': user_id,
                    'status': 'completed',
                    'completed_at': int(time.time())
                })

                return {
                    'payment_hash': payment_result['payment_hash'],
                    'amount_msat': decoded['amount_msat'],
                    'fee_msat': payment_result['fee_msat'],
                    'preimage': payment_result['payment_preimage']
                }
            else:
                raise Exception(f"Payment failed: {payment_result['error']}")

        except RpcError as e:
            self.logger.error(f"Lightning payment error: {e}")
            raise

    async def _attempt_payment_with_retry(
        self,
        bolt11: str,
        max_fee_msat: int,
        retry_count: int
    ) -> Dict:
        """Attempt payment with retry logic and route optimization"""
        for attempt in range(retry_count):
            try:
                # Try payment with different fee strategies
                fee_strategy = self._calculate_optimal_fee(attempt, max_fee_msat)

                payment = self.rpc.pay(
                    bolt11=bolt11,
                    maxfeepercent=fee_strategy['fee_percent'],
                    retry_for=fee_strategy['retry_seconds'],
                    maxdelay=fee_strategy['max_delay']
                )

                if payment['status'] == 'complete':
                    return payment
                elif payment['status'] == 'failed':
                    # Analyze failure reason
                    if 'error' in payment:
                        if 'no_route' in payment['error']:
                            # Try to open channel if no route found
                            await self._attempt_channel_open(payment)  # ty:ignore[unresolved-attribute]
                        elif 'insufficient_balance' in payment['error']:
                            # Rebalance channels if needed
                            await self._rebalance_channels()  # ty:ignore[unresolved-attribute]

                    if attempt < retry_count - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue

                return payment

            except RpcError as e:
                self.logger.warning(f"Payment attempt {attempt + 1} failed: {e}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

        raise Exception("All payment attempts failed")

    def _calculate_optimal_fee(self, attempt: int, max_fee_msat: int) -> Dict:
        """Calculate optimal fee strategy based on attempt number"""
        base_strategies = [
            {'fee_percent': 0.1, 'retry_seconds': 30, 'max_delay': 10},
            {'fee_percent': 0.5, 'retry_seconds': 60, 'max_delay': 20},
            {'fee_percent': 1.0, 'retry_seconds': 120, 'max_delay': 40}
        ]

        return base_strategies[min(attempt, len(base_strategies) - 1)]

    async def manage_channels(self) -> Dict:
        """Manage Lightning channels for optimal performance"""
        try:
            # Get current channel status
            channels = self.rpc.listchannels()
            peers = self.rpc.listpeers()

            metrics = {
                'total_channels': len(channels),
                'active_channels': len([c for c in channels if c['active']]),
                'total_capacity': sum(c['capacity'] for c in channels),
                'total_local_balance': sum(c['to_us_msat'] for c in channels) / 1000,
                'total_remote_balance': sum(c['to_them_msat'] for c in channels) / 1000
            }

            # Channel health checks
            health_issues = []

            for channel in channels:
                # Check channel balance ratio
                local_ratio = channel['to_us_msat'] / channel['capacity']
                if local_ratio < 0.1:  # Less than 10% local balance
                    health_issues.append({
                        'channel': channel['short_channel_id'],
                        'issue': 'low_local_balance',
                        'current_ratio': local_ratio
                    })

                # Check channel activity
                if not channel['active']:
                    health_issues.append({
                        'channel': channel['short_channel_id'],
                        'issue': 'inactive_channel'
                    })

                # Check fee rates
                if channel['fee_per_kw'] > 1000:  # High fee rate
                    health_issues.append({
                        'channel': channel['short_channel_id'],
                        'issue': 'high_fee_rate',
                        'fee_rate': channel['fee_per_kw']
                    })

            # Auto-fix actions
            if health_issues:
                await self._perform_channel_maintenance(health_issues)

            return {
                'metrics': metrics,
                'health_issues': health_issues,
                'actions_taken': await self.get_maintenance_actions()  # ty:ignore[unresolved-attribute]
            }

        except RpcError as e:
            self.logger.error(f"Channel management error: {e}")
            raise

    async def _perform_channel_maintenance(self, issues: List[Dict]) -> None:
        """Perform automated channel maintenance"""
        for issue in issues:
            try:
                if issue['issue'] == 'low_local_balance':
                    # Attempt to rebalance channel
                    await self._rebalance_channel(issue['channel'])  # ty:ignore[unresolved-attribute]

                elif issue['issue'] == 'inactive_channel':
                    # Try to reconnect
                    await self._reconnect_channel(issue['channel'])  # ty:ignore[unresolved-attribute]

                elif issue['issue'] == 'high_fee_rate':
                    # Adjust fee rate
                    await self._adjust_fee_rate(issue['channel'], issue['fee_rate'])  # ty:ignore[unresolved-attribute]

            except Exception as e:
                self.logger.error(f"Failed to fix issue {issue}: {e}")

    async def get_lightning_stats(self) -> Dict:
        """Get comprehensive Lightning Network statistics"""
        try:
            info = self.rpc.getinfo()
            funds = self.rpc.listfunds()
            invoices = self.rpc.listinvoices()

            # Calculate statistics
            total_received = sum(
                inv['amount_received_msat'] for inv in invoices['invoices']
                if inv['status'] == 'paid'
            )

            total_sent = sum(
                pay['amount_sent_msat'] for pay in self.rpc.listpays()['pays']
                if pay['status'] == 'complete'
            )

            return {
                'node_info': {
                    'id': info['id'],
                    'alias': info['alias'],
                    'blockheight': info['blockheight']
                },
                'liquidity': {
                    'total': sum(f['amount_msat'] for f in funds['outputs']) / 1000,
                    'onchain': sum(f['amount_msat'] for f in funds['outputs'] if f['status'] == 'confirmed') / 1000,
                    'offchain': sum(ch['to_us_msat'] for ch in self.rpc.listchannels()) / 1000
                },
                'volume': {
                    'received_sat': total_received / 1000,
                    'sent_sat': total_sent / 1000,
                    'total_transactions': len(invoices['invoices']) + len(self.rpc.listpays()['pays'])
                },
                'performance': {
                    'success_rate': self._calculate_success_rate(),  # ty:ignore[unresolved-attribute]
                    'average_fee_ppm': self._calculate_average_fee(),  # ty:ignore[unresolved-attribute]
                    'channel_count': len(self.rpc.listchannels())
                }
            }

        except RpcError as e:
            self.logger.error(f"Failed to get Lightning stats: {e}")
            raise
