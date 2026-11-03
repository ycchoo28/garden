// Where the browser tests find the API, the web app and the fake sign-in provider. The defaults are the dev stack's ports.
export const API_URL = process.env.GARDEN_E2E_API_URL ?? 'http://127.0.0.1:55000';
export const WEB_URL = process.env.GARDEN_E2E_WEB_URL ?? 'http://localhost:55173';
export const OIDC_URL = process.env.GARDEN_E2E_OIDC_URL ?? 'http://127.0.0.1:55081';
