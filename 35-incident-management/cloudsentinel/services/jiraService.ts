// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Jira Integration Service for CloudSentinel
 *
 * This service enables automatic incident ticket creation in Jira
 * when CloudSentinel detects issues with cloud providers or
 * identifies high-risk Terraform configurations.
 *
 * Configuration:
 * - VITE_JIRA_BASE_URL: Your Jira instance URL (e.g., https://company.atlassian.net)
 * - VITE_JIRA_API_TOKEN: API token for authentication
 * - VITE_JIRA_USER_EMAIL: Email associated with the API token
 * - VITE_JIRA_PROJECT_KEY: Default project key for tickets (e.g., "INC" or "OPS")
 */

import { CloudStatus, TerraformAnalysis, CloudProvider } from '../types';

// Jira configuration from environment variables
const JIRA_BASE_URL = import.meta.env.VITE_JIRA_BASE_URL || '';
const JIRA_API_TOKEN = import.meta.env.VITE_JIRA_API_TOKEN || '';
const JIRA_USER_EMAIL = import.meta.env.VITE_JIRA_USER_EMAIL || '';
const JIRA_PROJECT_KEY = import.meta.env.VITE_JIRA_PROJECT_KEY || 'INC';

/**
 * Check if Jira integration is configured
 */
export const isJiraConfigured = (): boolean => {
  return !!(JIRA_BASE_URL && JIRA_API_TOKEN && JIRA_USER_EMAIL);
};

/**
 * Jira issue types for different incident scenarios
 */
export enum JiraIssueType {
  INCIDENT = 'Incident',
  BUG = 'Bug',
  TASK = 'Task',
  STORY = 'Story',
}

/**
 * Jira priority levels mapped to CloudSentinel severity
 */
export enum JiraPriority {
  HIGHEST = '1',   // P1 - Critical outage
  HIGH = '2',      // P2 - Major degradation
  MEDIUM = '3',    // P3 - Minor issues
  LOW = '4',       // P4 - Informational
  LOWEST = '5',    // P5 - Cosmetic
}

/**
 * Interface for Jira ticket creation request
 */
export interface JiraTicketRequest {
  summary: string;
  description: string;
  issueType: JiraIssueType;
  priority: JiraPriority;
  labels?: string[];
  components?: string[];
  customFields?: Record<string, unknown>;
}

/**
 * Interface for Jira ticket response
 */
export interface JiraTicketResponse {
  success: boolean;
  ticketKey?: string;
  ticketUrl?: string;
  error?: string;
}

/**
 * Create authentication header for Jira API
 */
const getAuthHeader = (): string => {
  const credentials = btoa(`${JIRA_USER_EMAIL}:${JIRA_API_TOKEN}`);
  return `Basic ${credentials}`;
};

/**
 * Create a Jira ticket
 */
export const createJiraTicket = async (
  request: JiraTicketRequest
): Promise<JiraTicketResponse> => {
  if (!isJiraConfigured()) {
    return {
      success: false,
      error: 'Jira integration not configured. Set VITE_JIRA_* environment variables.',
    };
  }

  try {
    const payload = {
      fields: {
        project: { key: JIRA_PROJECT_KEY },
        summary: request.summary,
        description: {
          type: 'doc',
          version: 1,
          content: [
            {
              type: 'paragraph',
              content: [{ type: 'text', text: request.description }],
            },
          ],
        },
        issuetype: { name: request.issueType },
        priority: { id: request.priority },
        labels: request.labels || [],
        ...(request.customFields || {}),
      },
    };

    const response = await fetch(`${JIRA_BASE_URL}/rest/api/3/issue`, {
      method: 'POST',
      headers: {
        'Authorization': getAuthHeader(),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errorData = await response.json();
      return {
        success: false,
        error: `Jira API error: ${JSON.stringify(errorData.errors || errorData)}`,
      };
    }

    const data = await response.json();
    return {
      success: true,
      ticketKey: data.key,
      ticketUrl: `${JIRA_BASE_URL}/browse/${data.key}`,
    };
  } catch (error) {
    return {
      success: false,
      error: `Failed to create Jira ticket: ${error instanceof Error ? error.message : 'Unknown error'}`,
    };
  }
};

/**
 * Create incident ticket from cloud provider status
 */
export const createCloudIncidentTicket = async (
  status: CloudStatus
): Promise<JiraTicketResponse> => {
  // Map status to priority
  const priorityMap: Record<string, JiraPriority> = {
    'Outage': JiraPriority.HIGHEST,
    'Degraded': JiraPriority.HIGH,
    'Unknown': JiraPriority.MEDIUM,
    'Operational': JiraPriority.LOW,
  };

  const request: JiraTicketRequest = {
    summary: `[CloudSentinel] ${status.provider} ${status.status} - ${new Date().toISOString().split('T')[0]}`,
    description: `
CloudSentinel has detected an issue with ${status.provider}.

**Status:** ${status.status}
**Last Checked:** ${status.lastChecked}
**Details:** ${status.message}

**Recommended Actions:**
${status.status === 'Outage' ? '- Activate incident response procedure\n- Notify stakeholders\n- Check official status page' : ''}
${status.status === 'Degraded' ? '- Monitor closely\n- Prepare rollback plans\n- Review dependent services' : ''}

**Source:** CloudSentinel Monitoring Dashboard
**Provider Status Page:** ${getProviderStatusUrl(status.provider)}
    `.trim(),
    issueType: JiraIssueType.INCIDENT,
    priority: priorityMap[status.status] || JiraPriority.MEDIUM,
    labels: [
      'cloudsentinel',
      'cloud-incident',
      status.provider.toLowerCase(),
      status.status.toLowerCase(),
    ],
  };

  return createJiraTicket(request);
};

/**
 * Create ticket from Terraform analysis result
 */
export const createTerraformAnalysisTicket = async (
  analysis: TerraformAnalysis,
  terraformCode?: string
): Promise<JiraTicketResponse> => {
  // Map risk level to priority
  const priorityMap: Record<string, JiraPriority> = {
    'High': JiraPriority.HIGH,
    'Medium': JiraPriority.MEDIUM,
    'Low': JiraPriority.LOW,
  };

  const request: JiraTicketRequest = {
    summary: `[CloudSentinel] Terraform Risk: ${analysis.riskLevel} - ${analysis.summary.substring(0, 50)}...`,
    description: `
CloudSentinel Terraform Guard has identified potential issues in your infrastructure code.

**Risk Level:** ${analysis.riskLevel}
**Summary:** ${analysis.summary}

**Affected Resources:**
${analysis.affectedResources.map(r => `- ${r}`).join('\n') || '- None detected'}

**Details:**
${analysis.details}

**Recommended Actions:**
${analysis.riskLevel === 'High' ? '- STOP: Do not deploy without review\n- Review provider changelogs\n- Test in staging environment' : ''}
${analysis.riskLevel === 'Medium' ? '- Review before deploying to production\n- Check for deprecation warnings\n- Update provider versions if needed' : ''}
${analysis.riskLevel === 'Low' ? '- Review at your convenience\n- Consider best practices updates' : ''}

**Source:** CloudSentinel Terraform Guard

${terraformCode ? `**Terraform Code:**\n\`\`\`hcl\n${terraformCode.substring(0, 2000)}${terraformCode.length > 2000 ? '\n... (truncated)' : ''}\n\`\`\`` : ''}
    `.trim(),
    issueType: analysis.riskLevel === 'High' ? JiraIssueType.BUG : JiraIssueType.TASK,
    priority: priorityMap[analysis.riskLevel] || JiraPriority.MEDIUM,
    labels: [
      'cloudsentinel',
      'terraform',
      'infrastructure',
      `risk-${analysis.riskLevel.toLowerCase()}`,
      ...analysis.affectedResources.map(r => r.split('.')[0]).filter((v, i, a) => a.indexOf(v) === i),
    ],
  };

  return createJiraTicket(request);
};

/**
 * Get official status page URL for cloud provider
 */
const getProviderStatusUrl = (provider: CloudProvider): string => {
  const urls: Record<CloudProvider, string> = {
    [CloudProvider.AWS]: 'https://health.aws.amazon.com/health/status',
    [CloudProvider.GCP]: 'https://status.cloud.google.com/',
    [CloudProvider.AZURE]: 'https://status.azure.com/en-us/status',
  };
  return urls[provider] || '';
};

/**
 * Search for existing similar tickets to avoid duplicates
 */
export const searchExistingTickets = async (
  query: string
): Promise<{ found: boolean; tickets: Array<{ key: string; summary: string }> }> => {
  if (!isJiraConfigured()) {
    return { found: false, tickets: [] };
  }

  try {
    const jql = encodeURIComponent(
      `project = ${JIRA_PROJECT_KEY} AND labels = cloudsentinel AND status != Done AND text ~ "${query}" ORDER BY created DESC`
    );

    const response = await fetch(
      `${JIRA_BASE_URL}/rest/api/3/search?jql=${jql}&maxResults=5`,
      {
        method: 'GET',
        headers: {
          'Authorization': getAuthHeader(),
          'Content-Type': 'application/json',
        },
      }
    );

    if (!response.ok) {
      return { found: false, tickets: [] };
    }

    const data = await response.json();
    const tickets = data.issues?.map((issue: { key: string; fields: { summary: string } }) => ({
      key: issue.key,
      summary: issue.fields.summary,
    })) || [];

    return { found: tickets.length > 0, tickets };
  } catch {
    return { found: false, tickets: [] };
  }
};

/**
 * Add comment to existing ticket
 */
export const addCommentToTicket = async (
  ticketKey: string,
  comment: string
): Promise<boolean> => {
  if (!isJiraConfigured()) {
    return false;
  }

  try {
    const payload = {
      body: {
        type: 'doc',
        version: 1,
        content: [
          {
            type: 'paragraph',
            content: [{ type: 'text', text: comment }],
          },
        ],
      },
    };

    const response = await fetch(
      `${JIRA_BASE_URL}/rest/api/3/issue/${ticketKey}/comment`,
      {
        method: 'POST',
        headers: {
          'Authorization': getAuthHeader(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }
    );

    return response.ok;
  } catch {
    return false;
  }
};

/**
 * Get Jira configuration status for UI display
 */
export const getJiraConfigStatus = (): {
  configured: boolean;
  baseUrl: string;
  projectKey: string;
} => {
  return {
    configured: isJiraConfigured(),
    baseUrl: JIRA_BASE_URL ? new URL(JIRA_BASE_URL).hostname : 'Not configured',
    projectKey: JIRA_PROJECT_KEY || 'Not configured',
  };
};
