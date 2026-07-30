# CloudSentinel

Cloud provider status monitoring and Terraform analysis dashboard for iGaming incident management.

## Overview

CloudSentinel is a real-time monitoring dashboard that helps iGaming operations teams:
- Monitor AWS, GCP, and Azure health status
- Analyze Terraform configurations for potential breaking changes
- Automatically create Jira tickets for incidents
- Track provider API changes and deprecations

## Features

| Feature | Description | AI Required |
|---------|-------------|-------------|
| **Cloud Provider Status** | Real-time health monitoring for AWS, GCP, Azure | Optional |
| **Terraform Guard** | Analyze .tf files for breaking changes and deprecations | Optional |
| **Provider News** | Track recent API changes and changelogs | Optional |
| **Jira Integration** | Automatic incident ticket creation | No |

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CloudSentinel Dashboard                     │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ AWS Status  │  │ GCP Status  │  │Azure Status │             │
│  │   Card      │  │   Card      │  │   Card      │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│         └────────────────┼────────────────┘                     │
│                          ▼                                      │
│                ┌─────────────────┐                              │
│                │ Gemini AI       │ ◄── Optional                 │
│                │ (Status Check)  │                              │
│                └────────┬────────┘                              │
│                         │                                       │
│  ┌──────────────────────┼──────────────────────┐               │
│  │            Terraform Guard                   │               │
│  │  ┌─────────────┐    ┌─────────────────────┐ │               │
│  │  │ Code Input  │───►│ AI Analysis         │ │               │
│  │  └─────────────┘    │ (Risk Assessment)   │ │               │
│  │                     └──────────┬──────────┘ │               │
│  └────────────────────────────────┼────────────┘               │
│                                   │                             │
│                                   ▼                             │
│                        ┌─────────────────┐                      │
│                        │ Jira Integration│ ◄── Optional         │
│                        │ (Auto-Ticket)   │                      │
│                        └─────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

### Incident Detection Flow

```
1. CloudSentinel checks provider status
         │
         ▼
2. Status = Degraded/Outage?
         │
    ┌────┴────┐
    │ Yes     │ No ──► Continue monitoring
    ▼         │
3. Search for existing Jira ticket
         │
    ┌────┴────┐
    │ Found   │ Not Found
    │         ▼
    │    4. Create new Jira ticket
    ▼         │
5. Add comment to existing ticket
         │
         ▼
6. Notify operations team (Slack, Email)
         │
         ▼
7. Update dashboard status
```

## Integrations

### LLM Integration (Optional)

CloudSentinel works **fully without AI integration**. The Gemini API key is optional:

| Feature | With AI | Without AI |
|---------|---------|------------|
| Cloud Status | Real-time AI-summarized health | Static + links to official status pages |
| Terraform Analysis | AI-powered deprecation detection | Basic resource extraction |
| Provider News | AI-curated changelog updates | Placeholder message |

#### Enabling AI Features

1. Get a free Gemini API key: https://makersuite.google.com/app/apikey
2. Set in `.env.local`:
   ```bash
   VITE_GEMINI_API_KEY=your-api-key-here
   ```

### Jira Integration (Optional)

CloudSentinel can automatically create and update Jira tickets for incidents.

#### Features

- **Auto-Create Tickets**: Create incident tickets when provider status changes
- **Risk-Based Priority**: Map CloudSentinel risk levels to Jira priorities
- **Duplicate Detection**: Search for existing tickets before creating new ones
- **Comment Updates**: Add updates to existing tickets for ongoing incidents
- **Terraform Tickets**: Create tasks for high-risk Terraform configurations

#### Configuration

1. Create Jira API token: https://id.atlassian.com/manage-profile/security/api-tokens
2. Set in `.env.local`:
   ```bash
   VITE_JIRA_BASE_URL=https://company.atlassian.net
   VITE_JIRA_API_TOKEN=your-api-token
   VITE_JIRA_USER_EMAIL=ops-team@company.com
   VITE_JIRA_PROJECT_KEY=INC
   ```

#### Priority Mapping

| CloudSentinel Status | Jira Priority |
|---------------------|---------------|
| Outage | P1 - Highest |
| Degraded | P2 - High |
| Unknown | P3 - Medium |
| Operational | P4 - Low |

| Terraform Risk | Jira Priority |
|----------------|---------------|
| High | P2 - High |
| Medium | P3 - Medium |
| Low | P4 - Low |

#### Ticket Templates

**Cloud Incident Ticket:**
```
Summary: [CloudSentinel] AWS Outage - 2024-01-15
Labels: cloudsentinel, cloud-incident, aws, outage

Description:
CloudSentinel has detected an issue with AWS.

Status: Outage
Last Checked: 14:30:00
Details: Multiple services reporting issues in us-east-1

Recommended Actions:
- Activate incident response procedure
- Notify stakeholders
- Check official status page

Provider Status Page: https://health.aws.amazon.com
```

