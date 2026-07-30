#!/usr/bin/env python3
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
OCR Card Recognition Pipeline for Live Casino
Chapter 6 - Live Casino Streaming Infrastructure

Purpose: Real-time card detection and recognition from live casino video streams
using OpenCV + deep learning. Processes overhead camera feeds to identify:
  - Card rank (A, 2-10, J, Q, K)
  - Card suit (Hearts, Diamonds, Clubs, Spades)
  - Card position and dealing sequence

Architecture:
  Camera Feed -> Frame Capture -> ROI Detection -> Card Classification -> Result Emit

Features:
  - YOLOv8-based card detection (GPU-accelerated)
  - Template matching fallback for edge cases
  - Multi-card tracking with sequence ordering
  - Cross-camera validation (overhead + close-up agreement)
  - Confidence scoring with manual review triggers
  - Game-state-aware processing (only scan during deal phases)

Dependencies:
  pip install opencv-python-headless numpy ultralytics torch prometheus-client redis

Usage:
  python card_recognition.py --camera overhead --table-id 42
  python card_recognition.py --camera closeup --table-id 42 --gpu 0
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# =============================================================================
# Configuration
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis.live-casino:6379/0")
MODEL_PATH = os.getenv("MODEL_PATH", "/opt/models/card-detector-yolov8n.pt")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
MIN_CROSS_CAMERA_CONFIDENCE = float(os.getenv("MIN_CROSS_CAMERA_CONFIDENCE", "0.90"))
MANUAL_REVIEW_THRESHOLD = float(os.getenv("MANUAL_REVIEW_THRESHOLD", "0.70"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9101"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("card-ocr")


# =============================================================================
# Card Data Model
# =============================================================================
class Suit(str, Enum):
    HEARTS = "H"
    DIAMONDS = "D"
    CLUBS = "C"
    SPADES = "S"


class Rank(str, Enum):
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


# YOLOv8 class index mapping (from training)
CLASS_NAMES = [
    "AH", "2H", "3H", "4H", "5H", "6H", "7H", "8H", "9H", "10H", "JH", "QH", "KH",
    "AD", "2D", "3D", "4D", "5D", "6D", "7D", "8D", "9D", "10D", "JD", "QD", "KD",
    "AC", "2C", "3C", "4C", "5C", "6C", "7C", "8C", "9C", "10C", "JC", "QC", "KC",
    "AS", "2S", "3S", "4S", "5S", "6S", "7S", "8S", "9S", "10S", "JS", "QS", "KS",
    "CARD_BACK",
]


@dataclass
class DetectedCard:
    rank: str
    suit: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) in pixels
    position_index: int  # Left-to-right dealing order
    camera: str
    timestamp: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        return f"{self.rank}{self.suit}"

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "suit": self.suit,
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "bbox": list(self.bbox),
            "position": self.position_index,
            "camera": self.camera,
            "timestamp": self.timestamp,
        }


@dataclass
class RecognitionResult:
    table_id: str
    round_id: str
    cards: list[DetectedCard]
    processing_time_ms: float
    frame_number: int
    cross_validated: bool = False
    needs_manual_review: bool = False


# =============================================================================
# Prometheus Metrics
# =============================================================================
metrics_cards_detected = Counter(
    "ocr_cards_detected_total",
    "Total cards detected",
    ["table_id", "camera"],
)
metrics_detection_confidence = Histogram(
    "ocr_detection_confidence",
    "Card detection confidence scores",
    ["table_id"],
    buckets=[0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99],
)
metrics_processing_time = Histogram(
    "ocr_processing_time_ms",
    "Frame processing time in milliseconds",
    ["table_id", "camera"],
    buckets=[5, 10, 20, 30, 50, 75, 100, 150, 200],
)
metrics_manual_reviews = Counter(
    "ocr_manual_review_triggers_total",
    "Times manual review was triggered due to low confidence",
    ["table_id"],
)
metrics_cross_validation_failures = Counter(
    "ocr_cross_validation_failures_total",
    "Cross-camera validation mismatches",
    ["table_id"],
)
metrics_fps = Gauge(
    "ocr_processing_fps",
    "Current OCR processing frames per second",
    ["table_id", "camera"],
)


