# Contributing

Thank you for improving BrowserStream.

1. Open an issue for substantial behavior or protocol changes.
2. Create a focused branch and avoid committing generated configuration, keys, certificates, logs, or environment files.
3. Add a failing test first for backend behavior. Preserve the display-first kiosk flow and test both successful authentication and abuse cases.
4. Run:

   ```sh
   gofmt -w main.go core/*.go
   go vet ./...
   go test -race ./...
   go build ./...
   docker compose config
   ```

5. Update `config.example.json`, README, security notes, and tests when configuration or deployment behavior changes.
6. Submit a concise pull request describing threat-model impact, test output, and browser testing.

Use safe DOM APIs (`textContent`, `createElement`) for configured/user-controlled text. Do not add permanent TURN credentials, presenter codes in URLs, permissive WebSocket origins, floating container tags, or embedded TLS material.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
