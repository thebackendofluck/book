# AcmetoCasino Portal (ACP) - Enterprise Backoffice System

## Overview

Enterprise-grade backoffice system built with microservices architecture. Features a Vue.js PWA frontend, Express/WebSocket API server, Kafka event bus, PostgreSQL, Redis, and Flyway database migrations.

## Architecture

```
Vue.js PWA  <-->  Express + WebSocket API  <-->  Microservices (Auth, Audit, Security)
  (pwa/)          (services/client_web_api)       (services/auth, audit)
                         |                              |
                    Kafka Event Bus              PostgreSQL + Redis
```

### Key Features
- Google OAuth 2.0 integration
- JWT-based session management with Redis
- Kafka-based async microservice communication
- Request-response pattern over Kafka via Redis pub/sub
- WebSocket real-time updates
- Flyway database migrations
- Docker Compose orchestration

## Files

| File | Description |
|------|-------------|
| `docker-compose.yml` | Full service orchestration: PostgreSQL, Redis, Kafka, Zookeeper, API, Auth, Audit, PWA |
| `pwa/App.vue` | Vue.js root component with Material Design toolbar, drawer navigation |
| `pwa/Login.vue` | Google OAuth login page with session status tracking |
| `pwa/Menu.vue` | Navigation menu with route links (Home, Deposits, Login/Logout) |
| `redis.conf` | Redis configuration for session storage (password-protected, persistence) |

## Tech Stack
- **Frontend:** Vue.js 2, Vuex, Vue Router, Material Design
- **Backend:** Node.js, Express, WebSocket (ws)
- **Messaging:** Apache Kafka, Zookeeper
- **Database:** PostgreSQL 18, Flyway migrations
- **Cache:** Redis 8 (sessions, request state)
- **Auth:** Google OAuth 2.0, JWT (RSA signed)
- **Infra:** Docker, Docker Compose
