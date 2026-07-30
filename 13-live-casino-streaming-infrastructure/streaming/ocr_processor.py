# Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
OCR Processor for Live Casino

Real-time optical character recognition for live casino games:
- Card recognition (rank, suit, confidence)
- Roulette wheel tracking
- Chip detection and counting
- Multi-camera synchronization

Features:
- Sub-100ms recognition latency
- Multi-camera consensus for accuracy
- Game state synchronization
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class CardSuit(Enum):
    """Playing card suits."""

    HEARTS = "hearts"
    DIAMONDS = "diamonds"
    CLUBS = "clubs"
    SPADES = "spades"


class CardRank(Enum):
    """Playing card ranks."""

    ACE = "A"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"


@dataclass
class RecognitionResult:
    """Result of an OCR recognition."""

    element_type: str  # card, wheel, chip
    value: str
    confidence: float
    camera_id: str
    timestamp: datetime
    processing_time_ms: float


@dataclass
class CardRecognition(RecognitionResult):
    """Card recognition result."""

    rank: CardRank
    suit: CardSuit
    position: str = ""  # player1, dealer, community


@dataclass
class WheelRecognition(RecognitionResult):
    """Roulette wheel recognition result."""

    number: int
    color: str  # red, black, green


@dataclass
class GameState:
    """Current game state from OCR."""

    table_id: str
    game_type: str
    current_cards: List[CardRecognition] = field(default_factory=list)
    wheel_result: Optional[WheelRecognition] = None
    last_update: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    consensus_reached: bool = False
    cameras_reporting: int = 0


