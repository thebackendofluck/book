<!-- AcmetoCasino Portal - Root Application Component -->
<!-- Material Design shell with responsive drawer navigation -->

<template>
  <div class="page-container">
    <md-app md-mode="reveal">
      <md-app-toolbar class="md-primary">
        <md-button class="md-icon-button" @click="toggleMenu">
          <md-icon>menu</md-icon>
        </md-button>
        <h3 class="md-title" :style="{flex:1}">{{$store.state.view.title}}</h3>
        <Usericon />
      </md-app-toolbar>

      <md-app-drawer :md-active.sync="menuVisible" md-persistent="full">
        <md-toolbar class="md-transparent" md-elevation="0">
          <span>Menu</span>
          <div class="md-toolbar-section-end">
            <md-button class="md-icon-button md-dense" @click="toggleMenu">
              <md-icon>keyboard_arrow_left</md-icon>
            </md-button>
          </div>
        </md-toolbar>
        <Menu />
      </md-app-drawer>

      <md-app-content>
        <router-view/>
      </md-app-content>
    </md-app>
  </div>
</template>

<script lang="ts">
import { Component, Prop, Vue } from 'vue-property-decorator';
import Usericon from './components/Usericon.vue';
import Menu from './components/Menu.vue';

@Component({
  props: {
    foo: { type: String },
    auth: { type: Object, required: false },
  },
  components: {
    Usericon,
    Menu,
  },
})
export default class App extends Vue {
  public data() {
    return {
      menuVisible: false,
    };
  }

  protected toggleMenu() {
    this.$data.menuVisible = !this.$data.menuVisible;
  }
}
</script>

<style lang="scss">
  @import '~material-icons/iconfont/material-icons.css';

  .md-app {
    min-height: 100vh;
    border: 1px solid #000000;
  }

  .md-drawer {
    width: 230px;
    max-width: calc(100vw - 125px);
  }
</style>
