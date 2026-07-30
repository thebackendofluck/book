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
LSTM Sequence Model for Temporal Fraud Pattern Detection
=========================================================

Detects fraud patterns that unfold over sequences of events, not single events.

Why LSTM for iGaming fraud:
  - Captures temporal dependencies that tabular models miss
  - Detects multi-step fraud sequences (e.g., deposit -> minimal play -> withdraw)
  - Learns natural player session patterns and flags deviations
  - Models behavioral momentum (e.g., escalating bet sizes before cash-out)
  - Identifies coordinated multi-account sequences (synchronized actions)

Example fraud sequences this model detects:
  1. Bonus abuse: REGISTER -> CLAIM_BONUS -> BET_MIN_WAGERING -> WITHDRAW
  2. Money laundering: DEPOSIT_LARGE -> BET_LOW_MARGIN -> CASH_OUT_QUICK
  3. Account takeover: LONG_ABSENCE -> PASSWORD_CHANGE -> RAPID_BETS -> WITHDRAW
  4. Bot patterns: BET -> BET -> BET (identical timing, identical amounts)
  5. Chip dumping: PLAYER_A_RAISES -> PLAYER_B_FOLDS (repeatedly at same table)

Architecture:
  Embedding(event_type) + Features -> LSTM(2 layers, hidden=128)
  -> Attention -> FC(64) -> Sigmoid(fraud_probability)
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
from torch.utils.data import DataLoader, Dataset  # ty:ignore[unresolved-import]
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fraud.ml.lstm")


# Event types in the gaming platform
EVENT_TYPES = [
    "PAD",  # Padding token for shorter sequences
    "BET_PLACED", "BET_SETTLED", "GAME_SESSION_START", "GAME_SESSION_END",
    "BONUS_CLAIMED", "BONUS_WAGERED", "BONUS_FORFEITED",
    "DEPOSIT_COMPLETED", "DEPOSIT_FAILED", "WITHDRAWAL_REQUESTED",
    "WITHDRAWAL_COMPLETED", "WITHDRAWAL_CANCELLED",
    "LOGIN", "LOGOUT", "PASSWORD_CHANGE",
    "PAYMENT_METHOD_ADDED", "PAYMENT_METHOD_REMOVED",
    "CHARGEBACK_RECEIVED", "KYC_VERIFIED",
    "JACKPOT_WIN", "FREE_SPIN_AWARDED",
]
EVENT_TO_IDX = {e: i for i, e in enumerate(EVENT_TYPES)}

# Continuous features per event in the sequence
SEQUENCE_FEATURES = [
    "amount_eur", "time_delta_seconds", "hour_of_day",
    "bet_size_ratio", "cumulative_session_wagered",
    "ip_changed", "device_changed",
]


# =============================================================================
# Dataset
# =============================================================================

