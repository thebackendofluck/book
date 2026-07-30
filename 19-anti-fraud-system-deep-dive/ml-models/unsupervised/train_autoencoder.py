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
Autoencoder Anomaly Detection for Fraud
=========================================

Deep learning autoencoder that learns compressed representations of normal
player behavior. Events that cannot be reconstructed well are anomalous.

Why Autoencoders for iGaming fraud:
  - Captures non-linear feature interactions that tree models miss
  - Learns latent representations of "normal" player behavior
  - Reconstruction error is a natural anomaly score
  - Can detect subtle, multi-dimensional anomalies
  - Embeddings can be used for player clustering and similarity search

Architecture:
  Input (n_features) -> Encoder (128 -> 64 -> 32) -> Bottleneck (16)
  -> Decoder (32 -> 64 -> 128) -> Output (n_features)

  Bottleneck dimension (16) forces the model to learn the most important
  patterns in normal behavior. Fraudulent behavior cannot be compressed
  into this bottleneck effectively, resulting in high reconstruction error.

Training strategy:
  - Train ONLY on legitimate transactions (or mostly legitimate)
  - Reconstruction error on fraud will be high because the model
    has never learned to reconstruct fraudulent patterns
  - Use MSE loss; anomaly threshold set at 95th or 99th percentile
"""

import json
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import torch  # ty:ignore[unresolved-import]
import torch.nn as nn  # ty:ignore[unresolved-import]
from torch.utils.data import DataLoader, TensorDataset  # ty:ignore[unresolved-import]
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ml.autoencoder")


FEATURE_NAMES = [
    "tx_count_1h", "tx_count_24h", "inter_event_ms", "inter_event_std_10",
    "rolling_avg_amount_20", "amount_cv_20", "bot_timing_score",
    "hour_deviation_zscore", "hours_since_last_activity",
    "bet_size_ratio", "bet_size_volatility", "martingale_count_10",
    "unique_games_played", "game_switch_count_20",
    "unique_payment_methods_30d", "failed_deposit_count_1h",
    "deposit_to_play_ratio", "velocity_24h_count",
    "ip_is_vpn", "ip_is_datacenter", "multi_account_device_count",
]


# =============================================================================
# Autoencoder Architecture
# =============================================================================

class FraudAutoencoder(nn.Module):
    """
    Symmetric autoencoder for anomaly detection.

    Architecture details:
      - BatchNorm after each layer for training stability
      - Dropout (0.2) for regularization (prevents memorizing noise)
      - LeakyReLU activation (better gradient flow than ReLU for reconstruction)
      - Symmetric decoder mirrors encoder architecture
    """

    def __init__(self, input_dim: int, bottleneck_dim: int = 16):
        super().__init__()
        self.input_dim = input_dim
        self.bottleneck_dim = bottleneck_dim

        # Encoder: compress input to bottleneck representation
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.BatchNorm1d(128),
            nn.LeakyReLU(0.1),
            nn.Dropout(0.2),

            nn.Linear(128, 64),
            nn.BatchNorm1d(64),
            nn.LeakyReLU(0.1),
            nn.Dropout(0.2),

            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.LeakyReLU(0.1),

            nn.Linear(32, bottleneck_dim),
            nn.BatchNorm1d(bottleneck_dim),
        )

        # Decoder: reconstruct input from bottleneck
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck_dim, 32),
            nn.BatchNorm1d(32),
            nn.LeakyReLU(0.1),

            nn.Linear(32, 64),
            nn.BatchNorm1d(64),
            nn.LeakyReLU(0.1),
            nn.Dropout(0.2),

            nn.Linear(64, 128),
            nn.BatchNorm1d(128),
            nn.LeakyReLU(0.1),
            nn.Dropout(0.2),

            nn.Linear(128, input_dim),
            # No activation on output: reconstruct raw scaled features
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass returns (reconstruction, latent_embedding)."""
        latent = self.encoder(x)
        reconstructed = self.decoder(latent)
        return reconstructed, latent

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """Get latent embedding only (for similarity search)."""
        return self.encoder(x)


# =============================================================================
# Training Pipeline
# =============================================================================

