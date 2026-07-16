# Changelog

All notable changes to BrowserStream are documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-07-16

### Added

- Self-hosted WebRTC screen sharing for meeting-room displays.
- Display-first room enrollment with secret display tokens and rotating six-character presenter codes.
- Presenter-selected screen audio with transmitted mute and volume controls.
- Kiosk audio playback with an **Enable audio** fallback when audible autoplay is blocked.
- TURN/STUN configuration and an optional bundled coturn deployment.
- Interactive installation, safe existing-installation updates, and atomic room additions.
- Kiosk enrollment URL generator with multi-room support.
- HTTPS reverse-proxy and WAN deployment guidance.
- Architecture diagram and deployment documentation.
- Automated Go, frontend, installer, Compose, container, vulnerability, and secret checks.

### Security

- Restricted coturn Linux capabilities and enabled `no-new-privileges`.
- Protected generated configuration and enrollment tokens from accidental disclosure.
- Enforced allowed WebSocket origins, authenticated displays, presenter verification codes, and failed-code lockouts.
- Kept committed example configuration free of production secrets and organization-specific network values.

### Compatibility

- Screen video works in browsers implementing `getDisplayMedia()`.
- Screen/tab audio depends on browser and operating-system capture support; Chrome and Edge provide the most consistent audio support.
- Firefox presentation remains video-only because Firefox does not currently provide display audio through `getDisplayMedia()`.

[1.0.0]: https://github.com/CLSMCSMII/browserstream/releases/tag/v1.0.0
