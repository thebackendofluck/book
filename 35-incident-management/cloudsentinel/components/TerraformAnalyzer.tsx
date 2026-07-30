// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React, { useState } from 'react';
import { TerraformAnalysis } from '../types';
import { analyzeTerraformCode } from '../services/geminiService';

export const TerraformAnalyzer: React.FC = () => {
  const [code, setCode] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<TerraformAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!code.trim()) return;
    setAnalyzing(true);
    setError(null);
    setResult(null);
    try {
      const analysis = await analyzeTerraformCode(code);
      setResult(analysis);
    } catch (err) {
      setError("Failed to analyze code. Ensure API Key is set or try again.");
      console.error(err);
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-1 overflow-hidden shadow-xl">
      <div className="bg-slate-800/50 px-6 py-4 border-b border-slate-800 flex justify-between items-center">
        <div>
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <svg className="w-5 h-5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
            Terraform Guard
          </h2>
          <p className="text-xs text-slate-400">Paste your .tf content to check for breaking provider changes</p>
        </div>
      </div>

      <div className="p-6 grid lg:grid-cols-2 gap-6">
        {/* Input Section */}
        <div className="flex flex-col h-full">
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder='resource "aws_instance" "web" { ... }'
            className="w-full h-96 bg-slate-950 border border-slate-700 rounded-lg p-4 font-mono text-sm text-slate-300 focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none resize-none mb-4"
          />
          <button
            onClick={handleAnalyze}
            disabled={analyzing || !code}
            className={`w-full py-3 px-4 rounded-lg font-medium flex items-center justify-center gap-2 transition-all ${
              analyzing || !code
                ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
                : 'bg-primary-600 hover:bg-primary-500 text-white shadow-lg shadow-primary-600/20'
            }`}
          >
            {analyzing ? (
              <>
                <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Analyzing with Gemini...
              </>
            ) : (
              'Run Analysis'
            )}
          </button>
        </div>

        {/* Output Section */}
        <div className="bg-slate-950 rounded-lg border border-slate-800 p-6 h-96 overflow-y-auto relative">
          {!result && !error && !analyzing && (
            <div className="h-full flex flex-col items-center justify-center text-slate-600">
              <svg className="w-12 h-12 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" /></svg>
              <p>Results will appear here</p>
            </div>
          )}

          {error && (
            <div className="text-red-400 bg-red-500/10 p-4 rounded-lg border border-red-500/20">
              <p className="font-semibold">Error Analysis Failed</p>
              <p className="text-sm opacity-80">{error}</p>
            </div>
          )}

          {result && (
            <div className="space-y-4 animate-in fade-in duration-500">
              <div className="flex items-center justify-between">
                <span className="text-sm font-mono text-slate-400">Risk Assessment</span>
                <span className={`px-3 py-1 rounded-full text-xs font-bold ${
                  result.riskLevel === 'High' ? 'bg-red-500 text-white' :
                  result.riskLevel === 'Medium' ? 'bg-orange-500 text-white' :
                  'bg-green-500 text-white'
                }`}>
                  {result.riskLevel.toUpperCase()}
                </span>
              </div>
              
              <h3 className="text-lg font-bold text-white">{result.summary}</h3>
              
              <div>
                <h4 className="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2">Affected Resources</h4>
                <div className="flex flex-wrap gap-2">
                  {result.affectedResources.map((res, idx) => (
                    <span key={idx} className="bg-slate-800 text-slate-300 text-xs px-2 py-1 rounded border border-slate-700 font-mono">
                      {res}
                    </span>
                  ))}
                  {result.affectedResources.length === 0 && <span className="text-slate-500 text-xs italic">None detected</span>}
                </div>
              </div>

              <div>
                 <h4 className="text-xs uppercase tracking-wider text-slate-500 font-bold mb-2">Details</h4>
                 <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-line">
                   {result.details}
                 </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};