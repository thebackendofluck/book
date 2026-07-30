// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// server.js -- Privacy-first API log collector with AI analysis
// All data stays local -- no external telemetry, no cloud dependencies.
// AI analysis is opt-in and requires an API key at runtime.

import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import { GoogleGenerativeAI } from '@google/generative-ai';
import { rateLimit } from 'express-rate-limit';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 8080;

const limiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 100,
  standardHeaders: true,
  legacyHeaders: false,
});

app.use(cors());
app.use(express.json());
app.use(limiter);

// In-memory storage for logs
// Structure: Map<ServiceName, LogEntry[]>
const db = new Map();

// --- API Endpoints ---

// 1. Ingest Log Entry
// Any service can POST here to record API traffic
app.post('/api/ingest', (req, res) => {
  const { serviceName, method, path, status, latency } = req.body;

  if (!serviceName) {
    return res.status(400).json({ error: 'serviceName is required' });
  }

  const entry = {
    id: `log-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    timestamp: new Date().toISOString(),
    method: method || 'GET',
    path: path || '/',
    status: Number(status || 200),
    latency: Number(latency || 0),
    serviceName
  };

  if (!db.has(serviceName)) {
    db.set(serviceName, []);
  }

  // Keep last 1000 logs per service to manage memory
  const logs = db.get(serviceName);
  logs.push(entry);
  if (logs.length > 1000) logs.shift();

  res.status(201).json({ success: true, id: entry.id });
});

// 2. List Services
app.get('/api/services', (req, res) => {
  const services = Array.from(db.keys());
  res.json(services);
});

// 3. Get Logs for Service
app.get('/api/logs/:serviceName', (req, res) => {
  const { serviceName } = req.params;
  const logs = db.get(serviceName) || [];
  res.json(logs);
});

// --- AI Analysis Endpoint ---
// Proxies log data to Gemini for SRE-style analysis.
// The API key never leaves the server -- frontend calls this endpoint,
// not the AI provider directly.
app.post('/api/analyze', async (req, res) => {
  const { summary, errorLogs } = req.body;

  const apiKey = process.env.API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'API Key is missing. Set API_KEY env var.' });
  }

  const genAI = new GoogleGenerativeAI(apiKey);

  const prompt = `
    You are a Senior Site Reliability Engineer (SRE). Analyze the following API traffic summary and error logs.

    TRAFFIC SUMMARY:
    - Total Requests: ${summary.totalRequests}
    - Avg Latency: ${summary.avgLatency}ms
    - P95 Latency: ${summary.p95Latency}ms
    - Error Rate: ${summary.errorRate.toFixed(2)}%
    - Top Status Codes: ${JSON.stringify(summary.statusBreakdown)}
    - Top Paths: ${JSON.stringify(summary.topPaths.slice(0, 5))}

    SAMPLE ERRORS (Last 5):
    ${JSON.stringify(errorLogs.slice(0, 5), null, 2)}

    Provide 3 to 5 key insights. For each insight, provide a title, a severity level (low, medium, high), a brief description of the finding, and a specific technical recommendation.
    Focus on performance bottlenecks, potential security issues (like 401/403 spikes), and reliability.

    Respond with a JSON array of objects, each with keys: title, severity, description, recommendation.
  `;

  try {
    const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });
    const result = await model.generateContent(prompt);
    const response = await result.response;
    const text = response.text();

    try {
      const insights = JSON.parse(text);
      res.json(insights);
    } catch (parseError) {
      console.error("Failed to parse JSON response", text);
      res.status(500).json({ error: 'Invalid response format from AI provider' });
    }
  } catch (error) {
    console.error("AI analysis failed", error);
    res.status(500).json({ error: 'Analysis failed' });
  }
});

// --- Frontend Serving ---
app.use(express.static(path.join(__dirname, 'dist')));

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`Log Collector running on port ${PORT}`);
  console.log(`- Ingest: POST http://localhost:${PORT}/api/ingest`);
  console.log(`- Dashboard: http://localhost:${PORT}`);
});
