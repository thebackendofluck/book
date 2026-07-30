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
Model Serving Service for Fraud Detection

This service provides real-time model inference for fraud detection
using trained ML models with REST API endpoints.
"""

import os
import asyncio
import json
import pickle
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import mlflow  # ty:ignore[unresolved-import]
import mlflow.sklearn  # ty:ignore[unresolved-import]
import mlflow.xgboost  # ty:ignore[unresolved-import]

from ..data_ingestion.metrics import MetricsCollector  # ty:ignore[unresolved-import]

logger = structlog.get_logger(__name__)

# Initialize FastAPI app

# Browser origins allowed to call this service. A wildcard combined with
# allow_credentials lets any site read authenticated responses, so the
# origins have to be named.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

app = FastAPI(
    title="Fraud Detection - Model Serving Service",
    description="Real-time fraud detection model inference",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
metrics_collector = MetricsCollector()

# Global variables for models and preprocessing
models = {}
preprocessing = {}
model_metadata = {}


class PredictionRequest(BaseModel):
    """Request model for fraud prediction"""

    player_id: str = Field(..., description="Player identifier")
    features: Dict[str, Any] = Field(..., description="Engineered features for prediction")
    model_name: Optional[str] = Field("ensemble", description="Model to use for prediction")
    threshold: Optional[float] = Field(0.5, description="Decision threshold")


class PredictionResponse(BaseModel):
    """Response model for fraud prediction"""

    player_id: str
    fraud_probability: float
    fraud_prediction: int
    model_used: str
    confidence_score: float
    processing_time_ms: float
    timestamp: str
    feature_contributions: Optional[Dict[str, float]] = None


class BatchPredictionRequest(BaseModel):
    """Request model for batch fraud prediction"""

    predictions: List[PredictionRequest] = Field(..., description="List of prediction requests")
    batch_id: Optional[str] = Field(None, description="Optional batch identifier")


class BatchPredictionResponse(BaseModel):
    """Response model for batch fraud prediction"""

    batch_id: str
    total_predictions: int
    predictions: List[PredictionResponse]
    processing_time_ms: float
    timestamp: str


class ModelInfo(BaseModel):
    """Model information response"""

    model_name: str
    model_type: str
    version: str
    created_at: str
    metrics: Dict[str, Any]
    feature_columns: List[str]


@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Load models and preprocessing objects on startup"""

    global models, preprocessing, model_metadata

    model_dir = Path("models/initial")

    if not model_dir.exists():
        logger.warning("Model directory not found, models will need to be loaded manually")
        return

    try:
        # Load preprocessing objects
        preprocessing_path = model_dir / "preprocessing.pkl"
        if preprocessing_path.exists():
            with open(preprocessing_path, 'rb') as f:
                preprocessing = pickle.load(f)
            logger.info("Preprocessing objects loaded")

        # Load model metadata
        metadata_path = model_dir / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, 'r') as f:
                model_metadata = json.load(f)
            logger.info("Model metadata loaded")

        # Load individual models
        for model_file in model_dir.glob("*.pkl"):
            if model_file.name not in ["preprocessing.pkl"]:
                model_name = model_file.stem
                with open(model_file, 'rb') as f:
                    models[model_name] = pickle.load(f)
                logger.info(f"Model loaded: {model_name}")

        logger.info(f"All models loaded successfully: {list(models.keys())}")

    except Exception as e:
        logger.error("Failed to load models", error=str(e))
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint"""

    global models

    model_count = len(models)
    status = "healthy" if model_count > 0 else "degraded"

    return {
        "status": status,
        "timestamp": pd.Timestamp.now().isoformat(),
        "models_loaded": model_count,
        "model_names": list(models.keys())
    }


@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""

    global models

    ready = len(models) > 0

    return {
        "status": "ready" if ready else "not ready",
        "timestamp": pd.Timestamp.now().isoformat()
    }


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint"""

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return generate_latest(), {"Content-Type": CONTENT_TYPE_LATEST}