class FraudSequenceDataset(Dataset):
    """
    Dataset for player event sequences.

    Each sample is a fixed-length sequence of events for one player session,
    with the target being whether the session/player is fraudulent.

    Sequences are padded/truncated to max_seq_len.
    Most recent events are kept when truncating (fraud signals are often recent).
    """

    def __init__(
        self,
        event_sequences: list[list[dict]],
        labels: np.ndarray,
        max_seq_len: int = 100,
    ):
        self.max_seq_len = max_seq_len
        self.event_type_ids = []
        self.features = []
        self.lengths = []
        self.labels = labels

        for seq in event_sequences:
            # Truncate to last max_seq_len events (keep most recent)
            if len(seq) > max_seq_len:
                seq = seq[-max_seq_len:]

            seq_len = len(seq)
            self.lengths.append(seq_len)

            # Extract event type IDs
            type_ids = [EVENT_TO_IDX.get(e.get("event_type", "PAD"), 0) for e in seq]
            # Pad to max_seq_len
            type_ids += [0] * (max_seq_len - len(type_ids))
            self.event_type_ids.append(type_ids)

            # Extract continuous features
            feats = []
            for e in seq:
                feat_vec = [e.get(f, 0.0) for f in SEQUENCE_FEATURES]
                feats.append(feat_vec)
            # Pad with zeros
            while len(feats) < max_seq_len:
                feats.append([0.0] * len(SEQUENCE_FEATURES))
            self.features.append(feats)

        self.event_type_ids = torch.LongTensor(self.event_type_ids)
        self.features = torch.FloatTensor(np.array(self.features))
        self.lengths = torch.LongTensor(self.lengths)
        self.labels = torch.FloatTensor(labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return (
            self.event_type_ids[idx],
            self.features[idx],
            self.lengths[idx],
            self.labels[idx],
        )


# =============================================================================
# Model Architecture
# =============================================================================

class FraudLSTM(nn.Module):
    """
    LSTM with attention for fraud sequence classification.

    The attention mechanism allows the model to focus on the most
    fraud-relevant events in a long sequence, rather than relying
    solely on the final hidden state.

    This is critical because fraud signals can appear anywhere in
    a session: at the start (unusual login), middle (pattern change),
    or end (suspicious withdrawal).
    """

    def __init__(
        self,
        n_event_types: int = len(EVENT_TYPES),
        n_features: int = len(SEQUENCE_FEATURES),
        embedding_dim: int = 32,
        hidden_dim: int = 128,
        n_layers: int = 2,
        dropout: float = 0.3,
    ):
        super().__init__()

        # Event type embedding
        self.event_embedding = nn.Embedding(n_event_types, embedding_dim, padding_idx=0)

        # Input dimension = event embedding + continuous features
        input_dim = embedding_dim + n_features

        # Bidirectional LSTM: processes sequence forward AND backward
        # Backward pass captures "what happens after this event" context
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=n_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if n_layers > 1 else 0,
        )

        # Attention mechanism: learn which events in the sequence matter most
        self.attention = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, 1),
        )

        # Classification head
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * 2, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(
        self,
        event_type_ids: torch.Tensor,
        features: torch.Tensor,
        lengths: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forward pass.

        Args:
            event_type_ids: (batch, seq_len) event type indices
            features: (batch, seq_len, n_features) continuous features
            lengths: (batch,) actual sequence lengths

        Returns:
            (fraud_probabilities, attention_weights)
        """
        batch_size = event_type_ids.size(0)

        # Combine event embeddings with continuous features
        event_emb = self.event_embedding(event_type_ids)  # (batch, seq, emb_dim)
        combined = torch.cat([event_emb, features], dim=-1)  # (batch, seq, emb+feat)

        # Pack padded sequences for efficient LSTM processing
        packed = nn.utils.rnn.pack_padded_sequence(
            combined, lengths.cpu().clamp(min=1), batch_first=True, enforce_sorted=False
        )
        lstm_out, _ = self.lstm(packed)
        lstm_out, _ = nn.utils.rnn.pad_packed_sequence(lstm_out, batch_first=True)
        # lstm_out: (batch, seq_len, hidden*2)

        # Attention: compute importance weight for each timestep
        attn_scores = self.attention(lstm_out).squeeze(-1)  # (batch, seq_len)

        # Mask padding positions
        max_len = lstm_out.size(1)
        mask = torch.arange(max_len, device=lengths.device).unsqueeze(0) < lengths.unsqueeze(1)
        attn_scores = attn_scores.masked_fill(~mask, float("-inf"))

        attn_weights = torch.softmax(attn_scores, dim=1)  # (batch, seq_len)

        # Weighted sum of LSTM outputs
        context = torch.bmm(attn_weights.unsqueeze(1), lstm_out).squeeze(1)  # (batch, hidden*2)

        # Classify
        fraud_prob = self.classifier(context).squeeze(-1)  # (batch,)

        return fraud_prob, attn_weights


# =============================================================================
# Training Pipeline
# =============================================================================

class LSTMFraudTrainer:
    """LSTM training pipeline for temporal fraud detection."""

    def __init__(
        self,
        model_dir: str = "./models/lstm",
        max_seq_len: int = 100,
        device: str = "auto",
    ):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.max_seq_len = max_seq_len

        if device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)

        self.model: Optional[FraudLSTM] = None
        self.training_metadata: dict = {}

    def train(
        self,
        train_sequences: list[list[dict]],
        train_labels: np.ndarray,
        val_sequences: list[list[dict]],
        val_labels: np.ndarray,
        epochs: int = 50,
        batch_size: int = 256,
        learning_rate: float = 1e-3,
    ) -> dict:
        """
        Train LSTM on player event sequences.

        Args:
            train_sequences: List of event sequences (each sequence is a list of event dicts)
            train_labels: Binary labels per sequence (0=legitimate, 1=fraud)
            val_sequences: Validation sequences
            val_labels: Validation labels
            epochs: Training epochs
            batch_size: Batch size
            learning_rate: Initial learning rate

        Returns:
            Training metrics dict
        """
        start_time = time.time()

        # Create datasets
        train_dataset = FraudSequenceDataset(train_sequences, train_labels, self.max_seq_len)
        val_dataset = FraudSequenceDataset(val_sequences, val_labels, self.max_seq_len)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, drop_last=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

        # Create model
        self.model = FraudLSTM().to(self.device)

        # Class-weighted loss for imbalance
        n_fraud = np.sum(train_labels == 1)
        n_legit = np.sum(train_labels == 0)
        pos_weight = torch.tensor([n_legit / max(n_fraud, 1)]).to(self.device)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        optimizer = torch.optim.AdamW(self.model.parameters(), lr=learning_rate, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

        logger.info(
            "Training LSTM: %d train, %d val sequences (%.2f%% fraud)",
            len(train_sequences), len(val_sequences), np.mean(train_labels) * 100,
        )

        best_val_auc = 0.0
        best_state = None

        for epoch in range(epochs):
            # Train
            self.model.train()
            train_loss = 0.0
            for event_ids, features, lengths, labels in train_loader:
                event_ids = event_ids.to(self.device)
                features = features.to(self.device)
                lengths = lengths.to(self.device)
                labels = labels.to(self.device)

                probs, _ = self.model(event_ids, features, lengths)
                # Use BCE loss directly since model outputs sigmoid
                loss = nn.functional.binary_cross_entropy(probs, labels)

                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                train_loss += loss.item()

            scheduler.step()

            # Validate
            val_metrics = self._evaluate_loader(val_loader)

            if val_metrics.get("auc_roc", 0) > best_val_auc:
                best_val_auc = val_metrics["auc_roc"]
                best_state = self.model.state_dict().copy()

            if (epoch + 1) % 5 == 0:
                logger.info(
                    "Epoch %d/%d: train_loss=%.4f val_auc=%.4f val_aucpr=%.4f best=%.4f",
                    epoch + 1, epochs,
                    train_loss / len(train_loader),
                    val_metrics.get("auc_roc", 0),
                    val_metrics.get("aucpr", 0),
                    best_val_auc,
                )

        if best_state:
            self.model.load_state_dict(best_state)

        train_time = time.time() - start_time
        final_metrics = self._evaluate_loader(val_loader)
        final_metrics["training_time_seconds"] = round(train_time, 2)
        final_metrics["best_val_auc_roc"] = round(best_val_auc, 4)

        self.training_metadata = {
            "model_type": "lstm_sequence",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "max_seq_len": self.max_seq_len,
            "epochs_trained": epochs,
            "metrics": final_metrics,
        }

        logger.info("LSTM training complete in %.1fs (best AUC=%.4f)", train_time, best_val_auc)
        return final_metrics

    def _evaluate_loader(self, loader: DataLoader) -> dict:
        """Evaluate model on a DataLoader."""
        self.model.eval()  # ty:ignore[unresolved-attribute]
        all_probs = []
        all_labels = []

        with torch.no_grad():
            for event_ids, features, lengths, labels in loader:
                event_ids = event_ids.to(self.device)
                features = features.to(self.device)
                lengths = lengths.to(self.device)

                probs, _ = self.model(event_ids, features, lengths)  # ty:ignore[call-non-callable]
                all_probs.extend(probs.cpu().numpy())
                all_labels.extend(labels.numpy())

        all_probs = np.array(all_probs)
        all_labels = np.array(all_labels)

        metrics = {}
        try:
            metrics["auc_roc"] = round(roc_auc_score(all_labels, all_probs), 4)
            metrics["aucpr"] = round(average_precision_score(all_labels, all_probs), 4)
        except ValueError:
            pass

        return metrics

    def predict_with_attention(
        self,
        sequences: list[list[dict]],
    ) -> list[dict]:
        """
        Predict fraud probability with attention visualization.

        Returns per-event attention weights showing which events in the
        sequence contributed most to the fraud prediction. This is critical
        for analyst investigation: "why was this flagged?"

        Example output:
          {
            "fraud_probability": 0.87,
            "attention_events": [
              {"event": "DEPOSIT_COMPLETED", "attention": 0.35, "position": 0},
              {"event": "BET_PLACED", "attention": 0.05, "position": 1},
              {"event": "WITHDRAWAL_REQUESTED", "attention": 0.45, "position": 2},
            ]
          }
        """
        if self.model is None:
            raise ValueError("Model not trained/loaded")

        dataset = FraudSequenceDataset(sequences, np.zeros(len(sequences)), self.max_seq_len)
        loader = DataLoader(dataset, batch_size=len(sequences), shuffle=False)

        self.model.eval()
        results = []

        with torch.no_grad():
            for event_ids, features, lengths, _ in loader:
                event_ids = event_ids.to(self.device)
                features = features.to(self.device)
                lengths = lengths.to(self.device)

                probs, attn_weights = self.model(event_ids, features, lengths)

                for i in range(len(sequences)):
                    seq_len = min(len(sequences[i]), self.max_seq_len)
                    attn = attn_weights[i, :seq_len].cpu().numpy()

                    # Map back original events from the (possibly truncated) sequence
                    orig_seq = sequences[i][-self.max_seq_len:]

                    attention_events = []
                    for j in range(seq_len):
                        attention_events.append({
                            "event": orig_seq[j].get("event_type", "UNKNOWN"),
                            "attention": round(float(attn[j]), 4),
                            "position": j,
                        })

                    # Sort by attention (highest first)
                    attention_events.sort(key=lambda x: x["attention"], reverse=True)

                    results.append({
                        "fraud_probability": round(float(probs[i]), 4),
                        "top_attention_events": attention_events[:5],
                        "sequence_length": seq_len,
                    })

        return results

    def save_model(self, version: str = "latest") -> str:
        """Save model to disk."""
        model_path = self.model_dir / f"lstm_fraud_{version}"
        model_path.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), model_path / "model_weights.pt")  # ty:ignore[unresolved-attribute]
        with open(model_path / "metadata.json", "w") as f:
            json.dump(self.training_metadata, f, indent=2)

        logger.info("Model saved to %s", model_path)
        return str(model_path)

    def load_model(self, version: str = "latest") -> None:
        """Load model from disk."""
        model_path = self.model_dir / f"lstm_fraud_{version}"
        self.model = FraudLSTM().to(self.device)
        self.model.load_state_dict(
            torch.load(model_path / "model_weights.pt", map_location=self.device, weights_only=True)
        )
        self.model.eval()
        with open(model_path / "metadata.json", "r") as f:
            self.training_metadata = json.load(f)


def main():
    """Train LSTM on synthetic sequence data."""
    import random
    np.random.seed(42)
    random.seed(42)

    n_sequences = 5000
    sequences = []
    labels = []

    for i in range(n_sequences):
        is_fraud = random.random() < 0.05  # 5% fraud
        seq_len = random.randint(10, 80)
        seq = []

        for j in range(seq_len):
            if is_fraud and j > seq_len * 0.7:
                # Fraud pattern: suspicious events at end of session
                event_type = random.choice(["WITHDRAWAL_REQUESTED", "DEPOSIT_COMPLETED", "BET_PLACED"])
            else:
                event_type = random.choice(EVENT_TYPES[1:])  # Skip PAD

            seq.append({
                "event_type": event_type,
                "amount_eur": random.lognormvariate(2, 1) if is_fraud else random.lognormvariate(2, 0.5),
                "time_delta_seconds": random.uniform(0.1, 5) if is_fraud else random.uniform(1, 300),
                "hour_of_day": random.randint(0, 23),
                "bet_size_ratio": random.uniform(0.5, 5) if is_fraud else random.uniform(0.8, 1.5),
                "cumulative_session_wagered": (j + 1) * random.uniform(10, 50),
                "ip_changed": 1.0 if (is_fraud and random.random() < 0.3) else 0.0,
                "device_changed": 1.0 if (is_fraud and random.random() < 0.2) else 0.0,
            })

        sequences.append(seq)
        labels.append(1 if is_fraud else 0)

    labels = np.array(labels)
    split = int(n_sequences * 0.8)

    trainer = LSTMFraudTrainer()
    metrics = trainer.train(
        sequences[:split], labels[:split],
        sequences[split:], labels[split:],
        epochs=20, batch_size=128,
    )

    # Predict with attention
    results = trainer.predict_with_attention(sequences[split:split+3])
    for i, r in enumerate(results):
        logger.info("Sequence %d: prob=%.4f actual=%d", i, r["fraud_probability"], labels[split+i])
        for evt in r["top_attention_events"][:3]:
            logger.info("  %s (attention=%.4f, pos=%d)", evt["event"], evt["attention"], evt["position"])

    trainer.save_model("demo")


if __name__ == "__main__":
    main()
