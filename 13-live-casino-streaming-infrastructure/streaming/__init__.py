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
Live Casino Streaming Infrastructure

This module provides comprehensive streaming infrastructure for live casino platforms:
- Multi-provider studio integration (Evolution, Pragmatic, Ezugi)
- WebRTC and HLS streaming support
- Real-time OCR for card/wheel recognition
- Sub-500ms latency optimization

Key Components:
- StudioIntegrationManager: Multi-provider failover and load balancing
- StreamManager: WebRTC/HLS stream management
- OCRProcessor: Real-time card and wheel recognition
"""

from .studio_integration import StudioIntegrationManager, StudioConfig  # ty:ignore[unresolved-import]
from .stream_manager import StreamManager, StreamConfig  # ty:ignore[unresolved-import]
from .ocr_processor import OCRProcessor, RecognitionResult  # ty:ignore[unresolved-import]

__all__ = [
    "StudioIntegrationManager",
    "StudioConfig",
    "StreamManager",
    "StreamConfig",
    "OCRProcessor",
    "RecognitionResult",
]

__version__ = "1.0.0"
