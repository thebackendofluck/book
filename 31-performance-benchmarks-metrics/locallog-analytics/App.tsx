// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// App.tsx -- Main application shell for the analytics dashboard
// Manages service selection, log polling, and AI analysis orchestration.

import React, { useState, useEffect, useCallback } from 'react';
import { ApiLogEntry, AnalyticsSummary, AnalysisStatus, AiInsight } from './types';
import { generateSummary, fetchServices, fetchLogs } from './services/logParser';
import { analyzeWithAi } from './services/geminiService';
import { Dashboard } from './components/Dashboard';

const App: React.FC = () => {
  const [services, setServices] = useState<string[]>([]);
  const [selectedService, setSelectedService] = useState<string | null>(null);
  const [logs, setLogs] = useState<ApiLogEntry[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);

  // AI analysis state
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>(AnalysisStatus.IDLE);
  const [insights, setInsights] = useState<AiInsight[]>([]);

  // Poll for services every 5 seconds -- picks up new services automatically
  const refreshServices = useCallback(async () => {
    try {
      const list = await fetchServices();
      setServices(list);
      if (list.length > 0 && !selectedService) {
        setSelectedService(list[0]);
      }
    } catch (e) {
      console.error("Failed to fetch services", e);
    }
  }, [selectedService]);

  useEffect(() => {
    refreshServices();
    const interval = setInterval(refreshServices, 5000);
    return () => clearInterval(interval);
  }, [refreshServices]);

  // Reload logs when service changes
  useEffect(() => {
    if (selectedService) {
      fetchLogs(selectedService).then(data => {
        setLogs(data);
        setSummary(generateSummary(data));
        setInsights([]);
        setAnalysisStatus(AnalysisStatus.IDLE);
      });
    }
  }, [selectedService]);

  // Trigger AI-powered analysis of current logs
  const runAiAnalysis = useCallback(async () => {
    if (!summary || logs.length === 0) return;

    setAnalysisStatus(AnalysisStatus.ANALYZING);
    try {
      const errorLogs = logs.filter(l => l.status >= 400).slice(0, 20);
      const results = await analyzeWithAi(summary, errorLogs);
      setInsights(results);
      setAnalysisStatus(AnalysisStatus.COMPLETED);
    } catch (error) {
      setAnalysisStatus(AnalysisStatus.ERROR);
    }
  }, [summary, logs]);

  return (
    <div className="min-h-screen bg-slate-900 text-slate-200 flex">
      {/* Sidebar -- service selector */}
      <aside className="w-64 bg-slate-800 border-r border-slate-700 p-4">
        <h1 className="text-lg font-bold text-white mb-4">API Analytics</h1>
        <nav className="space-y-1">
          {services.map(svc => (
            <button
              key={svc}
              onClick={() => setSelectedService(svc)}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm ${
                selectedService === svc
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-400 hover:bg-slate-700'
              }`}
            >
              {svc}
            </button>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 p-8 overflow-y-auto h-screen">
        <div className="flex justify-between items-center mb-8">
          <h2 className="text-2xl font-bold text-white">
            {selectedService || 'Dashboard'}
          </h2>
          <div className="flex gap-3">
            <button onClick={refreshServices}
              className="px-3 py-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-sm">
              Refresh
            </button>
            <button onClick={runAiAnalysis}
              disabled={analysisStatus === AnalysisStatus.ANALYZING}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm disabled:opacity-50">
              {analysisStatus === AnalysisStatus.ANALYZING ? 'Analyzing...' : 'AI Analysis'}
            </button>
          </div>
        </div>

        {summary && <Dashboard summary={summary} logs={logs} />}

        {/* AI Insights Panel */}
        {insights.length > 0 && (
          <div className="mt-8 space-y-4">
            <h3 className="text-lg font-semibold text-white">AI Insights</h3>
            {insights.map((insight, i) => (
              <div key={i} className={`p-4 rounded-xl border ${
                insight.severity === 'high' ? 'border-red-500/30 bg-red-500/5' :
                insight.severity === 'medium' ? 'border-yellow-500/30 bg-yellow-500/5' :
                'border-green-500/30 bg-green-500/5'
              }`}>
                <h4 className="font-bold text-white">{insight.title}</h4>
                <p className="text-sm text-slate-400 mt-1">{insight.description}</p>
                <p className="text-sm text-slate-300 mt-2">
                  <span className="font-medium">Recommendation:</span> {insight.recommendation}
                </p>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
};

export default App;
