# coturn integration

The root `config.json` is the source of truth for TURN URLs and the REST shared secret. `install.sh` derives the ignored `coturn/turnserver.conf` from it; do not maintain a second credential file by hand.

Start the optional pinned service with:

```sh
./install.sh --with-turn
```

The generated file enables `use-auth-secret`, `fingerprint`, disables the CLI and multicast peers, and uses relay ports 49160–49200. Review these items before Internet exposure:

- set `coturn.listening_ip`, `coturn.relay_ip`, and (behind NAT) `coturn.external_ip` in the central JSON file;
- restrict TCP/UDP 3478 and relay ports to intended client networks where possible;
- the bundled profile supports plain `turn:` only; use a separately managed coturn deployment with certificates when `turns:` is required;
- configure coturn user/allocation quotas appropriate for your deployment;
- keep `config.json` and generated `turnserver.conf` mode 0600 and out of version control;
- rotate `turn.shared_secret` if it may have been disclosed.

BrowserStream sends short-lived coturn REST/HMAC credentials only after display-token or presenter-code authentication. Permanent TURN usernames and passwords must not be embedded in browser assets.