# =============================================================================
# Card Detector (YOLOv8-based)
# =============================================================================
class CardDetector:
    """
    YOLOv8-based playing card detector optimized for live casino overhead cameras.

    The model is trained on a custom dataset of playing cards photographed
    under studio lighting conditions with various table felt colors.
    """

    def __init__(self, model_path: str, device: str = "cpu"):
        self.device = device
        self.model = None
        self.model_path = model_path

        # ROI (Region of Interest) configuration for different table layouts
        self.roi_configs = {
            "blackjack": {
                "dealer_area": (0.3, 0.0, 0.7, 0.3),    # Top center
                "player_spots": [
                    (0.05, 0.5, 0.25, 0.85),   # Seat 1 (far left)
                    (0.20, 0.55, 0.40, 0.90),   # Seat 2
                    (0.35, 0.60, 0.55, 0.95),   # Seat 3 (center)
                    (0.50, 0.60, 0.70, 0.95),   # Seat 4
                    (0.65, 0.55, 0.85, 0.90),   # Seat 5
                    (0.80, 0.50, 0.95, 0.85),   # Seat 6 (far right)
                ],
            },
            "baccarat": {
                "player_area": (0.1, 0.5, 0.45, 0.9),
                "banker_area": (0.55, 0.5, 0.9, 0.9),
            },
        }

    def load_model(self):
        """Load the YOLOv8 model."""
        try:
            from ultralytics import YOLO  # ty:ignore[unresolved-import]
            self.model = YOLO(self.model_path)
            if self.device != "cpu":
                self.model.to(self.device)
            logger.info("Card detection model loaded from %s (device: %s)", self.model_path, self.device)
        except FileNotFoundError:
            logger.warning("Model not found at %s, using template matching fallback", self.model_path)
            self.model = None

    def detect_cards(
        self,
        frame: np.ndarray,
        game_type: str = "blackjack",
        camera: str = "overhead",
    ) -> list[DetectedCard]:
        """
        Detect and classify cards in a video frame.

        Args:
            frame: BGR image from OpenCV capture
            game_type: Table game type for ROI selection
            camera: Camera angle identifier

        Returns:
            List of detected cards sorted by x-position (dealing order)
        """
        if self.model is not None:
            return self._detect_yolo(frame, camera)
        else:
            return self._detect_template(frame, camera)

    def _detect_yolo(self, frame: np.ndarray, camera: str) -> list[DetectedCard]:
        """YOLOv8 inference for card detection."""
        start = time.time()

        # Run inference
        results = self.model(
            frame,
            conf=0.5,           # Initial confidence filter (refined later)
            iou=0.45,           # NMS IoU threshold
            max_det=20,         # Max 20 cards per frame
            verbose=False,
        )  # ty:ignore[call-non-callable]

        cards = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i])
                conf = float(boxes.conf[i])
                x1, y1, x2, y2 = boxes.xyxy[i].tolist()

                if cls_id >= len(CLASS_NAMES):
                    continue

                class_name = CLASS_NAMES[cls_id]

                # Skip card backs
                if class_name == "CARD_BACK":
                    continue

                # Parse rank and suit
                if len(class_name) == 2:
                    rank, suit = class_name[0], class_name[1]
                elif len(class_name) == 3:
                    rank, suit = class_name[:2], class_name[2]
                else:
                    continue

                cards.append(DetectedCard(
                    rank=rank,
                    suit=suit,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    position_index=0,
                    camera=camera,
                ))

        # Sort by x-position (left to right = dealing order)
        cards.sort(key=lambda c: c.bbox[0])
        for idx, card in enumerate(cards):
            card.position_index = idx

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("YOLO detection: %d cards in %.1f ms", len(cards), elapsed_ms)

        return cards

    def _detect_template(self, frame: np.ndarray, camera: str) -> list[DetectedCard]:
        """
        Template matching fallback when YOLO model is unavailable.

        Uses contour detection + color-based suit classification + template
        matching for rank identification.
        """
        start = time.time()
        cards = []

        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Adaptive threshold to isolate white cards from felt
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2,
        )

        # Find contours
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )

        # Filter contours by area (card-sized objects)
        frame_area = frame.shape[0] * frame.shape[1]
        min_card_area = frame_area * 0.005  # Card is at least 0.5% of frame
        max_card_area = frame_area * 0.05   # Card is at most 5% of frame

        card_contours = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if min_card_area < area < max_card_area:
                # Check aspect ratio (cards are roughly 2.5:3.5)
                x, y, w, h = cv2.boundingRect(cnt)
                aspect = w / h if h > 0 else 0
                if 0.5 < aspect < 0.85:  # Portrait orientation
                    card_contours.append((cnt, (x, y, x + w, y + h)))

        for idx, (cnt, bbox) in enumerate(sorted(card_contours, key=lambda c: c[1][0])):
            x1, y1, x2, y2 = bbox
            card_roi = frame[y1:y2, x1:x2]

            if card_roi.size == 0:
                continue

            # Extract corner region for rank/suit identification
            h, w = card_roi.shape[:2]
            corner = card_roi[0:int(h * 0.35), 0:int(w * 0.35)]

            # Classify suit by color analysis
            hsv_corner = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
            red_mask = cv2.inRange(hsv_corner, (0, 100, 100), (10, 255, 255))  # ty:ignore[no-matching-overload]
            red_mask |= cv2.inRange(hsv_corner, (160, 100, 100), (180, 255, 255))  # ty:ignore[no-matching-overload]
            red_ratio = np.count_nonzero(red_mask) / max(red_mask.size, 1)

            suit = "H" if red_ratio > 0.15 else "S"  # Simplified: red = hearts, black = spades

            # Rank estimation via contour complexity in corner
            corner_gray = cv2.cvtColor(corner, cv2.COLOR_BGR2GRAY)
            _, corner_thresh = cv2.threshold(corner_gray, 127, 255, cv2.THRESH_BINARY_INV)
            corner_contours, _ = cv2.findContours(
                corner_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
            )

            # Simplified rank detection (production would use trained classifier)
            rank = "?"
            if len(corner_contours) > 0:
                largest = max(corner_contours, key=cv2.contourArea)  # ty:ignore[no-matching-overload]
                hull = cv2.convexHull(largest)
                hull_area = cv2.contourArea(hull)
                solidity = cv2.contourArea(largest) / max(hull_area, 1)

                # Heuristic rank classification
                if solidity > 0.85:
                    rank = "10"  # Dense, blocky
                elif solidity > 0.7:
                    rank = "K"   # Complex shape
                else:
                    rank = "A"   # Simple shape

            cards.append(DetectedCard(
                rank=rank,
                suit=suit,
                confidence=0.60,  # Template matching has lower confidence
                bbox=bbox,
                position_index=idx,
                camera=camera,
            ))

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("Template detection: %d cards in %.1f ms", len(cards), elapsed_ms)

        return cards


