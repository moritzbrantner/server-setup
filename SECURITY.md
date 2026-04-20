# Security Policy

## Supported Versions

Security fixes are handled on the `main` branch.

## Reporting a Vulnerability

Do not publish active vulnerabilities in public issues. Report them privately to the repository owner, or use GitHub private vulnerability reporting if it is enabled for the repository.

Include:

- affected commit or release
- affected script, service, or dashboard route
- reproduction steps
- expected impact
- any relevant logs with secrets removed

## Secret Handling

Never commit real deployment secrets, GitHub tokens, webhook secrets, DNS provider keys, TLS private keys, `.env` files, or generated host registry/state files.
