// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React, { useEffect, useState } from 'react';
import { Header } from './components/Header';
import { StatusCard } from './components/StatusCard';
import { TerraformAnalyzer } from './components/TerraformAnalyzer';
import { CloudStatus, CloudProvider, NewsItem } from './types';
import { fetchCloudStatusWithAI, fetchCloudNews } from './services/geminiService';

const App: React.FC = () => {
  const [statuses, setStatuses] = useState<CloudStatus[]>([
    { provider: CloudProvider.AWS, status: 'Unknown', lastChecked: '-', message: 'Pending check...' },
    { provider: CloudProvider.GCP, status: 'Unknown', lastChecked: '-', message: 'Pending check...' },
    { provider: CloudProvider.AZURE, status: 'Unknown', lastChecked: '-', message: 'Pending check...' },
  ]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);

  useEffect(() => {
    const initData = async () => {
      setLoadingStatus(true);
      try {
        // Parallel data fetching
        const [statusData, newsData] = await Promise.all([
          fetchCloudStatusWithAI(),
          fetchCloudNews()
        ]);
        
        if (statusData && statusData.length > 0) {
            // Merge fetched data with default order
            const order = [CloudProvider.AWS, CloudProvider.GCP, CloudProvider.AZURE];
            const sortedStatus = order.map(p => 
                statusData.find(s => s.provider === p) || { 
                    provider: p, 
                    status: 'Unknown', 
                    lastChecked: new Date().toLocaleTimeString(), 
                    message: 'Could not fetch' 
                }
            ) as CloudStatus[];
            setStatuses(sortedStatus);
        }
        
        setNews(newsData);
      } catch (error) {
        console.error("Initialization error", error);
      } finally {
        setLoadingStatus(false);
      }
    };

    initData();
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 pb-10">
      <Header />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pt-8 space-y-8">
        
        {/* Status Section */}
        <section>
          <div className="flex items-center justify-between mb-6">
            <div>
                <h2 className="text-2xl font-bold text-white">Provider Status</h2>
                <p className="text-slate-400 text-sm">Real-time AI-summarized health checks</p>
            </div>
            <button 
                onClick={() => window.location.reload()}
                className="text-sm text-primary-400 hover:text-primary-300 flex items-center gap-1"
            >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                Refresh
            </button>
          </div>
          <div className="grid md:grid-cols-3 gap-6">
            {statuses.map((status) => (
              <StatusCard key={status.provider} data={status} loading={loadingStatus} />
            ))}
          </div>
        </section>

        {/* Terraform Analyzer Section */}
        <section>
          <TerraformAnalyzer />
        </section>

        {/* News Feed */}
        <section className="bg-slate-900/50 border border-slate-800 rounded-xl p-6">
            <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                <svg className="w-5 h-5 text-primary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" /></svg>
                Recent API & Provider Changes
            </h3>
            <div className="space-y-4">
                {news.length === 0 && !loadingStatus && (
                    <p className="text-slate-500 text-sm">No critical recent changes found or API limit reached.</p>
                )}
                {loadingStatus && news.length === 0 && (
                     <div className="space-y-3">
                        {[1, 2, 3].map((i) => (
                             <div key={i} className="h-16 bg-slate-800/50 rounded animate-pulse"></div>
                        ))}
                     </div>
                )}
                {news.map((item, idx) => (
                    <div key={idx} className="flex gap-4 p-4 rounded-lg hover:bg-slate-800/50 transition-colors border-b border-slate-800/50 last:border-0">
                        <div className="shrink-0">
                             <span className={`text-xs font-bold px-2 py-1 rounded ${
                                 item.provider === 'AWS' ? 'bg-orange-500/20 text-orange-400' :
                                 item.provider === 'GCP' ? 'bg-blue-500/20 text-blue-400' :
                                 'bg-sky-500/20 text-sky-400'
                             }`}>
                                 {item.provider}
                             </span>
                        </div>
                        <div>
                            <h4 className="text-slate-200 font-medium text-sm">{item.title}</h4>
                            <p className="text-slate-400 text-xs mt-1">{item.description}</p>
                            <span className="text-slate-600 text-xs mt-2 block">{item.date}</span>
                        </div>
                    </div>
                ))}
            </div>
        </section>

      </main>
    </div>
  );
};

export default App;