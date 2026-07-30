// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// components/StatCard.tsx -- Reusable KPI card for dashboard metrics
// Color-coded by severity: green (healthy), yellow (warning), red (critical).

import React from 'react';

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  color?: 'blue' | 'red' | 'green' | 'yellow' | 'purple';
}

export const StatCard: React.FC<StatCardProps> = ({ title, value, subtitle, color = 'blue' }) => {
  const colorClasses = {
    blue: 'border-blue-500/20 text-blue-400',
    red: 'border-red-500/20 text-red-400',
    green: 'border-green-500/20 text-green-400',
    yellow: 'border-yellow-500/20 text-yellow-400',
    purple: 'border-purple-500/20 text-purple-400',
  };

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 relative overflow-hidden">
      <h3 className="text-slate-400 text-sm font-medium uppercase tracking-wider">{title}</h3>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-3xl font-bold text-slate-100">{value}</span>
        {subtitle && <span className="text-sm text-slate-500">{subtitle}</span>}
      </div>
    </div>
  );
};
