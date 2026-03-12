#!/usr/bin/env bash
set -euo pipefail

database_url() {
  if [[ -n "${TEST_DATABASE_URL:-}" ]]; then
    printf '%s\n' "$TEST_DATABASE_URL"
    return
  fi

  local required=(POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD)
  local name
  for name in "${required[@]}"; do
    if [[ -z "${!name:-}" ]]; then
      echo "Missing required environment variable: $name" >&2
      exit 1
    fi
  done

  printf 'postgresql://%s:%s@%s:%s/%s\n' \
    "$POSTGRES_USER" \
    "$POSTGRES_PASSWORD" \
    "$POSTGRES_HOST" \
    "$POSTGRES_PORT" \
    "$POSTGRES_DB"
}

psql "$(database_url)" -v ON_ERROR_STOP=1 <<'SQL'
create table if not exists demo_items (
  id serial primary key,
  title text not null,
  created_at timestamptz not null default now()
);

insert into demo_items (title)
select 'Seeded from rest-api example'
where not exists (select 1 from demo_items);
SQL
