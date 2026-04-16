# complex-site

A richer Node.js website example for the `server-setup` sandbox.

What it demonstrates:

- service-mode deployment for a frontend app
- `npm ci` build/setup flow
- single-process Node.js service deployment behind `nginx` and `systemd`
- runtime health checks
- client-side integration with the `rest-api` example
- graceful UI error handling when the API is unavailable

Expected deploy hostname in the sandbox:

- `app.localhost`
