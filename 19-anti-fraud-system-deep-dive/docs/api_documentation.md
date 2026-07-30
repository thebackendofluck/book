# API Documentation

## Overview

This document provides comprehensive API documentation for the Fraud Detection System. All APIs follow REST principles and use JSON for request/response payloads.

## Authentication

All API endpoints require authentication using JWT tokens or API keys.

```bash
# Header format
Authorization: Bearer <jwt_token>
# or
X-API-Key: <api_key>
```

## Common Response Format

```json
{
  "success": true,
  "data": {},
  "message": "Operation completed successfully",
  "timestamp": "2024-01-15T10:30:00Z",
  "request_id": "req_123456"
}
```

## Error Response Format

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input parameters",
    "details": {
      "field": "amount",
      "issue": "must be positive number"
    }
  },
  "timestamp": "2024-01-15T10:30:00Z",
  "request_id": "req_123456"
}
```

## Data Ingestion API

### POST /api/v1/ingest/transaction

Ingest a transaction for fraud analysis.

**Request Body:**
```json
{
  "transaction_id": "txn_123456",
  "player_id": "player_789",
  "amount": 100.50,
  "currency": "USD",
  "timestamp": "2024-01-15T10:30:00Z",
  "payment_method": "credit_card",
  "game_type": "slots",
  "location": {
    "ip_address": "192.168.1.1",
    "country": "US",
    "city": "New York"
  },
  "device_fingerprint": "abc123def456"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "transaction_id": "txn_123456",
    "status": "ingested",
    "processing_time_ms": 45
  }
}
```

### POST /api/v1/ingest/batch

Ingest multiple transactions in a batch.

**Request Body:**
```json
{
  "transactions": [
    {
      "transaction_id": "txn_123456",
      "player_id": "player_789",
      "amount": 100.50,
      "currency": "USD",
      "timestamp": "2024-01-15T10:30:00Z"
    }
  ],
  "batch_id": "batch_001"
}
```

## Feature Engineering API

### GET /api/v1/features/player/{player_id}

Retrieve engineered features for a player.

**Response:**
```json
{
  "success": true,
  "data": {
    "player_id": "player_789",
    "features": {
      "total_bet_amount": 5000.0,
      "total_win_amount": 4500.0,
      "transaction_count": 50,
      "avg_bet_amount": 100.0,
      "session_duration_avg": 1800,
      "games_played_unique": 5,
      "last_transaction_days": 2
    },
    "feature_timestamp": "2024-01-15T10:30:00Z"
  }
}
```

### POST /api/v1/features/refresh

Trigger feature refresh for a player or group of players.

**Request Body:**
```json
{
  "player_ids": ["player_789", "player_790"],
  "feature_types": ["behavior", "transaction", "network"]
}
```

## Model Serving API

### POST /api/v1/predict/fraud

Get fraud prediction for a transaction or player.

**Request Body:**
```json
{
  "player_id": "player_789",
  "features": {
    "total_bet_amount": 5000.0,
    "transaction_count": 50,
    "avg_bet_amount": 100.0
  },
  "include_explanation": true
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "prediction": "low_risk",
    "probability": 0.15,
    "confidence": 0.92,
    "model_version": "v1.2.0",
    "processing_time_ms": 23,
    "explanation": {
      "top_features": [
        {"feature": "avg_bet_amount", "importance": 0.35},
        {"feature": "transaction_count", "importance": 0.28}
      ],
      "decision_factors": [
        "Consistent betting pattern",
        "Normal transaction velocity"
      ]
    }
  }
}
```

### POST /api/v1/predict/batch

Get batch fraud predictions.

**Request Body:**
```json
{
  "predictions": [
    {
      "player_id": "player_789",
      "features": {"total_bet_amount": 5000.0}
    }
  ],
  "batch_id": "pred_batch_001"
}
```

### GET /api/v1/models/info

Get information about deployed models.

**Response:**
```json
{
  "success": true,
  "data": {
    "models": [
      {
        "name": "fraud_detection_v1",
        "version": "1.2.0",
        "type": "ensemble",
        "accuracy": 0.94,
        "last_deployed": "2024-01-10T08:00:00Z",
        "status": "active"
      }
    ]
  }
}
```

## Alerting API

### GET /api/v1/alerts

Get alerts with filtering options.

**Query Parameters:**
- `status`: open, investigating, resolved, closed
- `severity`: critical, high, medium, low
- `limit`: maximum number of alerts (default: 50)
- `offset`: pagination offset (default: 0)

**Response:**
```json
{
  "success": true,
  "data": {
    "alerts": [
      {
        "alert_id": "alert_123",
        "title": "High Fraud Score Detected",
        "description": "Transaction fraud score exceeds threshold",
        "severity": "high",
        "status": "open",
        "player_id": "player_789",
        "created_at": "2024-01-15T10:30:00Z",
        "assigned_to": null
      }
    ],
    "total": 1,
    "pagination": {
      "limit": 50,
      "offset": 0,
      "has_more": false
    }
  }
}
```

### POST /api/v1/alerts/{alert_id}/assign

Assign an alert to a user.

**Request Body:**
```json
{
  "user_id": "analyst_123",
  "notes": "Taking ownership of this investigation"
}
```

### POST /api/v1/alerts/{alert_id}/resolve

Resolve an alert.

**Request Body:**
```json
{
  "resolution": "confirmed_fraud",
  "notes": "Player blocked after verification",
  "actions_taken": [
    "Blocked player account",
    "Notified compliance team"
  ]
}
```

### GET /api/v1/alerts/rules

Get alert rules configuration.

**Response:**
```json
{
  "success": true,
  "data": {
    "rules": [
      {
        "rule_id": "high_fraud_score",
        "name": "High Fraud Score",
        "condition": "fraud_score > 0.8",
        "severity": "high",
        "enabled": true,
        "description": "Alert when fraud score exceeds 0.8"
      }
    ]
  }
}
```

## Compliance API

### POST /api/v1/compliance/check

Run compliance checks on data or transactions.

**Request Body:**
```json
{
  "check_type": "gdpr_data_retention",
  "target_data": {
    "player_id": "player_789",
    "data_types": ["personal_info", "transaction_history"]
  }
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "check_id": "check_123",
    "status": "pass",
    "details": {
      "data_retention_compliant": true,
      "oldest_data_age_days": 45,
      "retention_policy_days": 2555
    }
  }
}
```

### POST /api/v1/gdpr/requests

Submit a GDPR data subject request.

**Request Body:**
```json
{
  "request_type": "access",
  "subject_id": "player_789",
  "requester_info": {
    "name": "John Doe",
    "email": "john.doe@email.com",
    "relationship": "data_subject"
  },
  "data_scope": ["personal_info", "transaction_history"]
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "request_id": "gdpr_123",
    "status": "submitted",
    "estimated_completion_days": 30,
    "next_steps": [
      "Identity verification",
      "Data collection",
      "Review and redaction"
    ]
  }
}
```

### GET /api/v1/compliance/reports

Get compliance reports.

**Query Parameters:**
- `report_type`: audit, gdpr_compliance, data_inventory
- `period`: last_30_days, last_90_days, last_year

## Cost Optimization API

### POST /api/v1/cost/analysis

Run cost analysis.

**Request Body:**
```json
{
  "period_days": 30,
  "include_recommendations": true
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "analysis_id": "cost_123",
    "period_days": 30,
    "total_cost": 15000.0,
    "cost_breakdown": {
      "compute": 8000.0,
      "storage": 3000.0,
      "database": 2500.0
    },
    "projected_savings": 2250.0,
    "recommendations": [
      {
        "priority": "high",
        "title": "Rightsize Compute Instances",
        "potential_savings": 1200.0,
        "implementation_effort": "medium"
      }
    ]
  }
}
```

### GET /api/v1/cost/optimization/recommendations

Get cost optimization recommendations.

**Query Parameters:**
- `limit`: maximum recommendations (default: 10)
- `min_savings_percent`: minimum savings threshold

## Monitoring API

### GET /api/v1/metrics/system

Get system metrics.

**Response:**
```json
{
  "success": true,
  "data": {
    "cpu_usage_percent": 65.5,
    "memory_usage_percent": 72.3,
    "disk_usage_percent": 45.2,
    "network_in_mbps": 150.5,
    "network_out_mbps": 98.3,
    "active_connections": 1250,
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

### GET /api/v1/metrics/business

Get business metrics.

**Response:**
```json
{
  "success": true,
  "data": {
    "transactions_processed": 15420,
    "fraud_alerts_generated": 23,
    "false_positive_rate": 0.032,
    "average_response_time_ms": 45.2,
    "system_uptime_percent": 99.95,
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

### GET /api/v1/health

System health check.

**Response:**
```json
{
  "success": true,
  "data": {
    "status": "healthy",
    "services": {
      "data_ingestion": "healthy",
      "feature_engineering": "healthy",
      "model_serving": "healthy",
      "alerting": "healthy",
      "database": "healthy"
    },
    "version": "1.2.0",
    "uptime_seconds": 345600
  }
}
```

## Dashboard API

### GET /api/dashboard/summary

Get dashboard summary data.

**Response:**
```json
{
  "success": true,
  "data": {
    "system_health": {
      "overall_status": "healthy",
      "active_alerts": 3,
      "response_time_p95": 125
    },
    "fraud_metrics": {
      "total_transactions_today": 15420,
      "fraud_detected_today": 23,
      "false_positive_rate": 0.032,
      "blocked_amount": 45670.50
    },
    "performance": {
      "cpu_usage": 65.5,
      "memory_usage": 72.3,
      "throughput_rps": 180
    },
    "cost_metrics": {
      "daily_cost": 450.50,
      "projected_monthly": 13515.00,
      "optimization_potential": 2027.25
    }
  }
}
```

### GET /api/dashboard/charts/{chart_type}

Get chart data for dashboards.

**Path Parameters:**
- `chart_type`: fraud_trends, performance_metrics, cost_analysis, alert_distribution

**Query Parameters:**
- `period`: 1h, 24h, 7d, 30d

## Rate Limiting

All APIs implement rate limiting:

- **Data Ingestion APIs**: 1000 requests per minute per client
- **Prediction APIs**: 500 requests per minute per client
- **Query APIs**: 100 requests per minute per client
- **Admin APIs**: 50 requests per minute per client

Rate limit headers are included in responses:
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 950
X-RateLimit-Reset: 1642156800
```

## Error Codes

| Code | Description | HTTP Status |
|------|-------------|-------------|
| VALIDATION_ERROR | Invalid request parameters | 400 |
| UNAUTHORIZED | Authentication required | 401 |
| FORBIDDEN | Insufficient permissions | 403 |
| NOT_FOUND | Resource not found | 404 |
| RATE_LIMITED | Rate limit exceeded | 429 |
| INTERNAL_ERROR | Internal server error | 500 |
| SERVICE_UNAVAILABLE | Service temporarily unavailable | 503 |

## Versioning

API versioning is handled through URL paths:
- Current version: `/api/v1/`
- Future versions will use `/api/v2/`, etc.

## SDKs and Libraries

### Python SDK

```python
from fraud_detection_sdk import FraudDetectionClient

client = FraudDetectionClient(api_key="your_api_key")

# Ingest transaction
result = client.ingest_transaction({
    "transaction_id": "txn_123",
    "player_id": "player_789",
    "amount": 100.50
})

# Get fraud prediction
prediction = client.predict_fraud({
    "player_id": "player_789",
    "features": {"total_bet_amount": 5000.0}
})
```

### JavaScript SDK

```javascript
import { FraudDetectionClient } from 'fraud-detection-sdk';

const client = new FraudDetectionClient({ apiKey: 'your_api_key' });

// Ingest transaction
const result = await client.ingestTransaction({
  transactionId: 'txn_123',
  playerId: 'player_789',
  amount: 100.50
});

// Get fraud prediction
const prediction = await client.predictFraud({
  playerId: 'player_789',
  features: { totalBetAmount: 5000.0 }
});
```

## Webhooks

The system supports webhooks for real-time notifications:

### Alert Webhooks

Configure webhook endpoints to receive alert notifications:

```json
{
  "webhook_url": "https://your-app.com/webhooks/alerts",
  "events": ["alert.created", "alert.resolved"],
  "secret": "webhook_secret_for_verification"
}
```

### Compliance Webhooks

Receive compliance-related notifications:

```json
{
  "webhook_url": "https://your-app.com/webhooks/compliance",
  "events": ["gdpr.request.submitted", "audit.completed"],
  "secret": "webhook_secret_for_verification"
}
```

## Support

For API support and questions:
- **Documentation**: https://docs.fraud-detection.com
- **API Status**: https://status.fraud-detection.com
- **Developer Forum**: https://community.fraud-detection.com
- **Email Support**: api-support@fraud-detection.com

## Changelog

### Version 1.2.0 (Latest)
- Added batch prediction endpoints
- Enhanced error responses with more details
- Added webhook support for real-time notifications
- Improved rate limiting with burst allowances

### Version 1.1.0
- Added compliance API endpoints
- Enhanced monitoring metrics
- Added cost optimization recommendations
- Improved authentication with API keys

### Version 1.0.0
- Initial API release
- Core fraud detection functionality
- Basic monitoring and alerting
- RESTful design with JSON payloads