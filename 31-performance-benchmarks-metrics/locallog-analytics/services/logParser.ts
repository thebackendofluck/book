// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// services/logParser.ts -- Client-side log fetching and summary generation
// Computes P95 latency, error rates, status breakdowns, and time-series
// aggregations entirely in the browser -- no data leaves the local network.

import { ApiLogEntry, AnalyticsSummary } from '../types';

const API_BASE = '/api';

export const fetchServices = async (): Promise<string[]> => {
  const res = await fetch(`${API_BASE}/services`);
  return res.json();
};

export const fetchLogs = async (serviceName: string): Promise<ApiLogEntry[]> => {
  const res = await fetch(`${API_BASE}/logs/${serviceName}`);
  return res.json();
};

export const generateSummary = (logs: ApiLogEntry[]): AnalyticsSummary => {
  if (logs.length === 0) {
    return {
      totalRequests: 0, avgLatency: 0, errorRate: 0, p95Latency: 0,
      statusBreakdown: {}, requestsOverTime: [], topPaths: []
    };
  }

  // Latency calculations
  const totalLatency = logs.reduce((sum, log) => sum + log.latency, 0);
  const latencies = logs.map(l => l.latency).sort((a, b) => a - b);
  const p95Index = Math.floor(latencies.length * 0.95);

  // Status breakdown and error counting
  const statusBreakdown: Record<string, number> = {};
  let errorCount = 0;

  // Path frequency
  const pathCounts: Record<string, number> = {};

  // Time-series bucketing (hourly)
  const timeMap = new Map<string, { count: number; totalLatency: number }>();

  logs.forEach(log => {
    const statusKey = log.status.toString();
    statusBreakdown[statusKey] = (statusBreakdown[statusKey] || 0) + 1;
    if (log.status >= 400) errorCount++;

    pathCounts[log.path] = (pathCounts[log.path] || 0) + 1;

    const date = new Date(log.timestamp);
    const timeKey = `${date.getHours().toString().padStart(2, '0')}:00`;

    const existing = timeMap.get(timeKey) || { count: 0, totalLatency: 0 };
    timeMap.set(timeKey, {
      count: existing.count + 1,
      totalLatency: existing.totalLatency + log.latency
    });
  });

  const requestsOverTime = Array.from(timeMap.entries())
    .map(([time, data]) => ({
      time,
      count: data.count,
      avgLatency: Math.round(data.totalLatency / data.count)
    }))
    .sort((a, b) => parseInt(a.time) - parseInt(b.time));

  const topPaths = Object.entries(pathCounts)
    .map(([path, count]) => ({ path, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  return {
    totalRequests: logs.length,
    avgLatency: Math.round(totalLatency / logs.length),
    errorRate: (errorCount / logs.length) * 100,
    p95Latency: latencies[p95Index] || 0,
    statusBreakdown,
    requestsOverTime,
    topPaths
  };
};