# =============================================================================
# Cross-Camera Validator
# =============================================================================
class CrossCameraValidator:
    """
    Validates card detections across multiple camera angles.
    Requires overhead + close-up cameras to agree on the same cards
    before emitting a confirmed result.
    """

    def __init__(self, redis_client: redis.Redis, table_id: str):
        self.redis = redis_client
        self.table_id = table_id
        self.pending_key = f"ocr:pending:{table_id}"

    def submit_detection(self, camera: str, cards: list[DetectedCard]) -> Optional[list[DetectedCard]]:
        """
        Submit a detection from one camera and check for cross-camera agreement.

        Returns validated cards if both cameras agree, None otherwise.
        """
        # Store detection in Redis with short TTL
        detection = {
            "camera": camera,
            "cards": [c.to_dict() for c in cards],
            "timestamp": time.time(),
        }
        self.redis.hset(self.pending_key, camera, json.dumps(detection))
        self.redis.expire(self.pending_key, 5)  # 5-second window for cross-validation

        # Check if we have detections from both cameras
        all_detections = self.redis.hgetall(self.pending_key)
        if len(all_detections) < 2:  # ty:ignore[invalid-argument-type]
            return None

        # Parse both camera results
        overhead_data = json.loads(all_detections.get(b"overhead", b"{}"))  # ty:ignore[possibly-missing-attribute]
        closeup_data = json.loads(all_detections.get(b"closeup", b"{}"))  # ty:ignore[possibly-missing-attribute]

        if not overhead_data.get("cards") or not closeup_data.get("cards"):
            return None

        # Compare card labels
        overhead_labels = {c["label"] for c in overhead_data["cards"] if c["confidence"] >= CONFIDENCE_THRESHOLD}
        closeup_labels = {c["label"] for c in closeup_data["cards"] if c["confidence"] >= CONFIDENCE_THRESHOLD}

        if overhead_labels == closeup_labels and len(overhead_labels) > 0:
            # Cameras agree -- use the detection with higher average confidence
            overhead_avg_conf = np.mean([c["confidence"] for c in overhead_data["cards"]])
            closeup_avg_conf = np.mean([c["confidence"] for c in closeup_data["cards"]])

            best = overhead_data if overhead_avg_conf >= closeup_avg_conf else closeup_data
            validated_cards = [
                DetectedCard(
                    rank=c["rank"],
                    suit=c["suit"],
                    confidence=c["confidence"],
                    bbox=tuple(c["bbox"]),
                    position_index=c["position"],
                    camera=c["camera"],
                    timestamp=c["timestamp"],
                )
                for c in best["cards"]
            ]

            # Clear pending detections
            self.redis.delete(self.pending_key)
            logger.info(
                "Cross-validation PASSED for table %s: %s",
                self.table_id,
                [c.label for c in validated_cards],
            )
            return validated_cards
        else:
            # Cameras disagree
            metrics_cross_validation_failures.labels(table_id=self.table_id).inc()
            logger.warning(
                "Cross-validation FAILED for table %s: overhead=%s vs closeup=%s",
                self.table_id, overhead_labels, closeup_labels,
            )
            return None


