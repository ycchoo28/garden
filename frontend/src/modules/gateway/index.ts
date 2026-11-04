import type { RouteRecordRaw } from 'vue-router';

import UsagePage from './UsagePage.vue';

// The page where a person sees today's model usage through the gateway.
export const gatewayRoutes: RouteRecordRaw[] = [{ path: '/usage', name: 'usage', component: UsagePage }];
