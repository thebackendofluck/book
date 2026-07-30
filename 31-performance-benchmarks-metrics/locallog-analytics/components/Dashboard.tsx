// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// components/Dashboard.tsx -- Real-time metrics visualization
// Uses Recharts for traffic volume, latency trends, and status code distribution.
// All rendering is client-side; data never leaves the local environment.

import React from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell
} from 'recharts';
import { AnalyticsSummary, ApiLogEntry } from '../types';
import { StatCard } from './StatCard';

interface DashboardProps {
  summary: AnalyticsSummary;
  logs: ApiLogEntry[];
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-slate-800 border border-slate-700 p-3 rounded shadow-xl text-xs">
        <p className="text-slate-200 font-bold mb-1">{label}</p>
        {payload.map((entry: any, index: number) => (
          <p key={index} style={{ color: entry.color }}>
            {entry.name}: {entry.value}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

export const Dashboard: React.FC<DashboardProps> = ({ summary, logs }) => {
  // Status code distribution for bar chart
  const statusData = Object.entries(summary.statusBreakdown)
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => parseInt(a.status) - parseInt(b.status));

  const getStatusColor = (status: string) => {
    if (status.startsWith('2')) return '#4ade80'; // green
    if (status.startsWith('3')) return '#60a5fa'; // blue
    if (status.startsWith('4')) return '#fbbf24'; // yellow
    if (status.startsWith('5')) return '#f87171'; // red
    return '#94a3b8';
  };

  return (
    <div className="space-y-6">
      {/* KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total Requests" value={summary.totalRequests.toLocaleString()}
          subtitle="In selected period" color="blue" />
        <StatCard title="Avg Latency" value={`${summary.avgLatency}ms`}
          subtitle={`P95: ${summary.p95Latency}ms`} color="purple" />
        <StatCard title="Error Rate" value={`${summary.errorRate.toFixed(2)}%`}
          subtitle={summary.errorRate > 1 ? "Attention Needed" : "Healthy"}
          color={summary.errorRate > 5 ? 'red' : summary.errorRate > 1 ? 'yellow' : 'green'} />
        <StatCard title="Top Endpoint" value={summary.topPaths[0]?.path || "N/A"}
          subtitle={`${summary.topPaths[0]?.count || 0} reqs`} color="blue" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Traffic Volume & Latency -- dual-axis area chart */}
        <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-xl p-6">
          <h3 className="text-slate-200 font-semibold mb-6">Traffic Volume & Latency</h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={summary.requestsOverTime}>
                <defs>
                  <linearGradient id="colorCount" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorLatency" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ec4899" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#ec4899" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis dataKey="time" stroke="#94a3b8" fontSize={12} />
                <YAxis yAxisId="left" stroke="#94a3b8" fontSize={12} />
                <YAxis yAxisId="right" orientation="right" stroke="#94a3b8" fontSize={12} unit="ms"/>
                <Tooltip content={<CustomTooltip />} />
                <Area yAxisId="left" type="monotone" dataKey="count" name="Requests"
                  stroke="#6366f1" strokeWidth={2} fill="url(#colorCount)" />
                <Area yAxisId="right" type="monotone" dataKey="avgLatency" name="Latency"
                  stroke="#ec4899" strokeWidth={2} fill="url(#colorLatency)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Status Code Distribution */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-6">
          <h3 className="text-slate-200 font-semibold mb-6">Response Codes</h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={statusData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis type="number" stroke="#94a3b8" fontSize={12} hide />
                <YAxis dataKey="status" type="category" stroke="#94a3b8" fontSize={12} width={30} />
                <Tooltip content={<CustomTooltip />} cursor={{fill: '#334155'}} />
                <Bar dataKey="count" name="Count" radius={[0, 4, 4, 0]} barSize={20}>
                  {statusData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={getStatusColor(entry.status)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Recent Logs Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <div className="p-6 border-b border-slate-700 flex justify-between items-center">
          <h3 className="text-slate-200 font-semibold">Recent Logs</h3>
          <span className="text-xs text-slate-500 font-mono">Last 10 entries</span>
        </div>
        <table className="w-full text-left text-sm text-slate-400">
          <thead className="bg-slate-900/50 text-slate-200 uppercase text-xs font-semibold">
            <tr>
              <th className="px-6 py-3">Time</th>
              <th className="px-6 py-3">Method</th>
              <th className="px-6 py-3">Path</th>
              <th className="px-6 py-3">Status</th>
              <th className="px-6 py-3 text-right">Latency</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {logs.slice(-10).reverse().map((log) => (
              <tr key={log.id} className="hover:bg-slate-700/30">
                <td className="px-6 py-3 font-mono text-xs">
                  {new Date(log.timestamp).toLocaleTimeString()}
                </td>
                <td className="px-6 py-3">
                  <span className={`font-bold text-xs px-2 py-1 rounded ${
                    log.method === 'GET' ? 'bg-blue-500/10 text-blue-400' :
                    log.method === 'POST' ? 'bg-green-500/10 text-green-400' :
                    log.method === 'DELETE' ? 'bg-red-500/10 text-red-400' :
                    'bg-slate-500/10 text-slate-400'
                  }`}>{log.method}</span>
                </td>
                <td className="px-6 py-3 font-mono text-slate-300">{log.path}</td>
                <td className="px-6 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                    log.status >= 500 ? 'text-red-400 bg-red-400/10' :
                    log.status >= 400 ? 'text-yellow-400 bg-yellow-400/10' :
                    'text-green-400 bg-green-400/10'
                  }`}>{log.status}</span>
                </td>
                <td className="px-6 py-3 text-right font-mono">{log.latency}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
