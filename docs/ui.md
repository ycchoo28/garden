# Frontend and UI

The frontend is Vue 3 with strict TypeScript, built by Vite and run by Bun.
It talks to the API through a client generated from the OpenAPI contract, with TanStack vue-query for caching and zod for validation.

## Shell

`App.vue` chooses the layout for each page.
Signed-in pages use `AppShell`, which shows the brand, the signed-in person, sign-out and the colour choice.
The signed-out page uses `BareLayout`, and a route opts into it with `meta: { layout: 'bare' }`.

The router asks the API who the person is before it shows a private page.
When there is no session, the browser goes to `/auth/login` and comes back to the page afterwards (`src/shared/redirects.ts`).

### Ownership direction

[ADR 0026](adr/0026-business-ownership-and-reporting-reads.md) assigns global pages, layouts and navigation composition to the application layer.
The current home/signed-out pages under Users and shell/layouts under Shared are pending refactor work.
Business pages remain in their owning modules; theme tokens and generic components remain Shared.
Central route registration does not require a central directory for business pages.

## Colour

Every colour comes from a token.
The tokens are in `frontend/src/styles.css` and in the PrimeVue preset in `frontend/src/shared/ui/theme.ts`.
Screens and components use token classes such as `text-muted`, `bg-surface` and `border-line`.

`scripts/check-ui-colours.sh` fails when a file names a palette colour or a literal colour value.
The only files allowed to name colours are `styles.css` and `theme.ts`, and the generated client is skipped.

## Dark mode

Dark mode is one class, `app-dark`, on the `<html>` element.
`public/color-mode.js` sets it before the first paint, and `src/shared/ui/colorMode.ts` lets the person change it.
With no saved choice, the app follows the system setting.

## Shared components

- `PageHeader` shows a page's one `h1`, a line of description and its actions.
- `SectionCard` is a titled region. It is named by its title, so people and tests can find it by name.
- `SkipLink` lets keyboard users jump past the header.

## Checks

`just frontend-check` runs the type check, the lint with boundaries rules, the unit tests and the colour check.
`just e2e` starts the API and the web app, signs a seeded person in through the fake identity provider, and checks the shell.
