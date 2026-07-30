<!-- AcmetoCasino Portal - Login View -->
<!-- Google OAuth integration with backoffice session establishment -->

<template>
  <div class="md-layout md-alignment-center-center">
    <meta name="google-signin-scope" content="profile email openid">
    <meta name="google-signin-client_id" :content="this.$auth.getClientId()">

    <md-card md-with-hover>
      <md-ripple>
        <md-card-header>
          <div class="md-title">Login</div>
        </md-card-header>

        <!-- Loading spinner while establishing BO session -->
        <md-card-media
          v-show="$store.state.Auth.user && !$store.state.Auth.boSessionId"
          class="md-layout md-alignment-center-center"
        >
          <md-progress-spinner
            :md-diameter="100"
            :md-stroke="10"
            md-mode="indeterminate"
          ></md-progress-spinner>
        </md-card-media>

        <md-card-content>
          Logged into Google:
          <span v-if="$store.state.Auth.user">Yes</span>
          <span v-else>No</span><br/>
          Logged into BO:
          <span v-if="$store.state.Auth.boSessionId">
            Yes SESSID: {{$store.state.Auth.boSessionId}}
          </span>
          <span v-else>No</span>
        </md-card-content>

        <!-- Google Sign-In button (shown when not authenticated) -->
        <md-card-actions
          v-if="!$store.state.Auth.user"
          class="md-layout md-alignment-center-center"
        >
          <div
            class="g-signin2"
            data-onsuccess="acmeGoogleSignIn"
            data-theme="dark"
          ></div>
        </md-card-actions>

        <!-- Proceed button (shown when fully authenticated) -->
        <md-card-actions
          v-if="$store.state.Auth.user && $store.state.Auth.boSessionId"
          class="md-layout md-alignment-center-center"
        >
          <md-button class="md-raised md-primary" @click="goToHome">
            Proceed
          </md-button>
        </md-card-actions>
      </md-ripple>
    </md-card>
  </div>
</template>

<script lang="ts">
import { Component, Vue } from 'vue-property-decorator';

@Component({
  props: {
    clientId: {
      type: String,
    },
  },
})
export default class Login extends Vue {
  public data() {
    return {
      loggingIntoBo: false,
    };
  }

  public mounted() {
    this.loadGAPIScript();
  }

  protected goToHome() {
    this.$router.push('/');
  }

  // Dynamically load Google Platform API for OAuth
  private loadGAPIScript() {
    const document = window.document;
    if (document && document.head) {
      const gapiScript = document.createElement('script');
      gapiScript.setAttribute('src', 'https://apis.google.com/js/platform.js');
      gapiScript.async = true;
      gapiScript.defer = true;
      document.head.appendChild(gapiScript);
    }
  }
}
</script>

<style>
</style>