@app.post("/api/v1/predict", response_model=PredictionResponse)
async def predict_fraud(request: PredictionRequest, background_tasks: BackgroundTasks):
    """Predict fraud probability for a single player"""

    start_time = time.time()

    try:
        with metrics_collector.time_event_processing("fraud_prediction"):
            player_id = request.player_id
            features = request.features
            model_name = request.model_name
            threshold = request.threshold

            # Apply default threshold if None
            threshold = threshold or 0.5

            # Validate model exists
            if model_name not in models:
                available_models = list(models.keys())
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model_name}' not found. Available models: {available_models}"
                )

            # Preprocess features
            processed_features = preprocess_features(features, model_name)

            # Make prediction
            model = models[model_name]
            prediction_result = make_prediction(model, processed_features, model_name, threshold)

            # Calculate processing time
            processing_time = (time.time() - start_time) * 1000

            # Get feature contributions if available
            feature_contributions = get_feature_contributions(model, processed_features, model_name)

            # Update metrics
            metrics_collector.increment_counter("predictions_total", {"model": model_name})
            metrics_collector.observe_histogram("prediction_latency_seconds",
                                              processing_time / 1000, {"model": model_name})

            response = PredictionResponse(
                player_id=player_id,
                fraud_probability=prediction_result["probability"],
                fraud_prediction=prediction_result["prediction"],
                model_used=model_name,
                confidence_score=prediction_result["confidence"],
                processing_time_ms=processing_time,
                timestamp=pd.Timestamp.now().isoformat(),
                feature_contributions=feature_contributions
            )

            return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Prediction failed", error=str(e), player_id=request.player_id)
        metrics_collector.increment_counter("prediction_errors_total", {"error_type": "processing"})
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


@app.post("/api/v1/predict/batch", response_model=BatchPredictionResponse)
async def predict_fraud_batch(request: BatchPredictionRequest, background_tasks: BackgroundTasks):
    """Predict fraud for multiple players"""

    start_time = time.time()
    batch_id = request.batch_id or f"batch_{int(start_time * 1000)}"

    try:
        with metrics_collector.time_event_processing("batch_fraud_prediction"):
            predictions = []

            for pred_request in request.predictions:
                try:
                    # Create individual prediction request
                    single_request = PredictionRequest(**pred_request.model_dump())
                    result = await predict_fraud(single_request, BackgroundTasks())

                    # Convert to dict for batch response
                    predictions.append(result.model_dump())

                except Exception as e:
                    logger.error("Batch prediction failed for player",
                               error=str(e), player_id=pred_request.player_id)
                    # Add error result
                    predictions.append({
                        "player_id": pred_request.player_id,
                        "error": str(e)
                    })

            processing_time = (time.time() - start_time) * 1000

            # Update metrics
            metrics_collector.increment_counter("batch_predictions_total",
                                              {"batch_size": len(request.predictions)})

            return BatchPredictionResponse(
                batch_id=batch_id,
                total_predictions=len(request.predictions),
                predictions=predictions,
                processing_time_ms=processing_time,
                timestamp=pd.Timestamp.now().isoformat()
            )

    except Exception as e:
        logger.error("Batch prediction failed", error=str(e), batch_id=batch_id)
        metrics_collector.increment_counter("batch_prediction_errors_total", {"error_type": "processing"})
        raise HTTPException(status_code=500, detail=f"Batch prediction failed: {str(e)}")


@app.get("/api/v1/models", response_model=List[ModelInfo])
async def list_models():
    """List available models"""

    global models, model_metadata

    model_list = []

    for model_name, model in models.items():
        # Get model type
        model_type = type(model).__name__

        # Get metadata if available
        metadata = model_metadata.get(model_name, {})

        model_info = ModelInfo(
            model_name=model_name,
            model_type=model_type,
            version=metadata.get("version", "1.0.0"),
            created_at=metadata.get("created_at", pd.Timestamp.now().isoformat()),
            metrics={},  # Would be populated from MLflow
            feature_columns=preprocessing.get("feature_columns", [])
        )

        model_list.append(model_info)

    return model_list


@app.get("/api/v1/models/{model_name}")
async def get_model_info(model_name: str):
    """Get detailed information about a specific model"""

    global models

    if model_name not in models:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    model = models[model_name]

    # Get basic model information
    info = {
        "model_name": model_name,
        "model_type": type(model).__name__,
        "parameters": getattr(model, 'get_params', lambda: {})(),
        "feature_columns": preprocessing.get("feature_columns", [])
    }

    # Add model-specific information
    if hasattr(model, 'feature_importances_'):
        info["feature_importance"] = dict(zip(
            preprocessing.get("feature_columns", []),
            model.feature_importances_
        ))

    if hasattr(model, 'get_booster'):
        # XGBoost model
        booster = model.get_booster()
        info["feature_importance"] = booster.get_score(importance_type='gain')

    return info