**Terraform Analysis Ticket:**
```
Summary: [CloudSentinel] Terraform Risk: High - Deprecated resource detected
Labels: cloudsentinel, terraform, infrastructure, risk-high, aws_instance

Description:
CloudSentinel Terraform Guard has identified potential issues.

Risk Level: High
Affected Resources:
- aws_instance.web
- aws_db_instance.main

Recommended Actions:
- STOP: Do not deploy without review
- Review provider changelogs
- Test in staging environment
```

## Quick Start

### Prerequisites

- Node.js 18+ (or Docker)

### Local Development

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build
```

### Docker Deployment

```bash
# Build image
docker build -t cloudsentinel .

# Run (without AI/Jira)
docker run -p 3000:80 cloudsentinel

# Run with all integrations
docker run -p 3000:80 \
  -e VITE_GEMINI_API_KEY=your-gemini-key \
  -e VITE_JIRA_BASE_URL=https://company.atlassian.net \
  -e VITE_JIRA_API_TOKEN=your-jira-token \
  -e VITE_JIRA_USER_EMAIL=ops@company.com \
  -e VITE_JIRA_PROJECT_KEY=INC \
  cloudsentinel
```

### Docker Compose

```bash
docker-compose up -d
```

## Project Structure

```
cloudsentinel/
├── App.tsx                    # Main React application
├── index.tsx                  # Entry point
├── types.ts                   # TypeScript interfaces
├── components/
│   ├── Header.tsx             # Dashboard header
│   ├── StatusCard.tsx         # Provider status cards
│   └── TerraformAnalyzer.tsx  # Terraform code analyzer
├── services/
│   ├── geminiService.ts       # AI service (optional)
│   └── jiraService.ts         # Jira integration (optional)
├── .env.local                 # Environment configuration
├── package.json               # Dependencies
├── vite.config.ts             # Build configuration
├── Dockerfile                 # Container build
├── docker-compose.yml         # Orchestration
├── nginx.conf                 # Production server
└── tsconfig.json              # TypeScript configuration
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_GEMINI_API_KEY` | No | Gemini API key for AI features |
| `VITE_JIRA_BASE_URL` | No | Jira instance URL |
| `VITE_JIRA_API_TOKEN` | No | Jira API token |
| `VITE_JIRA_USER_EMAIL` | No | Email for Jira authentication |
| `VITE_JIRA_PROJECT_KEY` | No | Default project for tickets (default: INC) |
| `VITE_AUTO_REFRESH_INTERVAL` | No | Status refresh interval in ms (default: 300000) |
| `VITE_AUTO_CREATE_TICKETS` | No | Auto-create tickets for outages (default: false) |

## Technology Stack

- **Frontend**: React 18 + TypeScript
- **Styling**: TailwindCSS 3
- **Build**: Vite 5
- **AI (Optional)**: Google Gemini 1.5 Flash
- **Ticketing (Optional)**: Jira REST API v3
- **Container**: Docker + nginx

## Incident Response Workflow

### Using CloudSentinel During Incidents

1. **Detection Phase**
   - CloudSentinel displays provider status in real-time
   - Red/orange status cards indicate issues
   - AI provides detailed health summaries (if enabled)

2. **Investigation Phase**
   - Click on status card to view detailed message
   - Check "Recent API & Provider Changes" section
   - Use official status page links for authoritative info

3. **Ticket Creation**
   - Click "Create Jira Ticket" on affected provider card
   - Or enable `VITE_AUTO_CREATE_TICKETS` for automatic creation
   - Ticket includes all relevant details and recommended actions

4. **Resolution Tracking**
   - Updates are automatically added to existing tickets
   - Status changes trigger new comments
   - Resolution time is tracked

### Terraform Change Validation

1. **Pre-Deployment Check**
   - Paste Terraform code into Terraform Guard
   - Run analysis to check for breaking changes

2. **Risk Assessment**
   - Review risk level (Low/Medium/High)
   - Check affected resources list
   - Read detailed analysis

3. **Documentation**
   - Create Jira task for high-risk changes
   - Link to change management tickets
   - Document in deployment notes

## API Reference

### Jira Service

```typescript
import {
  createCloudIncidentTicket,
  createTerraformAnalysisTicket,
  searchExistingTickets,
  addCommentToTicket,
  isJiraConfigured,
} from './services/jiraService';

// Check if Jira is configured
if (isJiraConfigured()) {
  // Create incident ticket
  const result = await createCloudIncidentTicket(status);
  console.log('Ticket created:', result.ticketUrl);

  // Search for duplicates
  const existing = await searchExistingTickets('AWS Outage');
  if (existing.found) {
    await addCommentToTicket(existing.tickets[0].key, 'Status update...');
  }
}
```

### Gemini Service

```typescript
import {
  fetchCloudStatusWithAI,
  analyzeTerraformCode,
  fetchCloudNews,
  isAIEnabled,
} from './services/geminiService';

// Check if AI is available
if (isAIEnabled()) {
  const statuses = await fetchCloudStatusWithAI();
  const analysis = await analyzeTerraformCode(terraformCode);
}
```

## License

MIT
