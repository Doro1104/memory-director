# Working on Memory Director

This repository contains public application code. Installation and testing use generated fixtures, not the user's family media.

- Read PRIVACY.md and the deployment skill before deployment. Never enumerate photo folders, open family images/videos, inspect the user database/face embeddings, or retrieve credentials/private keys for installation checks.
- Use smoke.py and synthetic tests. doctor.py is the shareable diagnostic surface; raw logs and local-config.json can expose private paths or network details and must remain on the user's machine.
- Do not enable telemetry, remote AI inference, broad file indexing, startup tasks, public network access or new external upload destinations as a routine implementation detail.
- Keep changes scoped. Do not kill unrelated Python processes, disable firewalls, weaken TLS fingerprint checks, overwrite configuration, or delete user data.
- PUBLIC-MANIFEST.json is the release allowlist. Never upload this directory wholesale after installation. Only the verified archive produced by tools/package_release.py is suitable for distribution; no private Git history is included.
- iOS signing belongs to the user in Xcode. Do not request Apple ID passwords, signing certificates, device contents or Keychain access. An unsigned simulator build is not an installable iPhone release.
