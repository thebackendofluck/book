// Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React, { useEffect, useState } from 'react';
import { Grid, Card, CardContent, Typography, Box } from '@mui/material';
import { FraudMetricsChart } from './charts/FraudMetricsChart';
import { AlertTimeline } from './charts/AlertTimeline';
import { ModelPerformanceChart } from './charts/ModelPerformanceChart';
import { SystemHealthGauge } from './SystemHealthGauge';
import { useWebSocket } from '../hooks/useWebSocket';
import { useDashboardData } from '../hooks/useDashboardData';

export const Dashboard: React.FC = () => {
  const [realtimeData, setRealtimeData] = useState<any>({});
  const { dashboardData, isLoading, error } = useDashboardData();
  const { lastMessage } = useWebSocket('ws://localhost:8000/ws/dashboard');

  useEffect(() => {
    if (lastMessage) {
      try {
        const data = JSON.parse(lastMessage.data);
        setRealtimeData(data);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    }
  }, [lastMessage]);

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <Typography>Loading dashboard...</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
        <Typography color="error">Error loading dashboard: {error.message}</Typography>
      </Box>
    );
  }

  const data = { ...dashboardData, ...realtimeData };

  return (
    <div>
      <Typography variant="h4" gutterBottom>
        Fraud Detection Dashboard
      </Typography>

      <Grid container spacing={3}>
        {/* System Health Overview */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                System Health
              </Typography>
              <SystemHealthGauge
                cpu={data.system_health?.cpu_usage_percent || 0}
                memory={data.system_health?.memory_usage_percent || 0}
                disk={data.system_health?.disk_usage_percent || 0}
              />
            </CardContent>
          </Card>
        </Grid>

        {/* Active Alerts */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Active Alerts
              </Typography>
              <Typography variant="h4" color="error">
                {data.alert_metrics?.active_alerts || 0}
              </Typography>
              <Typography variant="body2">
                {data.alert_metrics?.critical_alerts || 0} Critical
              </Typography>
              <Typography variant="body2">
                {data.alert_metrics?.high_alerts || 0} High Priority
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Fraud Detection Rate */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Fraud Detection
              </Typography>
              <Typography variant="h4" color="primary">
                {data.fraud_metrics?.detection_rate_percent?.toFixed(1) || '0.0'}%
              </Typography>
              <Typography variant="body2">
                {data.fraud_metrics?.fraud_detections_1h || 0} detections (1h)
              </Typography>
              <Typography variant="body2">
                Precision: {data.fraud_metrics?.precision_percent?.toFixed(1) || '0.0'}%
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Model Performance */}
        <Grid item xs={12} md={6} lg={3}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Model Accuracy
              </Typography>
              <Typography variant="h4" color="success">
                {data.model_metrics?.accuracy_percent?.toFixed(1) || '0.0'}%
              </Typography>
              <Typography variant="body2">
                AUC: {data.model_metrics?.auc_score?.toFixed(3) || '0.000'}
              </Typography>
              <Typography variant="body2">
                Latency: {data.model_metrics?.avg_inference_time_ms?.toFixed(1) || '0.0'}ms
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        {/* Fraud Metrics Chart */}
        <Grid item xs={12} lg={8}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Fraud Detection Trends (24h)
              </Typography>
              <FraudMetricsChart data={data.fraud_trends || []} />
            </CardContent>
          </Card>
        </Grid>

        {/* Alert Timeline */}
        <Grid item xs={12} lg={4}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Recent Alerts
              </Typography>
              <AlertTimeline alerts={data.recent_alerts || []} />
            </CardContent>
          </Card>
        </Grid>

        {/* Model Performance Details */}
        <Grid item xs={12} lg={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Model Performance Details
              </Typography>
              <ModelPerformanceChart data={data.model_performance || {}} />
            </CardContent>
          </Card>
        </Grid>

        {/* Business KPIs */}
        <Grid item xs={12} lg={6}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Business KPIs (24h)
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <Typography variant="body2" color="textSecondary">
                    Revenue
                  </Typography>
                  <Typography variant="h6">
                    ${(data.business_kpis?.revenue_24h_usd || 0).toLocaleString()}
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2" color="textSecondary">
                    Transactions
                  </Typography>
                  <Typography variant="h6">
                    {(data.business_kpis?.transactions_24h || 0).toLocaleString()}
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2" color="textSecondary">
                    Fraud Prevented
                  </Typography>
                  <Typography variant="h6" color="success.main">
                    ${(data.business_kpis?.fraud_loss_prevented_24h_usd || 0).toLocaleString()}
                  </Typography>
                </Grid>
                <Grid item xs={6}>
                  <Typography variant="body2" color="textSecondary">
                    ROI
                  </Typography>
                  <Typography variant="h6" color="primary">
                    {data.business_kpis?.roi_percent?.toFixed(1) || '0.0'}%
                  </Typography>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </div>
  );
};