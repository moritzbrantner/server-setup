# Local status UI

`server-setup ui` exposes a small read-only browser view over the same configuration, planning, and validation core used by the CLI.

```bash
server-setup ui
```

The default address is:

```text
http://127.0.0.1:8765/
```

The page shows:

- whether the host is converged or has drift;
- the current deterministic plan;
- validation results;
- the desired non-secret configuration;
- a machine-readable `/api/state` representation of the same state.

The page refreshes every 15 seconds. It does not have a separate database, cache, or state model.

## Safety boundary

The UI is deliberately local-only and read-only in this slice.

- `--host` accepts only `localhost` or literal loopback addresses such as `127.0.0.1` and `::1`.
- Binding to `0.0.0.0`, LAN addresses, hostnames other than `localhost`, or public addresses fails closed.
- There are no mutation endpoints, forms, or buttons. HTTP POST requests are rejected.
- Responses use `Cache-Control: no-store`, a restrictive content-security policy, and frame/content-type protection headers.
- HTML output escapes configuration, plan, and validation text before rendering.

To view it from another machine, prefer an SSH tunnel rather than exposing the UI directly:

```bash
ssh -L 8765:127.0.0.1:8765 your-server
```

Then open `http://127.0.0.1:8765/` locally.

## Architecture boundary

The UI is only a presentation surface. Host inspection, planning, and validation remain authoritative in `server_setup` modules and `ServerSetupCore`. Future UI mutations, if added, must invoke the existing plan/apply safety flow rather than implementing host changes in the HTTP layer.
