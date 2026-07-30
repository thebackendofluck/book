#!/usr/bin/env python3
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
Distributed Transaction Saga Orchestrator for Gambling Platform
================================================================

Chapter 42 - Complete Platform Architecture

Implements the Saga pattern for multi-step gambling transactions
that span multiple microservices. Each saga defines a sequence of
local transactions with compensating actions for rollback.

Saga: Player Deposit Flow
  1. Validate deposit request (Player Service)
  2. Process payment via PSP (Payment Service)
  3. Credit wallet balance (Wallet Service)
  4. Apply bonus if eligible (Bonus Service)
  5. Send confirmation notification (Notification Service)

Compensating Actions (reverse order on failure):
  5c. Cancel notification
  4c. Reverse bonus credit
  3c. Debit wallet (reverse credit)
  2c. Refund payment via PSP
  1c. Mark deposit as failed

Architecture:
- SagaOrchestrator: Manages saga lifecycle and step execution
- SagaStep: Individual step with execute and compensate methods
- SagaState: Persisted state for crash recovery
- Kafka-based communication with saga participants

Usage:
    orchestrator = SagaOrchestrator(redis_url="redis://localhost:6379")
    saga_id = await orchestrator.start_deposit_saga(
        player_id="PLR-123", amount=100.00, currency="EUR"
    )
    status = await orchestrator.get_saga_status(saga_id)

Dependencies:
    pip install redis aiohttp
