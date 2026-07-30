// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// types.ts -- Core type definitions for the analytics dashboard
// Privacy-first API log analytics with AI-powered insights

export interface ApiLogEntry {
  id: string;
  timestamp: string; // ISO date string
  method: string;
  path: string;
  status: number;
  latency: number; // in ms
  ip?: string;
  userAgent?: string;
  responseSize?: number; // bytes
  serviceName: string; // Multi-service support
}

export interface AnalyticsSummary {
  totalRequests: number;
  avgLatency: number;
  errorRate: number;
  p95Latency: number;
  statusBreakdown: Record<string, number>;
  requestsOverTime: { time: string; count: number; avgLatency: number }[];
  topPaths: { path: string; count: number }[];
}

export enum AnalysisStatus {
  IDLE = 'IDLE',
  ANALYZING = 'ANALYZING',
  COMPLETED = 'COMPLETED',
  ERROR = 'ERROR'
}

export interface AiInsight {
  title: string;
  severity: 'low' | 'medium' | 'high' | 'info';
  description: string;
  recommendation: string;
}
