// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

export enum CloudProvider {
  AWS = 'AWS',
  GCP = 'GCP',
  AZURE = 'AZURE'
}

export interface CloudStatus {
  provider: CloudProvider;
  status: 'Operational' | 'Degraded' | 'Outage' | 'Unknown';
  lastChecked: string;
  message: string;
  sourceUrl?: string;
}

export interface TerraformAnalysis {
  riskLevel: 'Low' | 'Medium' | 'High';
  summary: string;
  details: string;
  affectedResources: string[];
}

export interface NewsItem {
  title: string;
  date: string;
  provider: CloudProvider;
  description: string;
  url?: string;
}

/**
 * Jira ticket information returned after creation
 */
export interface JiraTicket {
  key: string;
  url: string;
  summary: string;
  status: string;
}

/**
 * Incident status for tracking
 */
export interface IncidentRecord {
  id: string;
  provider: CloudProvider;
  status: CloudStatus['status'];
  detectedAt: string;
  jiraTicket?: JiraTicket;
  acknowledged: boolean;
  resolvedAt?: string;
}