# Glossary

These are the words the code, the API and the screens use.
Each word means one thing.

## Words

- **Person**: someone who can sign in. People belong to one department.
- **Department**: one of Design, Engineering, Marketing or Operations.
- **Role**: a named bundle of permissions, such as `manager` or `finance`.
  People hold roles through role grants.
- **Employee**: the base role. Everyone who signs in holds it.
- **Permission**: a named right, such as `users.self` or `audit.view`.
  Each permission says whether an agent may hold it.
- **Data scope**: which departments a person may see. Finance and admins see all of them, and a manager sees their own department.
- **Operation**: a route's stable name, `module.resource.verb`, with a description. It appears in the contract.
- **Module**: a folder that owns one part of the product. A core module is part of the foundation, and a business module is part of the product.
- **Schema**: the Postgres schema that holds one module's tables.
- **Session**: a person's browser sign-in, kept in the `garden_session` cookie. It ends after 30 minutes without use.
- **CLI login**: an agent's sign-in. It is marked on every save it makes.
- **Confirmation**: a person's approval for one critical action taken by an agent, valid once and for ten minutes.
- **Confirmation gate**: the check that makes an agent's critical action wait for a confirmation.
- **Audit event**: one saved change, with who made it, whether an agent made it, the operation, the target and a summary.
- **Stored file**: content saved once by its SHA-256, and shared by every upload of the same bytes.
- **Contract**: the OpenAPI document the API publishes. Its fingerprint is the contract version.
- **Scaffold**: the template that `just new-module` uses to add a business module.
- **Persona**: one of the seeded people for development, such as `dev.admin@fernhill.example`.

## Terms to avoid

- Say **person**, not user, when you mean the human in the business.
  The `users` module and the `/api/users` routes keep their names.
- Say **confirmation**, not approval, for the agent gate.
  An approval is a decision a person makes on a claim, and that is a later module's word.
- Say **department**, not team, for the grouping that sets data scope.
