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
Model Serving Service -- Real-Time Fraud Scoring

Loads trained ML models (XGBoost, Random Forest, Isolation Forest) and
exposes REST endpoints for single and batch fraud prediction.  Supports
ensemble scoring, feature-importance explanations, and hot-reload of
model artifacts.

Reference implementation for Chapter 41: Anti-Fraud System Deep Dive.
"""

import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import mlflow  # ty:ignore[unresolved-import]
import mlflow.sklearn  # ty:ignore[unresolved-import]
import mlflow.xgboost  # ty:ignore[unresolved-import]
import numpy as np
import pandas as pd
import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fraud Detection -- Model Serving",
    description="Real-time fraud scoring with ensemble ML models",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,  # ty:ignore[invalid-argument-type]
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state -- populated at startup
models: Dict[str, Any] = {}
preprocessing: Dict[str, Any] = {}
model_metadata: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class PredictionRequest(BaseModel):
    player_id: str = Field(..., description="Player identifier")
    features: Dict[str, Any] = Field(..., description="Engineered features")
    model_name: Optional[str] = Field("ensemble", description="Model to use")
    threshold: Optional[float] = Field(0.5, description="Decision threshold")


class PredictionResponse(BaseModel):
    player_id: str
    fraud_probability: float
    fraud_prediction: int
    model_used: str
    confidence_score: float
    processing_time_ms: float
    timestamp: str
    feature_contributions: Optional[Dict[str, float]] = None


class BatchPredictionRequest(BaseModel):
    predictions: List[PredictionRequest]
    batch_id: Optional[str] = None


class BatchPredictionResponse(BaseModel):
    batch_id: str
    total_predictions: int
    predictions: List[PredictionResponse]
    processing_time_ms: float
    timestamp: str


class ModelInfo(BaseModel):
    model_name: str
    model_type: str
    version: str
    created_at: str
    metrics: Dict[str, Any]
    feature_columns: List[str]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")  # ty:ignore[deprecated]
async def startup_event():
    """Load serialised models from disk."""
    global models, preprocessing, model_metadata

    model_dir = Path("models/initial")
    if not model_dir.exists():
        logger.warning("Model directory not found -- load models manually")
        return

    try:
        pp_path = model_dir / "preprocessing.pkl"
        if pp_path.exists():
            with open(pp_path, "rb") as f:
                preprocessing.update(pickle.load(f))
            logger.info("Preprocessing objects loaded")

        meta_path = model_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path) as f:
                model_metadata.update(json.load(f))

        for model_file in model_dir.glob("*.pkl"):
            if model_file.name != "preprocessing.pkl":
                with open(model_file, "rb") as f:
                    models[model_file.stem] = pickle.load(f)
                logger.info(f"Model loaded: {model_file.stem}")

        logger.info(f"Models ready: {list(models.keys())}")

    except Exception as e:
        logger.error("Failed to load models", error=str(e))
        raise


# ---------------------------------------------------------------------------
# Health / readiness / metrics
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "healthy" if models else "degraded",
        "timestamp": pd.Timestamp.now().isoformat(),
        "models_loaded": len(models),
        "model_names": list(models.keys()),
    }


@app.get("/ready")
async def readiness_check():
    return {
        "status": "ready" if models else "not ready",
        "timestamp": pd.Timestamp.now().isoformat(),
    }


@app.get("/metrics")
async def prometheus_metrics():
    return generate_latest()


# ---------------------------------------------------------------------------
# Prediction endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/predict", response_model=PredictionResponse)
async def predict_fraud(request: PredictionRequest, background_tasks: BackgroundTasks):
    """Score a single player's features and return fraud probability."""
    start = time.time()

    if request.model_name not in models:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{request.model_name}' not found. "
                   f"Available: {list(models.keys())}",
        )

    processed = preprocess_features(request.features, request.model_name)
    model = models[request.model_name]
    result = make_prediction(model, processed, request.model_name, request.threshold)  # ty:ignore[invalid-argument-type]
    contributions = get_feature_contributions(model, processed, request.model_name)

    return PredictionResponse(
        player_id=request.player_id,
        fraud_probability=result["probability"],
        fraud_prediction=result["prediction"],
        model_used=request.model_name,
        confidence_score=result["confidence"],
        processing_time_ms=(time.time() - start) * 1000,
        timestamp=pd.Timestamp.now().isoformat(),
        feature_contributions=contributions,
    )