def preprocess_features(features: Dict[str, Any], model_name: str) -> pd.DataFrame:
    """Preprocess features for model prediction"""

    global preprocessing

    # Convert to DataFrame
    df = pd.DataFrame([features])

    # Handle missing values
    df = df.fillna(df.mean(numeric_only=True))
    df = df.fillna(0)

    # Encode categorical variables
    if "encoders" in preprocessing:
        for col, encoder in preprocessing["encoders"].items():
            if col in df.columns:
                df[col] = df[col].astype(str)
                # Handle unknown categories
                known_categories = set(encoder.classes_)
                df[col] = df[col].apply(lambda x: x if x in known_categories else 'unknown')

                if 'unknown' not in encoder.classes_:
                    encoder.classes_ = np.append(encoder.classes_, 'unknown')

                df[col] = encoder.transform(df[col])

    # Scale numerical features
    if "scalers" in preprocessing and "features" in preprocessing["scalers"]:
        scaler = preprocessing["scalers"]["features"]
        numerical_cols = df.select_dtypes(include=[np.number]).columns
        df[numerical_cols] = scaler.transform(df[numerical_cols])

    # Ensure correct feature order
    if "feature_columns" in preprocessing:
        feature_cols = preprocessing["feature_columns"]
        # Add missing columns with 0
        for col in feature_cols:
            if col not in df.columns:
                df[col] = 0
        # Reorder columns
        df = df[feature_cols]

    return df


def make_prediction(model, features_df: pd.DataFrame, model_name: str, threshold: float) -> Dict[str, Any]:
    """Make fraud prediction using the specified model"""

    if hasattr(model, 'predict_proba'):
        # Supervised model with probability prediction
        probabilities = model.predict_proba(features_df)
        fraud_probability = probabilities[0][1]  # Probability of positive class (fraud)

    elif hasattr(model, 'score_samples'):
        # Unsupervised model (Isolation Forest)
        scores = model.score_samples(features_df)
        # Convert anomaly score to fraud probability (lower score = more anomalous = higher fraud probability)
        fraud_probability = 1 / (1 + np.exp(scores[0]))  # Sigmoid transformation

    elif hasattr(model, 'predict'):
        # Model with only binary prediction
        prediction = model.predict(features_df)[0]
        fraud_probability = float(prediction)

    else:
        raise ValueError(f"Model {model_name} does not support prediction")

    # Make binary prediction
    fraud_prediction = 1 if fraud_probability >= threshold else 0

    # Calculate confidence score (distance from threshold)
    confidence = abs(fraud_probability - 0.5) * 2  # Scale to 0-1

    return {
        "probability": float(fraud_probability),
        "prediction": int(fraud_prediction),
        "confidence": float(confidence)
    }


def get_feature_contributions(model, features_df: pd.DataFrame, model_name: str) -> Optional[Dict[str, float]]:
    """Get feature contributions for the prediction"""

    try:
        if hasattr(model, 'feature_importances_'):
            # Tree-based models
            contributions = dict(zip(
                preprocessing.get("feature_columns", []),
                model.feature_importances_
            ))
            return contributions

        elif hasattr(model, 'get_booster'):
            # XGBoost model
            booster = model.get_booster()
            contributions = booster.get_score(importance_type='gain')
            return contributions

        else:
            return None

    except Exception as e:
        logger.error("Failed to get feature contributions", error=str(e))
        return None


@app.post("/api/v1/models/reload")
async def reload_models():
    """Reload models from disk (for development/testing)"""

    try:
        # Clear existing models
        global models, preprocessing, model_metadata
        models.clear()
        preprocessing.clear()
        model_metadata.clear()

        # Reload from startup
        await startup_event()

        return {
            "status": "success",
            "models_loaded": len(models),
            "message": f"Reloaded {len(models)} models"
        }

    except Exception as e:
        logger.error("Model reload failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Model reload failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "model_serving:app",
        host="0.0.0.0",
        port=8082,
        reload=True
    )