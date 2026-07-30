// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { GoogleGenerativeAI } from "@google/generative-ai";
import { CloudStatus, CloudProvider, TerraformAnalysis, NewsItem } from '../types';

/**
 * CloudSentinel AI Service
 *
 * This service provides AI-powered cloud status monitoring and Terraform analysis.
 * LLM integration is OPTIONAL - the application works fully without an API key,
 * returning static/mock data instead.
 *
 * To enable AI features:
 * 1. Get a Gemini API key from https://makersuite.google.com/app/apikey
 * 2. Set VITE_GEMINI_API_KEY in your .env.local file
 * 3. Or pass it as environment variable in Docker: -e VITE_GEMINI_API_KEY=your-key
 */

// Initialize Gemini Client - API key is optional
const apiKey = import.meta.env.VITE_GEMINI_API_KEY || import.meta.env.VITE_API_KEY || '';
const isValidApiKey = apiKey && apiKey !== '__APP_API_KEY_PLACEHOLDER__' && apiKey !== 'PLACEHOLDER_API_KEY' && apiKey.length > 10;

// Only initialize Gemini if we have a valid API key
let genAI: GoogleGenerativeAI | null = null;
if (isValidApiKey) {
  genAI = new GoogleGenerativeAI(apiKey);
  console.info('CloudSentinel: AI features enabled with Gemini API');
} else {
  console.info('CloudSentinel: Running in offline mode (no AI). Set VITE_GEMINI_API_KEY to enable AI features.');
}

/**
 * Check if AI features are available
 */
export const isAIEnabled = (): boolean => isValidApiKey && genAI !== null;

/**
 * Helper function to parse JSON from model output that might contain Markdown formatting.
 */
