# Users (core)

## What it owns

- Departments, people, role grants, browser sessions, sign-in attempts and CLI logins.
- The CLI login records and the token exchange (`create_cli_login`).
  The HTTP routes that approve, list and revoke CLI logins are in [agent](../agent/README.md), which calls this module's `UserService` for them.
- Sign-in through OIDC with PKCE and a checked ID token, behind the identity port.
- The role catalogue: employee, manager, finance and admin.
  Every signed-in person also holds employee.
- The data scope: which departments' records a person may see.
- Schema: `users`. Tables: `department`, `person`, `role_grant`, `web_session`, `sign_in_attempt` and `cli_login`.

## Interface

Import from `app.core.users` only: `require`, which checks a route's permission, `current_person` and `CurrentPerson`, which identify the caller, and `SESSION_COOKIE`.
It also exports `UserService` and `get_user_service`, `CliLogin`, `DepartmentView`, `advertise_permissions`, `router`, the role names and constants (`ROLES`, `ROLE_NAMES`, `PERMISSIONS`, `ADMIN`, `EMPLOYEE`, `FINANCE`, `MANAGER`).
The routes of this module are browser sign-in and sign-out, the caller, and the directory.

## Key decisions

- Only SHA-256 hashes of browser and CLI tokens are stored, so a copy of the database cannot be replayed as a session.
- A CLI login's bearer token wins over the browser cookie.
- A CLI login holds only the permissions an agent may hold.
  Person-only permissions, such as `agent.logins.revoke`, stay with the browser sign-in.
- Revoking a CLI login sets `revoked_at` and keeps the row.
- The identity provider is a port, and the OIDC adapter is one implementation of it.
- The data scope is set by role: finance and admin see every department, a manager sees their own, and everyone else sees none beyond their own record.
- Budgets Variance reads `department.id`, `code` and `name` for report labels through its documented reporting contract.
  Changes to these fields or department identity behavior must run the Budgets Variance tests.
