# Next-Generation Backoffice Framework

## Overview

Production backoffice framework built with Angular 4 (client) + Node.js/Express/TypeScript (server) + Oracle database. Provides modular workflow management for customer complaints, disputes, internal escalations, and admin functions.

## Architecture

```
Angular 4 Frontend  <-->  Node.js/Express API  <-->  Oracle DB
     (client/)              (server/)              (GAMEGATEWAY/BACKOFFICE)
```

### Key Features
- Configurable workflow system (complaints, disputes, escalations)
- Multi-brand support with data isolation
- Role-based access control (module + permission levels)
- UKGC complaints return reporting
- XML-based field data serialization
- Dynamic SQL script loading from filesystem

## Files

| File | Description |
|------|-------------|
| `server/api-router.ts` | Express API route configuration with module-level access control |
| `server/auth.ts` | Session-based authentication and authorization middleware |
| `server/database.ts` | Oracle connection management with dynamic SQL loading and transaction support |
| `server/workflow.ts` | Workflow engine: state transitions, item CRUD, history tracking |
| `server/customer.ts` | Customer search API with brand-scoped queries |
| `client/complaints.component.ts` | Angular component for complaint list with reactive search |
| `client/workflow.service.ts` | Angular HTTP service for workflow API communication |
| `client/complaints-ukgc-return.component.ts` | UKGC regulatory complaints return report |

## Tech Stack
- **Frontend:** Angular 4, TypeScript 2.4, RxJS
- **Backend:** Node.js 8, Express, TypeScript
- **Database:** Oracle (oracledb driver)
- **Auth:** Session-based with database validation
- **Transport:** HTTPS with certificate-based security