@app.post("/api/v1/predict/batch", response_model=BatchPredictionResponse)
async def predict_fraud_batch(
    request: BatchPredictionRequest, background_tasks: BackgroundTasks
):
    """Score multiple players in a single request."""
    start = time.time()
    batch_id = request.batch_id or f"batch_{int(start * 1000)}"
    results = []

    for pr in request.predictions:
        try:
            r = await predict_fraud(pr, BackgroundTasks())
            results.append(r)
        except Exception as e:
            logger.error("Batch item failed", player_id=pr.player_id, error=str(e))

    return BatchPredictionResponse(
        batch_id=batch_id,
        total_predictions=len(request.predictions),
        predictions=results,
        processing_time_ms=(time.time() - start) * 1000,
        timestamp=pd.Timestamp.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

@app.get("/api/v1/models", response_model=List[ModelInfo])
async def list_models():
    result = []
    for name, model in models.items():
        meta = model_metadata.get(name, {})
        result.append(
            ModelInfo(
                model_name=name,
                model_type=type(model).__name__,
                version=meta.get("version", "1.0.0"),
                created_at=meta.get("created_at", pd.Timestamp.now().isoformat()),
                metrics={},
                feature_columns=preprocessing.get("feature_columns", []),
            )
        )
    return result


@app.get("/api/v1/models/{model_name}")
async def get_model_info(model_name: str):
    if model_name not in models:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")
    model = models[model_name]
    info = {
        "model_name": model_name,
        "model_type": type(model).__name__,
        "parameters": getattr(model, "get_params", lambda: {})(),
        "feature_columns": preprocessing.get("feature_columns", []),
    }
    if hasattr(model, "feature_importances_"):
        info["feature_importance"] = dict(
            zip(preprocessing.get("feature_columns", []), model.feature_importances_)
        )
    if hasattr(model, "get_booster"):
        info["feature_importance"] = model.get_booster().get_score(importance_type="gain")
    return info


@app.post("/api/v1/models/reload")
async def reload_models():
    """Hot-reload models from disk (zero-downtime update)."""
    models.clear()
    preprocessing.clear()
    model_metadata.clear()
    await startup_event()
    return {"status": "success", "models_loaded": len(models)}


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def preprocess_features(features: Dict[str, Any], model_name: str) -> pd.DataFrame:
    """Align raw feature dict to the trained model's expected schema."""
    df = pd.DataFrame([features]).fillna(0)

    if "encoders" in preprocessing:
        for col, encoder in preprocessing["encoders"].items():
            if col in df.columns:
                df[col] = df[col].astype(str)
                known = set(encoder.classes_)
                df[col] = df[col].apply(lambda x: x if x in known else "unknown")
                if "unknown" not in encoder.classes_:
                    encoder.classes_ = np.append(encoder.classes_, "unknown")
                df[col] = encoder.transform(df[col])

    if "scalers" in preprocessing and "features" in preprocessing["scalers"]:
        scaler = preprocessing["scalers"]["features"]
        num_cols = df.select_dtypes(include=[np.number]).columns
        df[num_cols] = scaler.transform(df[num_cols])

    if "feature_columns" in preprocessing:
        for col in preprocessing["feature_columns"]:
            if col not in df.columns:
                df[col] = 0
        df = df[preprocessing["feature_columns"]]

    return df


def make_prediction(
    model, features_df: pd.DataFrame, model_name: str, threshold: float
) -> Dict[str, Any]:
    """Run inference and return probability + binary decision."""
    if hasattr(model, "predict_proba"):
        prob = model.predict_proba(features_df)[0][1]
    elif hasattr(model, "score_samples"):
        # Isolation Forest: sigmoid-transform the anomaly score
        score = model.score_samples(features_df)[0]
        prob = 1 / (1 + np.exp(score))
    elif hasattr(model, "predict"):
        prob = float(model.predict(features_df)[0])
    else:
        raise ValueError(f"Model {model_name} does not support prediction")

    return {
        "probability": float(prob),
        "prediction": int(prob >= threshold),
        "confidence": float(abs(prob - 0.5) * 2),
    }


def get_feature_contributions(
    model, features_df: pd.DataFrame, model_name: str
) -> Optional[Dict[str, float]]:
    """Return feature-importance map (tree-based models only)."""
    try:
        if hasattr(model, "feature_importances_"):
            return dict(
                zip(preprocessing.get("feature_columns", []), model.feature_importances_)
            )
        if hasattr(model, "get_booster"):
            return model.get_booster().get_score(importance_type="gain")
    except Exception as e:
        logger.error("Feature contributions unavailable", error=str(e))
    return None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("model_serving:app", host="0.0.0.0", port=8082, reload=True)