class AutoencoderFraudDetector:
    """
    Autoencoder-based anomaly detector for iGaming fraud.

    Training approach:
      1. Train on legitimate transactions only
      2. Compute reconstruction error distribution on training set
      3. Set anomaly threshold at configurable percentile (default: 95th)
      4. At inference: high reconstruction error = anomaly = potential fraud
    """

    def __init__(
        self,
        model_dir: str = "./models/autoencoder",
        bottleneck_dim: int = 16,
        threshold_percentile: float = 95.0,
        device: str = "auto",
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.bottleneck_dim = bottleneck_dim
        self.threshold_percentile = threshold_percentile

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.model: Optional[FraudAutoencoder] = None
        self.scaler: Optional[StandardScaler] = None
        self.anomaly_threshold: float = 0.0
        self.feature_names = FEATURE_NAMES
        self.training_metadata: dict = {}

        logger.info("Using device: %s", self.device)

    def train(
        self,
        X_train: np.ndarray,
        y_train: Optional[np.ndarray] = None,
        X_val: Optional[np.ndarray] = None,
        y_val: Optional[np.ndarray] = None,
        epochs: int = 100,
        batch_size: int = 512,
        learning_rate: float = 1e-3,
        feature_names: Optional[list[str]] = None,
    ) -> dict:
        """
        Train autoencoder on (mostly) legitimate transactions.

        If y_train is provided, filters to legitimate samples only for training.
        """
        if feature_names:
            self.feature_names = feature_names

        start_time = time.time()

        # Filter to legitimate samples if labels available
        if y_train is not None:
            legitimate_mask = y_train == 0
            X_legitimate = X_train[legitimate_mask]
            logger.info(
                "Filtered to %d legitimate samples (excluded %d fraud)",
                len(X_legitimate), np.sum(y_train == 1),
            )
        else:
            X_legitimate = X_train

        # Standardize features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_legitimate)

        # Create model
        input_dim = X_scaled.shape[1]
        self.model = FraudAutoencoder(input_dim, self.bottleneck_dim).to(self.device)

        # Create DataLoader
        dataset = TensorDataset(torch.FloatTensor(X_scaled))
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=True)

        # Training setup
        optimizer = torch.optim.Adam(self.model.parameters(), lr=learning_rate, weight_decay=1e-5)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-6
        )
        criterion = nn.MSELoss()

        logger.info(
            "Training autoencoder: %d samples, %d features, bottleneck=%d",
            len(X_scaled), input_dim, self.bottleneck_dim,
        )

        # Training loop
        best_loss = float("inf")
        best_state = None
        patience_counter = 0
        max_patience = 15

        for epoch in range(epochs):
            self.model.train()
            epoch_loss = 0.0

            for (batch_x,) in loader:
                batch_x = batch_x.to(self.device)
                reconstructed, _ = self.model(batch_x)
                loss = criterion(reconstructed, batch_x)

                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()

                epoch_loss += loss.item()

            avg_loss = epoch_loss / len(loader)
            scheduler.step(avg_loss)

            if avg_loss < best_loss:
                best_loss = avg_loss
                best_state = self.model.state_dict().copy()
                patience_counter = 0
            else:
                patience_counter += 1

            if (epoch + 1) % 10 == 0:
                logger.info(
                    "Epoch %d/%d: loss=%.6f best=%.6f lr=%.2e",
                    epoch + 1, epochs, avg_loss, best_loss,
                    optimizer.param_groups[0]["lr"],
                )

            if patience_counter >= max_patience:
                logger.info("Early stopping at epoch %d", epoch + 1)
                break

        # Load best weights
        if best_state:
            self.model.load_state_dict(best_state)

        # Set anomaly threshold based on training reconstruction errors
        self.model.eval()
        train_errors = self._compute_reconstruction_errors(X_scaled)
        self.anomaly_threshold = float(np.percentile(train_errors, self.threshold_percentile))

        train_time = time.time() - start_time

        metrics = {
            "training_time_seconds": round(train_time, 2),
            "best_loss": round(best_loss, 6),
            "epochs_trained": epoch + 1,
            "anomaly_threshold": round(self.anomaly_threshold, 6),
            "threshold_percentile": self.threshold_percentile,
            "train_error_mean": round(float(np.mean(train_errors)), 6),
            "train_error_std": round(float(np.std(train_errors)), 6),
            "train_error_p99": round(float(np.percentile(train_errors, 99)), 6),
        }

        # Evaluate on validation set if available
        if X_val is not None and y_val is not None:
            val_metrics = self._evaluate(X_val, y_val)
            metrics.update(val_metrics)

        self.training_metadata = {
            "model_type": "autoencoder",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "architecture": f"{input_dim}->128->64->32->{self.bottleneck_dim}->32->64->128->{input_dim}",
            "feature_count": input_dim,
            "feature_names": self.feature_names,
            "metrics": metrics,
        }

        logger.info(
            "Training complete in %.1fs. Threshold=%.6f (p%d)",
            train_time, self.anomaly_threshold, int(self.threshold_percentile),
        )

        return metrics

    def _compute_reconstruction_errors(self, X_scaled: np.ndarray) -> np.ndarray:
        """Compute per-sample MSE reconstruction error."""
        self.model.eval()  # ty:ignore[unresolved-attribute]
        with torch.no_grad():
            X_tensor = torch.FloatTensor(X_scaled).to(self.device)
            reconstructed, _ = self.model(X_tensor)  # ty:ignore[call-non-callable]
            errors = torch.mean((X_tensor - reconstructed) ** 2, dim=1)
            return errors.cpu().numpy()

    def _evaluate(self, X_val: np.ndarray, y_val: np.ndarray) -> dict:
        """Evaluate against labeled validation data."""
        X_scaled = self.scaler.transform(X_val)  # ty:ignore[unresolved-attribute]
        errors = self._compute_reconstruction_errors(X_scaled)

        # Normalize errors to [0, 1] for AUC calculation
        scores = self._normalize_scores(errors)

        metrics = {}
        try:
            metrics["val_auc_roc"] = round(roc_auc_score(y_val, scores), 4)
            metrics["val_aucpr"] = round(average_precision_score(y_val, scores), 4)
        except ValueError as e:
            logger.warning("Cannot compute AUC: %s", e)

        # Detection at threshold
        predicted = (errors > self.anomaly_threshold).astype(int)
        if np.sum(y_val == 1) > 0:
            recall = np.sum((predicted == 1) & (y_val == 1)) / np.sum(y_val == 1)
            metrics["val_recall_at_threshold"] = round(float(recall), 4)
        if np.sum(predicted == 1) > 0:
            precision = np.sum((predicted == 1) & (y_val == 1)) / np.sum(predicted == 1)
            metrics["val_precision_at_threshold"] = round(float(precision), 4)

        return metrics

    def _normalize_scores(self, errors: np.ndarray) -> np.ndarray:
        """Normalize reconstruction errors to [0, 1]."""
        min_e, max_e = errors.min(), errors.max()
        if max_e - min_e == 0:
            return np.zeros_like(errors)
        return (errors - min_e) / (max_e - min_e)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predict anomaly scores normalized to [0, 1].
        Higher score = more anomalous = more likely fraud.
        """
        if self.model is None or self.scaler is None:
            raise ValueError("Model not trained/loaded")
        X_scaled = self.scaler.transform(X)
        errors = self._compute_reconstruction_errors(X_scaled)
        return self._normalize_scores(errors)

    def get_embeddings(self, X: np.ndarray) -> np.ndarray:
        """
        Get latent space embeddings for player similarity analysis.

        Embeddings from the bottleneck layer capture compressed behavior patterns.
        Players with similar embeddings behave similarly - useful for:
          - Finding mule account networks (clustered embeddings)
          - Identifying multi-accounting (near-identical embeddings)
          - Player segmentation for risk tiering
        """
        X_scaled = self.scaler.transform(X)  # ty:ignore[unresolved-attribute]
        self.model.eval()  # ty:ignore[unresolved-attribute]
        with torch.no_grad():
            X_tensor = torch.FloatTensor(X_scaled).to(self.device)
            embeddings = self.model.encode(X_tensor)  # ty:ignore[unresolved-attribute]
            return embeddings.cpu().numpy()

    def save_model(self, version: str = "latest") -> str:
        """Save model to disk."""
        model_path = self.model_dir / f"autoencoder_fraud_{version}"
        model_path.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), model_path / "model_weights.pt")  # ty:ignore[unresolved-attribute]
        torch.save({
            "input_dim": self.model.input_dim,  # ty:ignore[unresolved-attribute]
            "bottleneck_dim": self.model.bottleneck_dim,  # ty:ignore[unresolved-attribute]
        }, model_path / "model_config.pt")

        with open(model_path / "scaler.pkl", "wb") as f:
            pickle.dump(self.scaler, f)
        with open(model_path / "metadata.json", "w") as f:
            json.dump({
                **self.training_metadata,
                "anomaly_threshold": self.anomaly_threshold,
            }, f, indent=2)

        logger.info("Model saved to %s", model_path)
        return str(model_path)

    def load_model(self, version: str = "latest") -> None:
        """Load model from disk."""
        model_path = self.model_dir / f"autoencoder_fraud_{version}"

        config = torch.load(model_path / "model_config.pt", weights_only=True)
        self.model = FraudAutoencoder(config["input_dim"], config["bottleneck_dim"]).to(self.device)
        self.model.load_state_dict(
            torch.load(model_path / "model_weights.pt", map_location=self.device, weights_only=True)
        )
        self.model.eval()

        with open(model_path / "scaler.pkl", "rb") as f:
            self.scaler = pickle.load(f)
        with open(model_path / "metadata.json", "r") as f:
            meta = json.load(f)
            self.training_metadata = meta
            self.anomaly_threshold = meta.get("anomaly_threshold", 0.0)
            self.feature_names = meta.get("feature_names", FEATURE_NAMES)


def main():
    """Train autoencoder on synthetic data."""
    np.random.seed(42)
    n_samples = 50_000
    n_features = len(FEATURE_NAMES)

    X = np.random.randn(n_samples, n_features)
    y = np.zeros(n_samples)
    fraud_idx = np.random.choice(n_samples, int(n_samples * 0.02), replace=False)
    y[fraud_idx] = 1
    X[fraud_idx] += np.random.uniform(2, 4, size=(len(fraud_idx), n_features))

    split = int(n_samples * 0.8)
    detector = AutoencoderFraudDetector()
    metrics = detector.train(X[:split], y[:split], X[split:], y[split:], epochs=50)

    scores = detector.predict(X[split:split+5])
    for i in range(5):
        logger.info("Sample %d: score=%.4f actual=%d", i, scores[i], int(y[split+i]))

    # Get embeddings for similarity
    embeddings = detector.get_embeddings(X[split:split+5])
    logger.info("Embedding shape: %s", embeddings.shape)

    detector.save_model("demo")


if __name__ == "__main__":
    main()
