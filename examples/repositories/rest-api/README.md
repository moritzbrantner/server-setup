# rest-api

A Python 3 REST API example for the `server-setup` sandbox.

What it demonstrates:

- service-mode deployment
- health checks
- Postgres-backed persistence using the attached compose database
- CORS access from `http://app.localhost`
- direct passthrough access from `http://127.0.0.1:4002`

Expected deploy hostname in the sandbox:

- `api.localhost`
