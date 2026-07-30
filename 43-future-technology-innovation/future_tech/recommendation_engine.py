# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 35: Future Technology
Advanced AI Recommendation Engine for Casino Personalization

This module implements a next-generation recommendation system using PyTorch,
BERT-based game encoding, LSTM-based player behavior encoding, and an
attention-based fusion model for hyper-personalized game recommendations.

Usage:
    engine = CasinoRecommendationEngine(model_path='path/to/model', redis_client=redis)
    recommendations = await engine.get_personalized_recommendations(
        player_id='player_123',
        context={'current_balance': 500, 'session_duration': 1800},
        candidate_games=['game_001', 'game_002', 'game_003']
    )
"""

import torch  # ty:ignore[unresolved-import]
import torch.nn as nn  # ty:ignore[unresolved-import]
from transformers import BertModel, BertTokenizer  # ty:ignore[unresolved-import]
import numpy as np
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import redis.asyncio as redis


class CasinoRecommendationEngine:
    def __init__(self, model_path: str, redis_client: redis.Redis):
        self.redis = redis_client
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        # Load pre-trained models
        self.game_encoder = self.load_game_encoder()
        self.player_encoder = self.load_player_encoder()
        self.recommendation_model = self.load_recommendation_model(model_path)

        # Real-time context tracking
        self.session_context = {}
        self.emotional_state = {}

    def load_game_encoder(self) -> nn.Module:
        """Load game feature encoder using BERT"""
        tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        model = BertModel.from_pretrained('bert-base-uncased')

        class GameEncoder(nn.Module):
            def __init__(self, bert_model, tokenizer):
                super().__init__()
                self.bert = bert_model
                self.tokenizer = tokenizer
                self.feature_projection = nn.Linear(768, 256)

            def forward(self, game_description: str) -> torch.Tensor:
                inputs = self.tokenizer(
                    game_description,
                    return_tensors='pt',
                    truncation=True,
                    padding=True,
                    max_length=512
                ).to(self.device)

                with torch.no_grad():
                    outputs = self.bert(**inputs)
                    embeddings = outputs.last_hidden_state[:, 0, :]  # CLS token

                return self.feature_projection(embeddings)

        return GameEncoder(model, tokenizer).to(self.device)

    def load_player_encoder(self) -> nn.Module:
        """Load player behavior encoder"""
        class PlayerEncoder(nn.Module):
            def __init__(self, input_dim: int = 100, hidden_dim: int = 128):
                super().__init__()
                self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
                self.attention = nn.MultiheadAttention(hidden_dim, num_heads=8)
                self.output_projection = nn.Linear(hidden_dim, 256)

            def forward(self, behavior_sequence: torch.Tensor) -> torch.Tensor:
                # Process sequence of player actions
                lstm_out, _ = self.lstm(behavior_sequence)

                # Apply attention mechanism
                attn_out, _ = self.attention(lstm_out, lstm_out, lstm_out)

                # Global average pooling
                pooled = torch.mean(attn_out, dim=1)

                return self.output_projection(pooled)

        return PlayerEncoder().to(self.device)

    def load_recommendation_model(self, model_path: str) -> nn.Module:
        """Load the main recommendation model"""
        class RecommendationModel(nn.Module):
            def __init__(self, player_dim: int = 256, game_dim: int = 256, hidden_dim: int = 512):
                super().__init__()
                self.player_projection = nn.Linear(player_dim, hidden_dim)
                self.game_projection = nn.Linear(game_dim, hidden_dim)
                self.context_projection = nn.Linear(50, hidden_dim)  # Context features

                self.attention_fusion = nn.MultiheadAttention(hidden_dim, num_heads=8)

                self.recommendation_head = nn.Sequential(
                    nn.Linear(hidden_dim * 4, hidden_dim),
                    nn.ReLU(),
                    nn.Dropout(0.2),
                    nn.Linear(hidden_dim, 1),
                    nn.Sigmoid()
                )

                self.explainability_head = nn.Linear(hidden_dim, 10)  # Top 10 factors

            def forward(self, player_embedding: torch.Tensor,
                       game_embedding: torch.Tensor,
                       context_features: torch.Tensor) -> Dict[str, torch.Tensor]:

                # Project to common space
                player_proj = self.player_projection(player_embedding)
                game_proj = self.game_projection(game_embedding)
                context_proj = self.context_projection(context_features)

                # Fuse features with attention
                combined = torch.stack([player_proj, game_proj, context_proj], dim=0)
                fused, attention_weights = self.attention_fusion(combined, combined, combined)

                # Generate recommendation score
                recommendation_input = torch.cat([
                    player_proj, game_proj, context_proj, fused.mean(dim=0)
                ], dim=-1)

                score = self.recommendation_head(recommendation_input)

                # Generate explainability factors
                factors = self.explainability_head(fused.mean(dim=0))

                return {
                    'recommendation_score': score,
                    'explainability_factors': factors,
                    'attention_weights': attention_weights
                }

        model = RecommendationModel()
        if model_path:
            model.load_state_dict(torch.load(model_path))
        return model.to(self.device)

    async def get_personalized_recommendations(
        self, player_id: str, context: Dict[str, Any], candidate_games: List[str]
    ) -> List[Dict[str, Any]]:
        """Get personalized game recommendations for a player"""

        # Get player behavior history
        player_history = await self.get_player_history(player_id)

        # Encode player behavior
        player_embedding = self.encode_player_behavior(player_history)

        # Get current context features
        context_features = self.extract_context_features(context)

        recommendations = []

        for game_id in candidate_games:
            # Get game features
            game_description = await self.get_game_description(game_id)
            game_embedding = self.game_encoder(game_description)

            # Generate recommendation
            with torch.no_grad():
                result = self.recommendation_model(
                    player_embedding.unsqueeze(0),
                    game_embedding.unsqueeze(0),
                    torch.tensor(context_features, dtype=torch.float32).unsqueeze(0)
                )

            score = result['recommendation_score'].item()
            factors = result['explainability_factors'].tolist()

            recommendations.append({
                'game_id': game_id,
                'recommendation_score': score,
                'explainability_factors': factors,
                'reasoning': self.interpret_factors(factors)
            })

        # Sort by recommendation score
        recommendations.sort(key=lambda x: x['recommendation_score'], reverse=True)

        return recommendations[:10]  # Top 10 recommendations

    def encode_player_behavior(self, history: List[Dict]) -> torch.Tensor:
        """Encode player behavior sequence"""
        # Convert history to feature vectors
        behavior_features = []

        for action in history[-50:]:  # Last 50 actions
            features = [
                action.get('bet_amount', 0) / 1000,  # Normalized bet
                action.get('win_loss_ratio', 0),
                action.get('session_duration', 0) / 3600,  # Hours
                action.get('game_type_encoded', 0),  # One-hot encoded
                action.get('time_of_day', 0) / 24,  # Normalized hour
                action.get('day_of_week', 0) / 7,  # Normalized day
                action.get('emotional_state', 0),  # Derived from behavior
            ]
            behavior_features.append(features)

        # Pad sequence if needed
        while len(behavior_features) < 50:
            behavior_features.insert(0, [0] * 7)

        return torch.tensor(behavior_features, dtype=torch.float32)

    def extract_context_features(self, context: Dict) -> List[float]:
        """Extract context features for recommendation"""
        return [
            context.get('current_balance', 0) / 10000,  # Normalized
            context.get('session_duration', 0) / 3600,   # Hours
            context.get('time_since_last_win', 0) / 3600, # Hours
            context.get('current_streak', 0),            # Win/loss streak
            context.get('preferred_game_category', 0),   # Encoded
            context.get('device_type', 0),               # Mobile/desktop
            context.get('location_risk_score', 0),       # Geo risk
            context.get('emotional_engagement', 0),      # Derived
            context.get('social_influence', 0),          # Friend activity
            context.get('market_conditions', 0),         # External factors
        ]

    def interpret_factors(self, factors: List[float]) -> str:
        """Interpret explainability factors into human-readable reasoning"""
        factor_names = [
            'betting_pattern_similarity',
            'win_rate_compatibility',
            'session_time_match',
            'game_type_preference',
            'time_of_day_alignment',
            'day_of_week_pattern',
            'emotional_state_match',
            'social_proof_factor',
            'market_trend_alignment',
            'exploration_opportunity'
        ]

        # Find top contributing factors
        top_factors = sorted(
            enumerate(factors),
            key=lambda x: x[1],
            reverse=True
        )[:3]

        reasons = []
        for idx, score in top_factors:
            if score > 0.5:  # Significant contribution
                reasons.append(factor_names[idx])

        if reasons:
            return f"Recommended because of: {', '.join(reasons)}"
        else:
            return "Recommended based on your playing style"

    async def get_player_history(self, player_id: str) -> List[Dict]:  # ty:ignore[empty-body]
        """Get player behavior history from Redis/cache"""
        # Implementation would fetch from data platform
        pass

    async def get_game_description(self, game_id: str) -> str:  # ty:ignore[empty-body]
        """Get game description for encoding"""
        # Implementation would fetch from game catalog
        pass
