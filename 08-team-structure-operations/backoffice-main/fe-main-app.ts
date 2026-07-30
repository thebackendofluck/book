// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Vue.js 3 Frontend Entry Point
// Bootstraps the main backoffice SPA with:
// - Keycloak SSO (login-required flow)
// - PrimeVue UI component library with custom theming
// - Vuex store with role-based access control
// - Role-aware route initialization
//
// The application waits for Keycloak authentication to complete before
// mounting the Vue app. This prevents route guards from firing before
// user roles are available, which would cause false permission denials.

import { store } from '@acme.bo/store-main';
import { createApp, watch } from 'vue';
import App from './App.vue';
import { router } from '@acme.bo/router-main';
import PrimeVue from 'primevue/config';

import { Col, Container, Row } from '@acme.bo/ui/layouts';

import 'primevue/resources/themes/saga-blue/theme.css';
import 'primevue/resources/primevue.min.css';
import 'primeicons/primeicons.css';
import 'primeflex/primeflex.css';
import '@acme.bo/ui/layouts/assets/style.scss';
import 'animate.css';

import CountryFlag from 'vue-country-flag-next';
import BadgeDirective from 'primevue/badgedirective';
import Tooltip from 'primevue/tooltip';
import ConfirmationService from 'primevue/confirmationservice';
import ToastService from 'primevue/toastservice';
import { vueKeycloak, useKeycloak } from '@baloise/vue-keycloak';

const app = createApp(App).use(store);

app.use(vueKeycloak, {
  initOptions: {
    flow: 'standard',
    checkLoginIframe: false,
    onLoad: 'login-required',
  },
  config: {
    url: process.env.VUE_APP_KEYCLOAK_URL,
    realm: process.env.VUE_APP_KEYCLOAK_REALM,
    clientId: process.env.VUE_APP_KEYCLOAK_CLIENT_ID,
  },
});

// Register global layout components
app.component('WContainer', Container);
app.component('WRow', Row);
app.component('WCol', Col);
app.component('CountryFlag', CountryFlag);

// Directives
app.directive('badge', BadgeDirective);
app.directive('tooltip', Tooltip);

// Store routes for role-based menu visibility
store.dispatch('saveRoutes', router.getRoutes());

// Wait for Keycloak authentication before mounting the application.
// This ensures user roles are available for route guards and
// permission-based UI rendering.
const { keycloak, isAuthenticated, isPending } = useKeycloak();
watch(
  [isAuthenticated, isPending],
  ([isAuthenticated, isPending]) => {
    if (isAuthenticated && !isPending) {
      // Store parsed JWT token data (roles, permissions, user info)
      store.dispatch('user/setUserFromToken', keycloak.tokenParsed);

      // Mount the application only after authentication is confirmed
      app
        .use(router)
        .use(PrimeVue)
        .use(ConfirmationService)
        .use(ToastService)
        .mount('#app');

      // Brief delay for initial page load animation
      setTimeout(() => {
        store.dispatch('setIsApplicationLoading', false);
      }, 800);
    }
  }
);
