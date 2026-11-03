# Local validation, November 3, 2026

Baseline: `urlcallapp/vscodeoffline` fork of `LOLINTERNETZ/vscodeoffline`, commit `9da2010c17d53cfed43adce0642bff4e5179effb`.

## Executed

- 39 pytest tests passed on Python 3.12.15 and 3.13.17.
- Python compile checks and Ruff F checks passed.
- Gunicorn 26.2.0, Python 3.12 process startup using `server:create_app()` and an empty artifact directory returned a valid empty catalog. Process stopped after the smoke check.
- Browser checks at 1440, 390, and 320px: no page overflow, no page-script errors, no external resource requests; search, empty state, and directory links worked. Actual desktop/mobile screenshots inspected.
- Real Marketplace fixture: `ms-vscode.hexeditor` 1.11.1, original VSIX 554,961 bytes, signature archive 13,111 bytes. Downloaded by refactored sync, then fetched through the local HTTP gallery.
- Microsoft's separately installed `@vscode/vsce-sign` 2.1.0 verified the HTTP-served original pair: `Success`, `didExecute: true`.
- A copy with an added ZIP entry failed: `PackageIntegrityCheckFailed`, `didExecute: true`.
- The Microsoft verifier, sample package and signature archives were used only for local validation. They are not project dependencies and are not included in this repository change.

## Not executed

- Docker/Podman builds or container smoke locally: no container engine was available. CI defines both builds and a gallery process smoke, but has not run on this unpublished branch.
- Interactive installation in Ron's actual VS Code distribution or offline network. Client distribution/version and exact failure log were not supplied. This is not a claim of full current-client or air-gap certification.
- No production DNS, certificate issuance/trust changes, domain updates, deployments, image releases or upstream publication.

## Known limits

- No full implementation of all Marketplace version-projection flags, enterprise sign-in/manifest APIs, or extension-admin uploads.
- No server-side cryptographic signature assertion. Local receipts are checksums; the VS Code client remains responsible for signature verification.
- First sync fetches legacy files without local receipts again. This can increase bandwidth/time.
- Complete artifact snapshots must be transferred after sync completes. Individual files are atomically replaced; the whole catalog is not a transactional snapshot.
- Extensions can have external runtime assets outside VSIX/dependency metadata, and applicable extension/Marketplace licenses still govern mirroring.
- TLS now requires organization-supplied certificates instead of the expired historical chain. Existing deployments need to provide the `/run/tls` mount or a trusted TLS reverse proxy.
- The gallery is unauthenticated. Place it only on a trusted network or behind an access-control proxy.
