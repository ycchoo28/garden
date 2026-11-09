import '@fontsource-variable/inter';
import { VueQueryPlugin } from '@tanstack/vue-query';
import PrimeVue from 'primevue/config';
import { createApp } from 'vue';

import './styles.css';
import App from './app/App.vue';
import { queryClient } from './app/queryClient';
import { router } from './app/router';
import { themeOptions } from './shared/ui';

createApp(App)
  .use(router)
  .use(VueQueryPlugin, { queryClient })
  .use(PrimeVue, { theme: themeOptions })
  .mount('#app');
