// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React from 'react';
import { CloudStatus, CloudProvider } from '../types';

interface StatusCardProps {
  data: CloudStatus;
  loading: boolean;
}

const getProviderColor = (provider: CloudProvider) => {
  switch (provider) {
    case CloudProvider.AWS: return 'text-orange-400';
    case CloudProvider.GCP: return 'text-blue-400';
    case CloudProvider.AZURE: return 'text-sky-400';
    default: return 'text-slate-400';
  }
};

const getStatusColor = (status: string) => {
  switch (status) {
    case 'Operational': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
    case 'Degraded': return 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20';
    case 'Outage': return 'bg-red-500/10 text-red-400 border-red-500/20';
    default: return 'bg-slate-500/10 text-slate-400 border-slate-500/20';
  }
};

export const StatusCard: React.FC<StatusCardProps> = ({ data, loading }) => {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 hover:border-slate-700 transition-all duration-300 shadow-lg">
      <div className="flex justify-between items-start mb-4">
        <h3 className={`text-lg font-bold ${getProviderColor(data.provider)} flex items-center gap-2`}>
           {data.provider === 'AWS' && <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M13.792 7.766l-2.73 5.54-2.73-5.54h-2.45l3.95 7.61-.56 1.09h-2.32l1.78-3.48H6.48l-2.26 4.4h5.5l5.75-11.05h-2.47l.792 1.43zM17.6 16.09l3.64-7.07h-2.5l-2.35 4.58-2.36-4.58h-2.49l3.63 7.07h2.43z"/></svg>}
           {data.provider === 'GCP' && <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M12 24c6.627 0 12-5.373 12-12S18.627 0 12 0 0 5.373 0 12s5.373 12 12 12z" fill="#fff"/><path d="M12.004 22.005c-5.524 0-10.004-4.48-10.004-10.004 0-5.525 4.48-10.005 10.004-10.005 5.45 0 9.885 4.368 9.998 9.79H12v.43h9.998C21.885 17.637 17.45 22.005 12.004 22.005z" fill="#4285F4"/></svg>}
           {data.provider === 'AZURE' && <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M5.86 20.56L11.54 4.57l6.6 15.99h-3.95l-2.2-5.74h-4.8l1.52 4.4H3.26l2.6-1.34zM8.5 12.2h3.2l-1.6-4.2-1.6 4.2z"/></svg>}
           {data.provider}
        </h3>
        {loading ? (
          <div className="h-6 w-20 bg-slate-800 rounded animate-pulse"></div>
        ) : (
          <span className={`px-3 py-1 rounded-full text-xs font-medium border ${getStatusColor(data.status)}`}>
            {data.status}
          </span>
        )}
      </div>
      
      <div className="space-y-3">
        <p className="text-slate-400 text-sm h-10 line-clamp-2">
          {loading ? "Analyzing global infrastructure..." : data.message}
        </p>
        
        <div className="pt-4 border-t border-slate-800 flex justify-between items-center text-xs text-slate-500">
          <span>Last check: {data.lastChecked}</span>
          <span className="flex items-center gap-1">
             Gemini AI
             <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
          </span>
        </div>
      </div>
    </div>
  );
};