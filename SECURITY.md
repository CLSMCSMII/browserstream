# Security Policy

## Supported versions

Security fixes are made on the current `main` branch. Deploy immutable revisions and keep Go, the base image, browser, reverse proxy, and coturn patched.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting for this repository, or contact the maintainer privately through the repository owner's published security channel. Include affected revision, impact, reproduction steps, and mitigations. Do not include live credentials or personal data.

## Deployment requirements

- Terminate HTTPS at a maintained reverse proxy; screen capture and secure WebSockets require a secure context.
- Keep the application port bound to loopback or a trusted private network.
- Use exact `allowed_origins`; do not place untrusted origins behind the same reverse proxy.
- Generate independent high-entropy display tokens and a TURN shared secret. Never commit `config.json`.
- Treat kiosk enrollment URLs as secrets. The token uses a URL fragment so it is not sent in HTTP requests, but browser history, screenshots, extensions, and physical access can still expose it.
- Rotate the display token after kiosk loss/reassignment and rotate the TURN secret after suspected disclosure.
- Keep debug disabled in production unless actively troubleshooting.
- Review rate limits and message limits for the deployment size.
- Restrict TURN ports and configure coturn correctly for NAT. Short-lived credentials limit reuse but do not replace network controls.
- Direct TLS is optional and accepts only external cert/key paths. Never place private key material in this repository or image.

## Historical secrets

Deleting a secret from the current tree does not remove it from Git history. Before changing repository visibility, rotate every historical credential, scan all refs/history, and rewrite history where appropriate. Coordinate history rewriting with all clone holders.