# =============================================================================
# Stream Processor (Main Pipeline)
# =============================================================================
class CardRecognitionPipeline:
    """
    Main pipeline that captures frames from a live stream, runs card detection,
    performs cross-camera validation, and publishes results.
    """

    def __init__(
        self,
        table_id: str,
        camera: str,
        stream_url: str,
        game_type: str = "blackjack",
        device: str = "cpu",
    ):
        self.table_id = table_id
        self.camera = camera
        self.stream_url = stream_url
        self.game_type = game_type
        self.device = device

        self.detector = CardDetector(MODEL_PATH, device)
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        self.validator = CrossCameraValidator(self.redis_client, table_id)

        self.running = False
        self.frame_count = 0
        self.fps_counter = 0
        self.last_fps_time = time.time()

    def start(self):
        """Initialize detector and start processing loop."""
        self.detector.load_model()
        self.running = True

        logger.info(
            "Starting card recognition: table=%s camera=%s stream=%s device=%s",
            self.table_id, self.camera, self.stream_url, self.device,
        )

        # Open video stream
        cap = cv2.VideoCapture(self.stream_url)
        if not cap.isOpened():
            logger.error("Failed to open stream: %s", self.stream_url)
            sys.exit(1)

        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffering for low latency

        try:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Frame capture failed, reconnecting...")
                    cap.release()
                    time.sleep(1)
                    cap = cv2.VideoCapture(self.stream_url)
                    continue

                self.frame_count += 1
                self.fps_counter += 1

                # Process every 3rd frame (10 fps from 30 fps source)
                if self.frame_count % 3 != 0:
                    continue

                # Check game state: only process during deal phases
                game_phase = self._get_game_phase()
                if game_phase not in ("dealing", "reveal"):
                    continue

                # Detect cards
                start_time = time.time()
                cards = self.detector.detect_cards(frame, self.game_type, self.camera)
                processing_ms = (time.time() - start_time) * 1000

                # Update metrics
                metrics_processing_time.labels(
                    table_id=self.table_id, camera=self.camera
                ).observe(processing_ms)

                for card in cards:
                    metrics_cards_detected.labels(
                        table_id=self.table_id, camera=self.camera
                    ).inc()
                    metrics_detection_confidence.labels(
                        table_id=self.table_id
                    ).observe(card.confidence)

                if cards:
                    # Check for low-confidence detections
                    min_conf = min(c.confidence for c in cards)
                    if min_conf < MANUAL_REVIEW_THRESHOLD:
                        metrics_manual_reviews.labels(table_id=self.table_id).inc()
                        self._trigger_manual_review(frame, cards)

                    # Submit to cross-camera validator
                    validated = self.validator.submit_detection(self.camera, cards)
                    if validated:
                        self._publish_result(validated, processing_ms)

                # Update FPS metric every second
                now = time.time()
                if now - self.last_fps_time >= 1.0:
                    metrics_fps.labels(
                        table_id=self.table_id, camera=self.camera
                    ).set(self.fps_counter)
                    self.fps_counter = 0
                    self.last_fps_time = now

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            cap.release()
            self.running = False

    def _get_game_phase(self) -> str:
        """Query game engine for current round phase."""
        phase = self.redis_client.get(f"game:phase:{self.table_id}")
        if phase:
            return phase.decode("utf-8")  # ty:ignore[possibly-missing-attribute]
        return "dealing"  # Default to processing if phase unknown

    def _publish_result(self, cards: list[DetectedCard], processing_ms: float):
        """Publish validated card recognition result to Redis pub/sub."""
        round_id = self.redis_client.get(f"game:round:{self.table_id}")
        round_id = round_id.decode("utf-8") if round_id else "unknown"  # ty:ignore[possibly-missing-attribute]

        result = {
            "table_id": self.table_id,
            "round_id": round_id,
            "cards": [c.to_dict() for c in cards],
            "card_labels": [c.label for c in cards],
            "processing_time_ms": round(processing_ms, 2),
            "frame_number": self.frame_count,
            "cross_validated": True,
            "timestamp": time.time(),
        }

        # Publish to Redis for game engine consumption
        self.redis_client.publish(
            f"ocr:results:{self.table_id}",
            json.dumps(result),
        )

        # Store latest result for API access
        self.redis_client.setex(
            f"ocr:latest:{self.table_id}",
            30,
            json.dumps(result),
        )

        logger.info(
            "Published result: table=%s round=%s cards=%s (%.1f ms)",
            self.table_id, round_id,
            [c.label for c in cards],
            processing_ms,
        )

    def _trigger_manual_review(self, frame: np.ndarray, cards: list[DetectedCard]):
        """Save frame and flag for dealer/pit boss manual review."""
        review_dir = Path(f"/var/data/ocr-reviews/{self.table_id}")
        review_dir.mkdir(parents=True, exist_ok=True)

        filename = f"review_{int(time.time())}_{self.frame_count}.jpg"
        filepath = review_dir / filename

        # Draw detection boxes on frame for review
        annotated = frame.copy()
        for card in cards:
            x1, y1, x2, y2 = [int(v) for v in card.bbox]
            color = (0, 255, 0) if card.confidence >= CONFIDENCE_THRESHOLD else (0, 0, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                annotated,
                f"{card.label} ({card.confidence:.2f})",
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6, color, 2,
            )

        cv2.imwrite(str(filepath), annotated)

        # Notify review queue
        review_data = {
            "table_id": self.table_id,
            "image_path": str(filepath),
            "cards": [c.to_dict() for c in cards],
            "timestamp": time.time(),
        }
        self.redis_client.lpush("ocr:review_queue", json.dumps(review_data))

        logger.warning(
            "Manual review triggered for table %s: low confidence cards %s",
            self.table_id,
            [(c.label, c.confidence) for c in cards if c.confidence < CONFIDENCE_THRESHOLD],
        )


# =============================================================================
# CLI Entry Point
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Live Casino Card Recognition OCR")
    parser.add_argument("--table-id", required=True, help="Table identifier")
    parser.add_argument("--camera", default="overhead", choices=["overhead", "closeup", "wide", "dealer"])
    parser.add_argument("--stream-url", default=None, help="RTMP/SRT stream URL (auto-detected if omitted)")
    parser.add_argument("--game-type", default="blackjack", choices=["blackjack", "baccarat", "poker"])
    parser.add_argument("--gpu", default=None, help="GPU device ID (omit for CPU)")
    args = parser.parse_args()

    # Default stream URL based on table/camera
    if not args.stream_url:
        args.stream_url = f"rtmp://ingest.livecasino.internal:1935/live-casino/table{args.table_id}_{args.camera}"

    device = f"cuda:{args.gpu}" if args.gpu else "cpu"

    # Start Prometheus metrics server
    start_http_server(METRICS_PORT)
    logger.info("Metrics server started on port %d", METRICS_PORT)

    pipeline = CardRecognitionPipeline(
        table_id=args.table_id,
        camera=args.camera,
        stream_url=args.stream_url,
        game_type=args.game_type,
        device=device,
    )
    pipeline.start()


if __name__ == "__main__":
    main()