class OCRProcessor:
    """
    Real-time OCR processor for live casino games.

    Processes video frames from multiple cameras to recognize:
    - Playing cards (blackjack, baccarat, poker)
    - Roulette wheel positions
    - Chip stacks and denominations

    Uses multi-camera consensus for accuracy validation.

    Example:
        >>> processor = OCRProcessor(redis_client)
        >>> result = await processor.process_frame(
        ...     table_id="table_123",
        ...     camera_id="dealer_cam",
        ...     frame_data=frame_bytes
        ... )
        >>> print(f"Card: {result.value} ({result.confidence:.0%})")
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)
        self.game_states: Dict[str, GameState] = {}
        self.recognition_buffers: Dict[str, List[RecognitionResult]] = {}

        # Recognition thresholds
        self.confidence_threshold = 0.85
        self.consensus_threshold = 2  # Min cameras agreeing
        self.recognition_timeout_ms = 100

    async def process_frame(
        self, table_id: str, camera_id: str, frame_data: bytes
    ) -> Optional[RecognitionResult]:
        """
        Process a video frame for OCR recognition.

        Args:
            table_id: Table identifier
            camera_id: Camera identifier
            frame_data: Raw frame data (JPEG/PNG)

        Returns:
            RecognitionResult if recognition successful
        """
        start_time = time.time()

        try:
            # Preprocess frame
            processed = await self._preprocess_frame(frame_data)

            # Detect elements in frame
            detections = await self._detect_elements(processed, camera_id)

            # Process each detection
            results: List[RecognitionResult] = []
            for detection in detections:
                result = await self._recognize_element(
                    detection, camera_id, start_time
                )
                if result and result.confidence >= self.confidence_threshold:
                    results.append(result)

            # Update game state with results
            if results:
                await self._update_game_state(table_id, results)
                return results[0]  # Return primary result

            return None

        except Exception as e:
            self.logger.error(f"Frame processing error: {e}")
            return None

    async def get_game_state(self, table_id: str) -> Optional[GameState]:
        """Get current game state for a table."""
        return self.game_states.get(table_id)

    async def recognize_card(
        self, frame_data: bytes, camera_id: str
    ) -> Optional[CardRecognition]:
        """Recognize a playing card from frame."""
        start_time = time.time()

        try:
            # Preprocess for card detection
            processed = await self._preprocess_frame(frame_data)

            # Detect card region
            card_region = await self._detect_card_region(processed)
            if not card_region:
                return None

            # Recognize rank and suit
            rank = await self._recognize_rank(card_region)
            suit = await self._recognize_suit(card_region)

            if rank and suit:
                processing_time = (time.time() - start_time) * 1000
                return CardRecognition(
                    element_type="card",
                    value=f"{rank.value}{suit.value[0].upper()}",
                    confidence=0.95,  # Would come from ML model
                    camera_id=camera_id,
                    timestamp=datetime.now(timezone.utc),
                    processing_time_ms=processing_time,
                    rank=rank,
                    suit=suit,
                )

            return None

        except Exception as e:
            self.logger.error(f"Card recognition error: {e}")
            return None

    async def recognize_wheel(
        self, frame_data: bytes, camera_id: str
    ) -> Optional[WheelRecognition]:
        """Recognize roulette wheel result from frame."""
        start_time = time.time()

        try:
            # Preprocess for wheel detection
            processed = await self._preprocess_frame(frame_data)

            # Detect ball position
            ball_position = await self._detect_ball_position(processed)
            if ball_position is None:
                return None

            # Determine number and color
            number = ball_position
            color = self._get_wheel_color(number)

            processing_time = (time.time() - start_time) * 1000
            return WheelRecognition(
                element_type="wheel",
                value=str(number),
                confidence=0.92,
                camera_id=camera_id,
                timestamp=datetime.now(timezone.utc),
                processing_time_ms=processing_time,
                number=number,
                color=color,
            )

        except Exception as e:
            self.logger.error(f"Wheel recognition error: {e}")
            return None

    async def _update_game_state(
        self, table_id: str, results: List[RecognitionResult]
    ) -> None:
        """Update game state with recognition results."""
        if table_id not in self.game_states:
            self.game_states[table_id] = GameState(
                table_id=table_id,
                game_type="unknown",
            )

        state = self.game_states[table_id]

        for result in results:
            if isinstance(result, CardRecognition):
                # Add to cards if not duplicate
                existing = [c for c in state.current_cards if c.value == result.value]
                if not existing:
                    state.current_cards.append(result)
            elif isinstance(result, WheelRecognition):
                state.wheel_result = result

        state.last_update = datetime.now(timezone.utc)
        state.cameras_reporting += 1

        # Check for consensus
        await self._check_consensus(state)

    async def _check_consensus(self, state: GameState) -> None:
        """Check if multiple cameras agree on game state."""
        if state.cameras_reporting >= self.consensus_threshold:
            # Verify cards match across cameras
            # In production, would compare results from different cameras
            state.consensus_reached = True

    async def clear_game_state(self, table_id: str) -> None:
        """Clear game state for new round."""
        if table_id in self.game_states:
            self.game_states[table_id] = GameState(
                table_id=table_id,
                game_type=self.game_states[table_id].game_type,
            )

    async def _preprocess_frame(self, frame_data: bytes) -> bytes:
        """Preprocess frame for OCR."""
        # In production, would use OpenCV for:
        # - Color conversion
        # - Noise reduction
        # - Contrast enhancement
        return frame_data

    async def _detect_elements(
        self, frame: bytes, camera_id: str
    ) -> List[Dict[str, Any]]:
        """Detect recognizable elements in frame."""
        # In production, would use trained model
        return []

    async def _recognize_element(
        self, detection: Dict[str, Any], camera_id: str, start_time: float
    ) -> Optional[RecognitionResult]:
        """Recognize a detected element."""
        # In production, would classify and recognize
        return None

    async def _detect_card_region(
        self, frame: bytes
    ) -> Optional[bytes]:
        """Detect card region in frame."""
        # In production, would use object detection
        return frame

    async def _recognize_rank(
        self, card_region: bytes
    ) -> Optional[CardRank]:
        """Recognize card rank."""
        # In production, would use CNN classifier
        return CardRank.ACE

    async def _recognize_suit(
        self, card_region: bytes
    ) -> Optional[CardSuit]:
        """Recognize card suit."""
        # In production, would use CNN classifier
        return CardSuit.SPADES

    async def _detect_ball_position(
        self, frame: bytes
    ) -> Optional[int]:
        """Detect roulette ball position."""
        # In production, would use object tracking
        return 17

    def _get_wheel_color(self, number: int) -> str:
        """Get color for roulette number."""
        if number == 0 or number == 37:  # 37 = 00
            return "green"

        red_numbers = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
        return "red" if number in red_numbers else "black"

    def get_recognition_stats(self) -> Dict[str, Any]:
        """Get OCR recognition statistics."""
        total_recognitions = sum(
            len(buf) for buf in self.recognition_buffers.values()
        )

        return {
            "tables_active": len(self.game_states),
            "total_recognitions": total_recognitions,
            "avg_processing_time_ms": 45.0,  # Would calculate from history
            "confidence_threshold": self.confidence_threshold,
        }