const parseJSON = (text: string | undefined): any => {
  if (!text) return null;
  try {
    // Remove markdown code blocks (```json ... ``` or ``` ... ```)
    const cleanText = text.replace(/```json\n?|\n?```/g, '').replace(/```/g, '').trim();
    return JSON.parse(cleanText);
  } catch (e) {
    console.error("Failed to parse JSON response:", text, e);
    return null;
  }
};

/**
 * Fetches cloud provider status using AI or returns static data.
 *
 * When AI is enabled: Uses Gemini to search for real-time cloud health status
 * When AI is disabled: Returns static "operational" status for all providers
 */
export const fetchCloudStatusWithAI = async (): Promise<CloudStatus[]> => {
  if (!genAI || !isValidApiKey) {
    console.info("AI not available. Returning static status data.");
    return getMockStatus();
  }

  const model = genAI.getGenerativeModel({ model: 'gemini-1.5-flash' });
  const prompt = `
    Search for the current service health status dashboard for AWS, Google Cloud Platform (GCP), and Microsoft Azure.
    Summarize the current global health status for each provider as of right now.
    Focus on major outages affecting core services (Compute, Storage, Networking).

    CRITICAL: Return the data in a valid JSON format ONLY. Do not use markdown code blocks.

    Format:
    [
      { "provider": "AWS", "status": "Operational" | "Degraded" | "Outage", "message": "Brief summary of issues or 'All systems nominal'" },
      { "provider": "GCP", "status": "Operational" | "Degraded" | "Outage", "message": "Brief summary..." },
      { "provider": "AZURE", "status": "Operational" | "Degraded" | "Outage", "message": "Brief summary..." }
    ]
  `;

  try {
    const result = await model.generateContent(prompt);

    const text = result.response.text();
    if (!text) throw new Error("Empty response from AI");

    const data = parseJSON(text);
    
    if (!data || !Array.isArray(data)) {
        console.warn("Invalid JSON received from AI status check");
        return getMockStatus();
    }

    const now = new Date().toLocaleTimeString();

    return data.map((item: any) => ({
      provider: item.provider as CloudProvider,
      status: item.status,
      message: item.message,
      lastChecked: now
    }));

  } catch (error) {
    console.error("Error fetching status with AI:", error);
    return getMockStatus();
  }
};

/**
 * Analyzes Terraform code for potential API breaking changes.
 *
 * When AI is enabled: Uses Gemini to analyze code for deprecations and breaking changes
 * When AI is disabled: Returns a static analysis suggesting manual review
 */
export const analyzeTerraformCode = async (code: string): Promise<TerraformAnalysis> => {
  if (!genAI || !isValidApiKey) {
    // Return helpful static analysis when AI is not available
    return getMockTerraformAnalysis(code);
  }

  const prompt = `
    You are a Senior DevOps Engineer and Terraform Expert.
    Analyze the following Terraform configuration code.
    Identify any resources that might be affected by RECENT (last 6-12 months) API changes, deprecations, or version updates in AWS, GCP, or Azure providers.
    
    Terraform Code:
    ${code.substring(0, 10000)} 

    Return a JSON response adhering to this schema:
    {
      "riskLevel": "Low" | "Medium" | "High",
      "summary": "One sentence summary of the risk",
      "details": "A detailed markdown string explaining the specific deprecations or changes found.",
      "affectedResources": ["resource_type.name", ...]
    }
  `;

  const model = genAI.getGenerativeModel({ model: 'gemini-1.5-flash', generationConfig: { responseMimeType: "application/json" } });
  const result = await model.generateContent(prompt);

  const text = result.response.text();
  if (!text) throw new Error("Failed to analyze code");
  
  return JSON.parse(text) as TerraformAnalysis;
};

/**
 * Fetches recent cloud provider news and changelogs.
 *
 * When AI is enabled: Uses Gemini to search for recent provider changes
 * When AI is disabled: Returns empty array (no news available)
 */
export const fetchCloudNews = async (): Promise<NewsItem[]> => {
  if (!genAI || !isValidApiKey) {
    console.info("AI not available. News feed disabled.");
    return getMockNews();
  }

  const prompt = `
    Search for the latest "Terraform Provider Changelog" and "API Breaking Changes" for AWS, Google Cloud, and Azure from the last 7 days.
    Select the top 3 most critical updates that a DevOps engineer should know about.
    
    CRITICAL: Return valid JSON only. Do not use markdown.
    Format: [{"title": "...", "date": "...", "provider": "AWS"|"GCP"|"AZURE", "description": "..."}]
  `;

  try {
    const model = genAI.getGenerativeModel({ model: 'gemini-1.5-flash' });
    const result = await model.generateContent(prompt);

    const data = parseJSON(result.response.text());
    if(!data || !Array.isArray(data)) return [];
    return data;
  } catch (e) {
      console.error(e);
      return [];
  }
}

// ============================================================================
// Fallback/Mock Data - Used when AI is not available
// ============================================================================

/**
 * Returns static cloud provider status when AI is not available.
 * In production, you could fetch from actual status pages instead.
 */
const getMockStatus = (): CloudStatus[] => {
  const now = new Date().toLocaleTimeString();
  return [
    {
      provider: CloudProvider.AWS,
      status: 'Operational',
      lastChecked: now,
      message: 'Status check via AI unavailable. Check https://health.aws.amazon.com for real-time status.',
    },
    {
      provider: CloudProvider.GCP,
      status: 'Operational',
      lastChecked: now,
      message: 'Status check via AI unavailable. Check https://status.cloud.google.com for real-time status.',
    },
    {
      provider: CloudProvider.AZURE,
      status: 'Operational',
      lastChecked: now,
      message: 'Status check via AI unavailable. Check https://status.azure.com for real-time status.',
    },
  ];
};

/**
 * Returns static Terraform analysis when AI is not available.
 * Provides basic resource extraction from the code.
 */
const getMockTerraformAnalysis = (code: string): TerraformAnalysis => {
  // Extract resource names from Terraform code using regex
  const resourcePattern = /resource\s+"([^"]+)"\s+"([^"]+)"/g;
  const affectedResources: string[] = [];
  let match;

  while ((match = resourcePattern.exec(code)) !== null) {
    affectedResources.push(`${match[1]}.${match[2]}`);
  }

  // Detect provider from resource types
  const hasAws = code.includes('aws_');
  const hasGcp = code.includes('google_');
  const hasAzure = code.includes('azurerm_');

  const providers = [];
  if (hasAws) providers.push('AWS');
  if (hasGcp) providers.push('GCP');
  if (hasAzure) providers.push('Azure');

  return {
    riskLevel: 'Low',
    summary: `AI analysis unavailable. Found ${affectedResources.length} resources for ${providers.join(', ') || 'unknown provider'}.`,
    details: `**Manual Review Recommended**

CloudSentinel is running without AI capabilities. To enable AI-powered analysis:
1. Get a Gemini API key from https://makersuite.google.com/app/apikey
2. Set the VITE_GEMINI_API_KEY environment variable
3. Restart the application

**Resources Detected:**
${affectedResources.map(r => `- ${r}`).join('\n') || '- No resources found'}

**Recommended Manual Checks:**
- Review Terraform provider changelogs for recent breaking changes
- Check provider version constraints in your configuration
- Validate deprecated resource attributes
- Test in a staging environment before production deployment`,
    affectedResources,
  };
};

/**
 * Returns sample news items when AI is not available.
 */
const getMockNews = (): NewsItem[] => {
  return [
    {
      title: 'AI-powered news unavailable',
      date: new Date().toISOString().split('T')[0],
      provider: CloudProvider.AWS,
      description: 'Enable Gemini API to fetch real-time provider changelog updates.',
    },
  ];
};