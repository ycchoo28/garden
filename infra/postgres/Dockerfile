# The local Postgres image: Postgres 17 with the pgvector extension, built by `just up`.
ARG POSTGRES_BASE_IMAGE=postgres:17.11@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3
FROM ${POSTGRES_BASE_IMAGE}

# Install the pinned pgvector package from PGDG for the distribution in the base image.
ARG PGVECTOR_APT_VERSION
COPY pgdg.asc /etc/apt/keyrings/pgdg.asc
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && echo "deb [signed-by=/etc/apt/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends "postgresql-17-pgvector=${PGVECTOR_APT_VERSION}" \
 && apt-mark hold postgresql-17-pgvector \
 && apt-get clean
