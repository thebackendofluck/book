// Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { useQuery } from '@tanstack/react-query';
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface DashboardData {
  fraud_metrics: {
    total_transactions_1h: number;
    fraud_detections_1h: number;
    false_positives_1h: number;
    detection_rate_percent: number;
    precision_percent: number;
    recall_percent: number;
    blocked_amount_usd: number;
  };
  system_health: {
    cpu_usage_percent: number;
    memory_usage_percent: number;
    disk_usage_percent: number;
    network_in_mbps: number;
    network_out_mbps: number;
    active_connections: number;
    error_rate_percent: number;
    avg_response_time_ms: number;
  };
  model_metrics: {
    accuracy_percent: number;
    precision_percent: number;
    recall_percent: number;
    auc_score: number;
    f1_score: number;
    avg_inference_time_ms: number;
    throughput_predictions_per_sec: number;
    model_drift_score: number;
  };
  alert_metrics: {
    active_alerts: number;
    critical_alerts: number;
    high_alerts: number;
    medium_alerts: number;
    low_alerts: number;
    alerts_last_1h: number;
    avg_resolution_time_min: number;
    alert_accuracy_percent: number;
  };
  business_kpis: {
    revenue_24h_usd: number;
    transactions_24h: number;
    fraud_loss_prevented_24h_usd: number;
    customer_satisfaction_score: number;
    system_uptime_percent: number;
    mean_time_to_detect_min: number;
    mean_time_to_resolve_min: number;
    roi_percent: number;
  };
  fraud_trends: Array<{
    timestamp: string;
    fraud_detections: number;
    total_transactions: number;
    false_positives: number;
  }>;
  recent_alerts: Array<{
    id: string;
    title: string;
    severity: string;
    status: string;
    timestamp: string;
  }>;
  model_performance: {
    accuracy_trend: number[];
    latency_trend: number[];
    drift_score_trend: number[];
  };
}

const fetchDashboardData = async (): Promise<DashboardData> => {
  const response = await axios.get(`${API_BASE_URL}/api/v1/dashboard/metrics/summary`);
  return response.data;
};

export const useDashboardData = () => {
  return useQuery<DashboardData, Error>(
    'dashboard-data',
    fetchDashboardData,
    {
      refetchInterval: 30000, // Refetch every 30 seconds
      retry: 3,
      retryDelay: attemptIndex => Math.min(1000 * 2 ** attemptIndex, 30000),
      staleTime: 10000, // Consider data fresh for 10 seconds
      cacheTime: 300000, // Cache for 5 minutes
      onError: (error) => {
        console.error('Failed to fetch dashboard data:', error);
      }
    }
  );
};

const fetchFraudTrends = async (hours: number = 24) => {
  const response = await axios.get(`${API_BASE_URL}/api/v1/dashboard/metrics/fraud-trends`, {
    params: { hours }
  });
  return response.data.trends;
};

export const useFraudTrends = (hours: number = 24) => {
  return useQuery(
    ['fraud-trends', hours],
    () => fetchFraudTrends(hours),
    {
      refetchInterval: 60000, // Refetch every minute
      retry: 2,
      staleTime: 30000,
    }
  );
};

const fetchRecentAlerts = async (limit: number = 50) => {
  const response = await axios.get(`${API_BASE_URL}/api/v1/dashboard/alerts/recent`, {
    params: { limit }
  });
  return response.data.alerts;
};

export const useRecentAlerts = (limit: number = 50) => {
  return useQuery(
    ['recent-alerts', limit],
    () => fetchRecentAlerts(limit),
    {
      refetchInterval: 15000, // Refetch every 15 seconds
      retry: 2,
      staleTime: 10000,
    }
  );
};

const fetchModelPerformance = async () => {
  const response = await axios.get(`${API_BASE_URL}/api/v1/dashboard/models/performance`);
  return response.data.models;
};

export const useModelPerformance = () => {
  return useQuery(
    'model-performance',
    fetchModelPerformance,
    {
      refetchInterval: 120000, // Refetch every 2 minutes
      retry: 2,
      staleTime: 60000,
    }
  );
};