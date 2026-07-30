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
Payment Service - Chapter 22: Casino Implementation Planning and Timeline

Payment gateway integration layer supporting multiple payment processors
including Stripe (card payments) and Trustly (bank transfers).

Part of the iGaming Platform Engineering book.
"""

import stripe  # ty:ignore[unresolved-import]
import requests
import time
from typing import Dict, Optional
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PaymentRequest:
    amount: Decimal
    currency: str
    user_id: str
    payment_method: str
    description: str


@dataclass
class PaymentResponse:
    transaction_id: str
    status: str
    amount: Decimal
    currency: str
    fees: Decimal


class PaymentProcessingError(Exception):
    """Raised when a payment processing error occurs."""
    pass


class PaymentService:
    def __init__(self, stripe_secret_key: str, trustly_config: Dict):
        stripe.api_key = stripe_secret_key
        self.trustly_config = trustly_config

    async def process_payment(self, request: PaymentRequest) -> PaymentResponse:
        """Process payment through appropriate gateway"""
        if request.payment_method in ['visa', 'mastercard']:
            return await self._process_stripe(request)
        elif request.payment_method == 'trustly':
            return await self._process_trustly(request)
        else:
            raise ValueError(f"Unsupported payment method: {request.payment_method}")

    async def _process_stripe(self, request: PaymentRequest) -> PaymentResponse:
        """Process payment via Stripe"""
        try:
            # Convert amount to cents
            amount_cents = int(request.amount * 100)

            payment_intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=request.currency.lower(),
                payment_method_types=['card'],
                description=request.description,
                metadata={
                    'user_id': request.user_id,
                    'integration': 'stripe'
                }
            )

            return PaymentResponse(
                transaction_id=payment_intent.id,
                status='pending',
                amount=request.amount,
                currency=request.currency,
                fees=Decimal('0.029') * request.amount + Decimal('0.30')  # 2.9% + 30¢
            )

        except stripe.error.StripeError as e:
            raise PaymentProcessingError(f"Stripe error: {e.user_message}")

    async def _process_trustly(self, request: PaymentRequest) -> PaymentResponse:
        """Process payment via Trustly"""
        payload = {
            'method': 'Deposit',
            'params': {
                'Username': self.trustly_config['username'],
                'Password': self.trustly_config['password'],
                'MessageId': f"deposit_{request.user_id}_{int(time.time())}",
                'NotificationURL': self.trustly_config['notification_url'],
                'EndUserID': request.user_id,
                'Message': {
                    'Amount': str(request.amount),
                    'Currency': request.currency,
                    'Country': 'GB',  # Default to UK
                    'Locale': 'en_GB',
                    'SuccessURL': f"{self.trustly_config['base_url']}/success",
                    'FailURL': f"{self.trustly_config['base_url']}/fail"
                }
            },
            'version': '1.1'
        }

        response = requests.post(
            self.trustly_config['api_url'],
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            return PaymentResponse(
                transaction_id=data['result']['orderid'],
                status='pending',
                amount=request.amount,
                currency=request.currency,
                fees=Decimal('0.015') * request.amount  # 1.5% fee
            )
        else:
            raise PaymentProcessingError(f"Trustly API error: {response.text}")
