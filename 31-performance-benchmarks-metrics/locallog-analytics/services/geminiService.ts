// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// services/geminiService.ts -- AI analysis proxy client
// Sends summarized log data to the local server's /api/analyze endpoint,
// which forwards to Gemini. The AI API key stays server-side.

import { AnalyticsSummary, AiInsight } from "../types";

export const analyzeWithAi = async (
  summary: AnalyticsSummary,
  sampleErrors: any[]
): Promise<AiInsight[]> => {
  const response = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ summary, errorLogs: sampleErrors })
  });

  if (!response.ok) {
    throw new Error('Analysis failed');
  }

  return response.json();
};
