<!-- AcmetoCasino Portal - Navigation Menu -->
<!-- Material Design list with route links for backoffice sections -->

<template>
<md-list>
    <router-link to="/">
    <md-list-item>
        <md-icon>home</md-icon>
        <span class="md-list-item-text">Home</span>
    </md-list-item>
    </router-link>

    <router-link to="/about">
    <md-list-item>
        <md-icon>help</md-icon>
        <span class="md-list-item-text">About</span>
    </md-list-item>
    </router-link>

    <router-link to="/deposits">
    <md-list-item>
        <md-icon>attach_money</md-icon>
        <span class="md-list-item-text">Deposits</span>
    </md-list-item>
    </router-link>

    <router-link to="/login">
    <md-list-item>
        <md-icon>face</md-icon>
        <span class="md-list-item-text">Login</span>
    </md-list-item>
    </router-link>

    <router-link to="/logout">
    <md-list-item>
        <md-icon>exit_to_app</md-icon>
        <span class="md-list-item-text">Logout</span>
    </md-list-item>
    </router-link>
</md-list>
</template>

<script lang="ts">
import { Component, Prop, Vue } from 'vue-property-decorator';

@Component
export default class Menu extends Vue {
  @Prop() private msg!: string;
}
</script>