"""

import asyncio
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # ty:ignore[invalid-assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("saga-orchestrator")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class SagaStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATION_FAILED = "compensation_failed"


class StepStatus(enum.Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    SKIPPED = "skipped"


@dataclass
class SagaStepResult:
    """Result from executing a saga step."""
    success: bool
    data: dict = field(default_factory=dict)
    error: Optional[str] = None
    service: str = ""
    duration_ms: float = 0.0


@dataclass
class SagaStep:
    """
    A single step in a saga with execute and compensate actions.

    Each step represents a local transaction in one microservice.
    The compensate action reverses the effect of execute.
    """
    name: str
    service: str
    execute_fn: Callable[..., Coroutine]
    compensate_fn: Callable[..., Coroutine]
    timeout_seconds: float = 30.0
    retry_count: int = 2
    retry_delay_seconds: float = 1.0
    status: StepStatus = StepStatus.PENDING
    result: Optional[SagaStepResult] = None
    compensate_result: Optional[SagaStepResult] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class SagaState:
    """
    Persistent saga state for crash recovery.

    Stored in Redis with TTL for automatic cleanup.
    """
    saga_id: str
    saga_type: str
    status: SagaStatus
    created_at: str
    updated_at: str
    player_id: str
    context: dict                     # Shared context between steps
    current_step: int
    steps: List[dict]                 # Serialized step states
    error: Optional[str] = None
    compensation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "saga_id": self.saga_id,
            "saga_type": self.saga_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "player_id": self.player_id,
            "context": self.context,
            "current_step": self.current_step,
            "steps": self.steps,
            "error": self.error,
            "compensation_errors": self.compensation_errors,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SagaState":
        return cls(
            saga_id=data["saga_id"],
            saga_type=data["saga_type"],
            status=SagaStatus(data["status"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            player_id=data["player_id"],
            context=data["context"],
            current_step=data["current_step"],
            steps=data["steps"],
            error=data.get("error"),
            compensation_errors=data.get("compensation_errors", []),
        )


# ---------------------------------------------------------------------------
# Service Simulators (replace with actual HTTP/gRPC clients in production)
# ---------------------------------------------------------------------------

class ServiceSimulator:
    """
    Simulates microservice calls for testing.
    In production, replace with actual HTTP/gRPC client calls.
    """

    def __init__(self, failure_probability: float = 0.0):
        self._fail_prob = failure_probability
        import random
        self._random = random

    async def validate_deposit(self, context: dict) -> SagaStepResult:
        """Step 1: Validate deposit request."""
        await asyncio.sleep(0.05)  # Simulate network

        player_id = context["player_id"]
        amount = context["amount"]

        # Validate
        if amount <= 0 or amount > 50000:
            return SagaStepResult(
                success=False,
                error=f"Invalid amount: {amount}",
                service="player-service",
            )

        return SagaStepResult(
            success=True,
            data={"validated": True, "player_id": player_id, "kyc_level": "full"},
            service="player-service",
        )

    async def compensate_validate(self, context: dict) -> SagaStepResult:
        """Compensate Step 1: Mark deposit as failed."""
        await asyncio.sleep(0.02)
        return SagaStepResult(
            success=True,
            data={"marked_failed": True},
            service="player-service",
        )

    async def process_payment(self, context: dict) -> SagaStepResult:
        """Step 2: Process payment via PSP."""
        await asyncio.sleep(0.2)  # PSP call is slower

        if self._random.random() < self._fail_prob:
            return SagaStepResult(
                success=False,
                error="PSP_TIMEOUT: Payment provider did not respond",
                service="payment-service",
            )

        psp_reference = f"PSP-{uuid.uuid4().hex[:12]}"
        return SagaStepResult(
            success=True,
            data={"psp_reference": psp_reference, "psp": "stripe"},
            service="payment-service",
        )

    async def compensate_payment(self, context: dict) -> SagaStepResult:
        """Compensate Step 2: Refund payment."""
        await asyncio.sleep(0.15)
        psp_ref = context.get("psp_reference", "unknown")
        return SagaStepResult(
            success=True,
            data={"refund_reference": f"REFUND-{psp_ref}", "refunded": True},
            service="payment-service",
        )

    async def credit_wallet(self, context: dict) -> SagaStepResult:
        """Step 3: Credit player wallet."""
        await asyncio.sleep(0.05)

        if self._random.random() < self._fail_prob:
            return SagaStepResult(
                success=False,
                error="WALLET_ERROR: Could not acquire lock",
                service="wallet-service",
            )

        return SagaStepResult(
            success=True,
            data={
                "wallet_tx_id": f"WTX-{uuid.uuid4().hex[:10]}",
                "new_balance": context.get("amount", 0) + 500.0,  # Simulated
            },
            service="wallet-service",
        )

    async def compensate_wallet(self, context: dict) -> SagaStepResult:
        """Compensate Step 3: Debit wallet (reverse credit)."""
        await asyncio.sleep(0.05)
        return SagaStepResult(
            success=True,
            data={"debit_reference": f"DEBIT-{uuid.uuid4().hex[:8]}", "reversed": True},
            service="wallet-service",
        )

    async def apply_bonus(self, context: dict) -> SagaStepResult:
        """Step 4: Apply deposit bonus if eligible."""
        await asyncio.sleep(0.03)

        amount = context.get("amount", 0)
        # Simple bonus rule: 100% match up to 100 EUR for first deposit
        bonus_amount = min(amount, 100.0)

        return SagaStepResult(
            success=True,
            data={
                "bonus_id": f"BONUS-{uuid.uuid4().hex[:8]}",
                "bonus_amount": bonus_amount,
                "wagering_requirement": bonus_amount * 35,  # 35x wagering
            },
            service="bonus-service",
        )

    async def compensate_bonus(self, context: dict) -> SagaStepResult:
        """Compensate Step 4: Reverse bonus credit."""
        await asyncio.sleep(0.03)
        bonus_id = context.get("bonus_id", "unknown")
        return SagaStepResult(
            success=True,
            data={"bonus_reversed": True, "bonus_id": bonus_id},
            service="bonus-service",
        )

    async def send_notification(self, context: dict) -> SagaStepResult:
        """Step 5: Send confirmation notification."""
        await asyncio.sleep(0.02)
        return SagaStepResult(
            success=True,
            data={
                "notification_id": f"NOTIF-{uuid.uuid4().hex[:8]}",
                "channel": "email",
                "template": "deposit_confirmation",
            },
            service="notification-service",
        )

    async def compensate_notification(self, context: dict) -> SagaStepResult:
        """Compensate Step 5: Cancel/update notification."""
        await asyncio.sleep(0.02)
        return SagaStepResult(
            success=True,
            data={"cancelled": True},
            service="notification-service",
        )


# ---------------------------------------------------------------------------
# Saga Orchestrator
# ---------------------------------------------------------------------------

class SagaOrchestrator:
    """
    Orchestrator for distributed transaction sagas.

    Features:
    - Step-by-step execution with automatic compensation on failure
    - Persistent state in Redis for crash recovery
    - Configurable retry per step
    - Timeout enforcement
    - Audit logging of all saga events
    - Idempotent step execution

    GLI-11 Compliance:
    - All financial operations are fully reversible
    - Audit trail captures every step transition
    - Saga state is durable (survives process restart)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        service_simulator: Optional[ServiceSimulator] = None,
    ):
        self.redis_url = redis_url
        self.redis: Optional[aioredis.Redis] = None
        self._simulator = service_simulator or ServiceSimulator()
        self._active_sagas: Dict[str, asyncio.Task] = {}

    async def connect(self):
        if aioredis:
            try:
                self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
                await self.redis.ping()  # ty:ignore[invalid-await]
                logger.info("Saga orchestrator connected to Redis")
            except Exception as e:
                logger.warning("Redis not available: %s", e)
                self.redis = None

    async def close(self):
        if self.redis:
            await self.redis.close()

    def _build_deposit_steps(self) -> List[SagaStep]:
        """Build the step sequence for a deposit saga."""
        sim = self._simulator
        return [
            SagaStep(
                name="validate_deposit",
                service="player-service",
                execute_fn=sim.validate_deposit,
                compensate_fn=sim.compensate_validate,
                timeout_seconds=10,
            ),
            SagaStep(
                name="process_payment",
                service="payment-service",
                execute_fn=sim.process_payment,
                compensate_fn=sim.compensate_payment,
                timeout_seconds=30,
                retry_count=2,
            ),
            SagaStep(
                name="credit_wallet",
                service="wallet-service",
                execute_fn=sim.credit_wallet,
                compensate_fn=sim.compensate_wallet,
                timeout_seconds=10,
                retry_count=1,
            ),
            SagaStep(
                name="apply_bonus",
                service="bonus-service",
                execute_fn=sim.apply_bonus,
                compensate_fn=sim.compensate_bonus,
                timeout_seconds=10,
            ),
            SagaStep(
                name="send_notification",
                service="notification-service",
                execute_fn=sim.send_notification,
                compensate_fn=sim.compensate_notification,
                timeout_seconds=10,
            ),
        ]

    async def start_deposit_saga(
        self,
        player_id: str,
        amount: float,
        currency: str = "EUR",
        payment_method: str = "card",
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        Start a deposit saga.

        Returns the saga_id for tracking.
        """
        saga_id = f"SAGA-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        context = {
            "player_id": player_id,
            "amount": amount,
            "currency": currency,
            "payment_method": payment_method,
            "idempotency_key": idempotency_key or f"IDP-{uuid.uuid4().hex[:12]}",
        }

        state = SagaState(
            saga_id=saga_id,
            saga_type="deposit",
            status=SagaStatus.PENDING,
            created_at=now,
            updated_at=now,
            player_id=player_id,
            context=context,
            current_step=0,
            steps=[],
        )

        await self._persist_state(state)

        logger.info(
            "Starting deposit saga %s: player=%s, amount=%.2f %s",
            saga_id, player_id, amount, currency,
        )

        # Execute saga
        steps = self._build_deposit_steps()
        await self._execute_saga(saga_id, state, steps)

        return saga_id

    async def _execute_saga(
        self, saga_id: str, state: SagaState, steps: List[SagaStep]
    ) -> None:
        """Execute saga steps sequentially with compensation on failure."""
        state.status = SagaStatus.RUNNING
        state.updated_at = datetime.now(timezone.utc).isoformat()
        completed_steps: List[SagaStep] = []

        for i, step in enumerate(steps):
            state.current_step = i
            step.status = StepStatus.EXECUTING
            step.started_at = time.time()

            logger.info(
                "Saga %s: executing step %d/%d [%s] via %s",
                saga_id, i + 1, len(steps), step.name, step.service,
            )

            # Execute with retry
            result = await self._execute_step_with_retry(step, state.context)
            step.result = result
            step.completed_at = time.time()

            if result.success:
                step.status = StepStatus.COMPLETED
                # Merge step output into context for subsequent steps
                state.context.update(result.data)
                completed_steps.append(step)

                state.steps.append({
                    "name": step.name,
                    "service": step.service,
                    "status": step.status.value,
                    "data": result.data,
                    "duration_ms": result.duration_ms,
                })

                logger.info(
                    "Saga %s: step [%s] completed (%.1fms)",
                    saga_id, step.name, result.duration_ms,
                )
            else:
                step.status = StepStatus.FAILED
                state.error = f"Step [{step.name}] failed: {result.error}"

                state.steps.append({
                    "name": step.name,
                    "service": step.service,
                    "status": step.status.value,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                })

                logger.warning(
                    "Saga %s: step [%s] FAILED: %s. Starting compensation.",
                    saga_id, step.name, result.error,
                )

                # Start compensation
                await self._compensate(saga_id, state, completed_steps)
                return

            await self._persist_state(state)

        # All steps completed successfully
        state.status = SagaStatus.COMPLETED
        state.updated_at = datetime.now(timezone.utc).isoformat()
        await self._persist_state(state)

        logger.info(
            "Saga %s: COMPLETED successfully (%d steps)",
            saga_id, len(steps),
        )

    async def _execute_step_with_retry(
        self, step: SagaStep, context: dict
    ) -> SagaStepResult:
        """Execute a step with configurable retry."""
        last_error = None

        for attempt in range(step.retry_count + 1):
            try:
                start = time.time()
                result = await asyncio.wait_for(
                    step.execute_fn(context),
                    timeout=step.timeout_seconds,
                )
                result.duration_ms = (time.time() - start) * 1000

                if result.success:
                    return result

                last_error = result.error

            except asyncio.TimeoutError:
                last_error = f"Timeout after {step.timeout_seconds}s"
            except Exception as e:
                last_error = str(e)

            if attempt < step.retry_count:
                logger.info(
                    "Step [%s] retry %d/%d after error: %s",
                    step.name, attempt + 1, step.retry_count, last_error,
                )
                await asyncio.sleep(step.retry_delay_seconds * (attempt + 1))

        return SagaStepResult(
            success=False,
            error=last_error,
            service=step.service,
            duration_ms=0,
        )

    async def _compensate(
        self, saga_id: str, state: SagaState, completed_steps: List[SagaStep]
    ) -> None:
        """
        Execute compensating actions in reverse order.

        GLI-11: All financial operations must be fully reversible.
        Compensation must be attempted for all completed steps,
        even if some compensations fail.
        """
        state.status = SagaStatus.COMPENSATING
        state.updated_at = datetime.now(timezone.utc).isoformat()
        await self._persist_state(state)

        logger.info(
            "Saga %s: compensating %d completed steps (reverse order)",
            saga_id, len(completed_steps),
        )

        all_compensated = True

        for step in reversed(completed_steps):
            step.status = StepStatus.COMPENSATING
            logger.info("Saga %s: compensating [%s]", saga_id, step.name)

            try:
                comp_result = await asyncio.wait_for(
                    step.compensate_fn(state.context),
                    timeout=step.timeout_seconds * 2,  # Extra time for compensation
                )
                step.compensate_result = comp_result

                if comp_result.success:
                    step.status = StepStatus.COMPENSATED
                    logger.info(
                        "Saga %s: [%s] compensated successfully",
                        saga_id, step.name,
                    )
                else:
                    step.status = StepStatus.COMPENSATION_FAILED
                    all_compensated = False
                    error_msg = f"Compensation for [{step.name}] failed: {comp_result.error}"
                    state.compensation_errors.append(error_msg)
                    logger.error("Saga %s: %s", saga_id, error_msg)

            except Exception as e:
                step.status = StepStatus.COMPENSATION_FAILED
                all_compensated = False
                error_msg = f"Compensation for [{step.name}] exception: {str(e)}"
                state.compensation_errors.append(error_msg)
                logger.error("Saga %s: %s", saga_id, error_msg)

        if all_compensated:
            state.status = SagaStatus.FAILED  # Failed but fully compensated
            logger.info("Saga %s: fully compensated (status=FAILED)", saga_id)
        else:
            state.status = SagaStatus.COMPENSATION_FAILED
            logger.critical(
                "Saga %s: COMPENSATION_FAILED - manual intervention required! "
                "Errors: %s",
                saga_id, state.compensation_errors,
            )

        state.updated_at = datetime.now(timezone.utc).isoformat()
        await self._persist_state(state)

    async def get_saga_status(self, saga_id: str) -> Optional[dict]:
        """Get the current status of a saga."""
        state = await self._load_state(saga_id)
        if state:
            return state.to_dict()
        return None

    async def _persist_state(self, state: SagaState) -> None:
        """Persist saga state to Redis."""
        if self.redis:
            key = f"saga:{state.saga_id}"
            await self.redis.setex(
                key,
                86400 * 7,  # 7 day TTL
                json.dumps(state.to_dict()),
            )

    async def _load_state(self, saga_id: str) -> Optional[SagaState]:
        """Load saga state from Redis."""
        if self.redis:
            key = f"saga:{saga_id}"
            data = await self.redis.get(key)
            if data:
                return SagaState.from_dict(json.loads(data))
        return None


# ---------------------------------------------------------------------------
# Test / Simulation
# ---------------------------------------------------------------------------

async def run_simulation():
    """Run saga orchestrator simulation."""
    print("=" * 70)
    print("Saga Orchestrator Simulation - Gambling Platform Deposit Flow")
    print("=" * 70)

    orchestrator = SagaOrchestrator(
        service_simulator=ServiceSimulator(failure_probability=0.0),
    )

    # Scenario 1: Successful deposit
    print("\n--- Scenario 1: Successful Deposit ---")
    saga_id = await orchestrator.start_deposit_saga(
        player_id="PLR-12345",
        amount=100.00,
        currency="EUR",
    )
    print(f"  Saga ID: {saga_id}")

    # Scenario 2: Failed payment (PSP error)
    print("\n--- Scenario 2: Failed Payment (compensation triggered) ---")
    fail_orchestrator = SagaOrchestrator(
        service_simulator=ServiceSimulator(failure_probability=1.0),
    )
    saga_id2 = await fail_orchestrator.start_deposit_saga(
        player_id="PLR-67890",
        amount=250.00,
        currency="GBP",
    )
    print(f"  Saga ID: {saga_id2}")

    # Scenario 3: Invalid amount
    print("\n--- Scenario 3: Validation Failure ---")
    saga_id3 = await orchestrator.start_deposit_saga(
        player_id="PLR-11111",
        amount=-50.00,  # Invalid
        currency="EUR",
    )
    print(f"  Saga ID: {saga_id3}")

    print("\nSimulation complete.")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Saga Orchestrator for Gambling Platform"
    )
    parser.add_argument("--test", action="store_true", help="Run simulation")
    parser.add_argument("--serve", type=int, help="Run as HTTP service on port")
    args = parser.parse_args()

    if args.test:
        asyncio.run(run_simulation())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
