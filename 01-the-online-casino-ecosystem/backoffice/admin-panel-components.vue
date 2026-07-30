<!--
  ============================================================================
  Admin Panel Component Architecture -- Backoffice SPA
  ============================================================================
  This file shows how the modern backoffice admin panel structures its
  Vue components within the Nx monorepo. It demonstrates how domain modules
  (players, dashboard, search) are organized as separate Nx libraries
  that compose into a single-page application.

  The component tree mirrors the admin panel's navigation structure:
  a shell layout with sidebar navigation, where each route lazy-loads
  a feature library's root component.
  ============================================================================
-->

<template>
  <!-- Application Shell (apps/fe-main-app) -->
  <!-- The shell owns layout, navigation, and authentication -->
  <!-- Feature content is loaded via <router-view> from feature libraries -->

  <div id="backoffice-app">
    <!-- Layout from @acme.bo/ui/layouts -->
    <AdminLayout>
      <!-- Sidebar navigation -->
      <template #sidebar>
        <SidebarNav :items="navigationItems" />
      </template>

      <!-- Main content area: feature libraries render here -->
      <template #content>
        <router-view />
      </template>

      <!-- Global modals from @acme.bo/feature-modals -->
      <template #modals>
        <ModalContainer />
      </template>
    </AdminLayout>
  </div>
</template>

<script lang="ts">
import { defineComponent } from 'vue';

// Shell imports only from shared libraries -- never from feature libraries directly
// Feature libraries are loaded via lazy routes in @acme.bo/router-main
import { AdminLayout } from '@acme.bo/ui/layouts';
import { SidebarNav } from '@acme.bo/ui/components';
import { ModalContainer } from '@acme.bo/feature-modals';
import type { NavItem } from '@acme.bo/interfaces/navigation';

export default defineComponent({
  name: 'BackofficeApp',
  components: { AdminLayout, SidebarNav, ModalContainer },

  setup() {
    // Navigation items map to feature libraries
    // Each route lazy-loads its feature library's entry component
    const navigationItems: NavItem[] = [
      {
        label: 'Dashboard',
        icon: 'dashboard',
        route: '/dashboard',
        // Loads: @acme.bo/feature-dashboard
        // Team: dashboard-team
      },
      {
        label: 'Players',
        icon: 'players',
        route: '/players',
        // Loads: @acme.bo/feature-players
        // Team: player-team
        children: [
          { label: 'Search', route: '/players/search' },
          { label: 'Player Details', route: '/players/:id' },
          { label: 'Risk Profiles', route: '/players/risk' },
        ],
      },
      {
        label: 'Knowledge Base',
        icon: 'book',
        route: '/knowledge-base',
        // Loads: @acme.bo/feature-knowledge-base
        // Team: platform
      },
    ];

    return { navigationItems };
  },
});
</script>

<!--
  ============================================================================
  COMPONENT ARCHITECTURE OVERVIEW
  ============================================================================

  apps/fe-main-app/              <- Application shell (platform team)
  ├── src/main.ts                   Bootstrap, plugin registration
  ├── src/App.vue                   Root component (this file's pattern)
  └── public/index.html             HTML entry point

  libs/feature-dashboard/        <- Dashboard feature (dashboard team)
  ├── src/index.ts                  Barrel export
  └── src/lib/
      ├── DashboardPage.vue         Root page component
      ├── widgets/
      │   ├── ActivePlayersWidget.vue
      │   ├── RevenueWidget.vue
      │   └── AlertsWidget.vue
      └── composables/
          └── useDashboardData.ts   Data fetching logic

  libs/feature-players/          <- Player management (player team)
  ├── src/index.ts                  Barrel export
  └── src/lib/
      ├── PlayerSearchPage.vue      Search with filters
      ├── PlayerDetailsPage.vue     Full player profile
      ├── components/
      │   ├── PlayerHeader.vue
      │   ├── TransactionHistory.vue
      │   ├── KycDocuments.vue
      │   └── ResponsibleGaming.vue
      └── composables/
          └── usePlayerService.ts   Calls @acme.bo/service-clients/player-service

  libs/ui/components/            <- Design system (design-system team)
  ├── src/index.ts                  Public API: DataTable, SearchBar, etc.
  └── src/lib/
      ├── DataTable.vue             Reusable table with sorting/pagination
      ├── SearchBar.vue             Search input with filters
      ├── SidebarNav.vue            Navigation sidebar
      ├── StatusBadge.vue           Player status indicators
      └── FormFields/               Input components library

  libs/ui/layouts/               <- Layout shells (design-system team)
  ├── src/index.ts
  └── src/lib/
      ├── AdminLayout.vue           Main admin layout with sidebar
      └── AuthLayout.vue            Login/auth pages layout

  KEY PATTERN: The shell app (fe-main-app) never imports feature
  libraries directly. Routes in @acme.bo/router-main use dynamic
  imports to lazy-load features:

    {
      path: '/dashboard',
      component: () => import('@acme.bo/feature-dashboard'),
    }

  This means the dashboard team can deploy changes to their feature
  library without rebuilding the entire application. Nx's affected
  commands ensure only changed libraries are rebuilt and retested.
  ============================================================================
-->
