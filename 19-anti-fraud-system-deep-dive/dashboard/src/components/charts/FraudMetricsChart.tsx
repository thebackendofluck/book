// Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer
} from 'recharts';

interface FraudMetricsChartProps {
  data: Array<{
    timestamp: string;
    fraud_detections: number;
    total_transactions: number;
    false_positives: number;
  }>;
}

export const FraudMetricsChart: React.FC<FraudMetricsChartProps> = ({ data }) => {
  const chartData = data.map(item => ({
    ...item,
    time: new Date(item.timestamp).toLocaleTimeString(),
    fraudRate: item.total_transactions > 0
      ? (item.fraud_detections / item.total_transactions) * 100
      : 0,
    falsePositiveRate: item.total_transactions > 0
      ? (item.false_positives / item.total_transactions) * 100
      : 0
  }));

  const formatTooltip = (value: any, name: string) => {
    if (name === 'fraudRate' || name === 'falsePositiveRate') {
      return [`${value.toFixed(2)}%`, name === 'fraudRate' ? 'Fraud Rate' : 'False Positive Rate'];
    }
    return [value, name];
  };

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 12 }}
        />
        <YAxis
          yAxisId="left"
          tick={{ fontSize: 12 }}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          formatter={formatTooltip}
          labelFormatter={(label) => `Time: ${label}`}
        />
        <Legend />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="fraud_detections"
          stroke="#8884d8"
          name="Fraud Detections"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="total_transactions"
          stroke="#82ca9d"
          name="Total Transactions"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="fraudRate"
          stroke="#ff7300"
          name="Fraud Rate (%)"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="falsePositiveRate"
          stroke="#ff0000"
          name="False Positive Rate (%)"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
};