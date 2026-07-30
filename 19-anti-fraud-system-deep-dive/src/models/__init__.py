# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
ML Models Package

This package contains machine learning models and serving infrastructure
for the fraud detection system.
"""

__version__ = "1.0.0"
__author__ = "Fraud Detection Team"

from .train_initial_models import FraudModelTrainer  # ty:ignore[unresolved-import]
from .model_serving import app as model_serving_app  # ty:ignore[unresolved-import]

__all__ = ["FraudModelTrainer", "model_serving_app"]