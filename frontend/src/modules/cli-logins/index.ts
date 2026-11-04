import type { RouteRecordRaw } from 'vue-router';

import CliLoginsPage from './CliLoginsPage.vue';

// The page where a person sees and revokes the devices signed in with the garden command.
export const cliLoginsRoutes: RouteRecordRaw[] = [
  { path: '/cli-logins', name: 'cli-logins', component: CliLoginsPage },
];
