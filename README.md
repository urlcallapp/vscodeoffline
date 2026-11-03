# Visual Studio Code Offline Gallery and Updater

Mirror VS Code extensions and installer/update artifacts into an air-gapped network without changing their bytes. This is a fork of [LOLINTERNETZ/vscodeoffline](https://github.com/LOLINTERNETZ/vscodeoffline), not Microsoft's official Private Marketplace.

- **vscsync** runs on an Internet-connected system and downloads installer binaries, extension assets, extension packs/dependencies, and the Marketplace malicious-extension list.
- **vscgallery** serves the mirrored catalog, update API, and original assets on the offline network. The responsive catalog UI has local search, platform filters, sorting, VSIX links, and artifact browsing. It loads no external fonts, scripts, or images.

## Build and transfer

```sh
docker compose build
# Online system only. The default sync repeats every 12 hours.
docker compose up vscsync
```

Wait for `Complete` in the sync log, then stop sync or take a consistent snapshot before copying the **complete** `artifacts/` directory into the offline network. Preserve metadata, original VSIX/signature assets, and hidden checksum receipts. A checksum receipt only detects local changes; it is not a cryptographic signature or an authenticity guarantee.

The fork builds local images (`vscodeoffline-sync:local`, `vscodeoffline-gallery:local`). Its CI only validates and builds. It does not log into Docker Hub, publish images, or use the upstream author's image namespace.

## Production DNS and TLS

The existing deployment model redirects VS Code service hostnames to this gallery:

- `update.code.visualstudio.com`
- `marketplace.visualstudio.com`
- `main.vscode-cdn.net`

The online sync system must still resolve the real public services, not its offline mirror. Client distribution, build configuration, and VS Code version can change which hosts are needed. Confirm the actual requests in the target client's Developer Tools/Shared logs. Do not infer VSCodium, Code OSS, VS Code Desktop, VS Code Server, and Web are interchangeable.

Provide your organization's trusted certificate/key as `tls/tls.crt` and `tls/tls.key`, mounted read-only at `/run/tls`. Include the required service hostnames in its SANs. Deploy trust through your organization's approved process. **Do not trust or reuse the historical bundled certificate chain/key**: the leaf expired November 20, 2025, the intermediate expired May 20, 2026, and the leaf lacks `main.vscode-cdn.net`. The gallery no longer uses it by default. No domain, DNS, trust-store, or client settings are changed by this project.

```sh
docker compose up vscgallery
```

An organization-managed reverse proxy can terminate TLS instead. Set `SSLARGS=` and `BIND=0.0.0.0:8080` behind that trusted proxy. Do not expose plaintext as the production TLS setup.

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ARTIFACTS` | `/artifacts` | Local artifact directory |
| `PUBLIC_URL` | `https://update.code.visualstudio.com` | Public asset origin, not a local filesystem path |
| `BIND` | `0.0.0.0:443` | Gunicorn listen address |
| `SSLARGS` | `/run/tls` certificate/key flags | Explicit TLS configuration |
| `THREADS` | `4` | Gunicorn threads |
| `TIMEOUT` | `180` | Gunicorn timeout |

Only run this unauthenticated gallery on a trusted network or behind your own access-control proxy. The catalog and artifacts are readable to anyone who can reach it. The UI is browse/download only; it is not an authenticated admin/upload portal.

## Extension signatures and air-gap installs

Keep VS Code's extension signature verification enabled. The mirror does not re-sign, strip signatures, repackage VSIX files, or label an archive's presence as successful verification. It downloads the original `Microsoft.VisualStudio.Services.VsixSignature` asset alongside the corresponding platform/version's VSIX. VS Code verifies the cryptographic signature during installation.

New sync behavior:

- Reject failed HTTP responses and empty assets rather than cache error pages as packages.
- Download to a temporary file and atomically replace the destination only on success.
- Check VSIX/signature ZIP shape, including `.signature.p7s` in signature archives. These are structural checks, not cryptographic verification.
- Store local SHA-256 receipts. Caches without receipts, including downloads created by the old implementation, are fetched again once. This can increase the first sync's bandwidth and duration.
- Publish extension metadata only after downloads succeed. Follow extension packs and `extensionDependencies` transitively with duplicate/cycle and blocklist guards.

If installation fails, capture the exact client distribution/version, OS/architecture, extension ID/version/target platform, request URL/status, and Shared log error code. Distinguish TLS trust errors, missing signature archives, mismatched or corrupted bytes, unsupported verifier environments, and certificate-revocation/trust failures. Language servers, runtimes and other resources downloaded by extensions may also require separate offline preparation.

A local integration check mirrored `ms-vscode.hexeditor` 1.11.1, served the original package/signature through the gallery, and verified them with Microsoft's separately installed `@vscode/vsce-sign` 2.1.0: `Success`, verification executed. A modified copy failed with `PackageIntegrityCheckFailed`. The verifier is **not bundled or redistributed** by this MIT project. Its Microsoft license has separate use/distribution limits. This test does not establish successful installation in every air-gapped client or prove that certificate-revocation network access is unnecessary in every environment.

## Protocol compatibility

Routes checked against the VS Code **1.144.0** source (October 28, 2026):

| Endpoint | Purpose |
| --- | --- |
| `POST /_apis/public/gallery/extensionquery` | Filter/sort/page mirrored extensions; total count before paging |
| `GET /_apis/public/gallery/vscode/{publisher}/{name}/latest` | Raw cached extension/version metadata for the latest-version resource API |
| `POST /_apis/public/gallery/publishers/{publisher}/extensions/{name}/{version}/stats` | Accept install statistics without storing or forwarding them |
| `GET /artifacts/{path}` | Original local asset bytes; ignores client query parameters such as targetPlatform/redirect |
| `GET /api/update/{platform}/{quality}/{commit}` | Installer update metadata |
| `GET /commit:{commit}/{platform}/{quality}` | Redirect to cached installer bytes |
| `GET /extensions/workspaceRecommendations.json.gz` | Legacy recommendation data, when mirrored |
| `GET /extensions/marketplace.json` | Mirrored malicious-extension/control data |
| `GET /api/catalog` | Local UI catalog, not a VS Code protocol endpoint |

`PUBLIC_URL` determines asset paths. Local directories never become public URLs. Exact missing-name searches return no results, not unrelated recommended extensions. Deleted extensions disappear after the gallery reloads the cache. Malformed queries return 400. Directory traversal, outside-root symlinks, raw HTML filenames and hidden download receipts are blocked/escaped; untrusted HTML assets are sandboxed.

Not implemented: Microsoft's authenticated enterprise manifest/sign-in workflow; arbitrary Marketplace APIs; a general-purpose upstream proxy; extension content re-signing. Query version flags are not fully projected: cached versions/platform variants are returned, with compatibility selection left to the client. End-to-end installation on Ron's target VS Code distribution still needs its version/error evidence and testing. No claim of full current-client certification is made.

## Sync options

Use `python vscoffline/sync.py --help` for the complete list. Common choices:

```sh
# Stable binaries and recommended/specified extensions, repeats every 12 hours.
python vscoffline/sync.py --artifacts ./artifacts --sync
# Explicit list only, plus their packs/dependencies. Does not select recommendations.
python vscoffline/sync.py --artifacts ./artifacts --check-specified-extensions --update-extensions
# Broad catalog sync. Can use substantial bandwidth/disk; do not choose casually.
python vscoffline/sync.py --artifacts ./artifacts --syncall
```

`artifacts/specified.json` uses `{"extensions":["publisher.extension"]}`. Pack/dependency expansion can legitimately produce more artifacts than the number of entries. `--vscode-version` controls the sync client's advertised version. `--prerelease-extensions` permits prerelease packages. `SYNCARGS` passes options to the sync container.

Custom/private extensions need matching metadata and original asset layout. Do not label privately supplied packages as Microsoft-signed. This increment does not add a VSIX-upload interface.

## Local development and tests

Python 3.12 and 3.13 are tested. Dependencies are currently resolved from requirements rather than a reproducible lockfile.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r vscoffline/vscsync/requirements.txt -r vscoffline/vscgallery/requirements.txt pytest
python -m pytest -q tests
mkdir -p ./artifacts
ARTIFACTS="$PWD/artifacts" PUBLIC_URL=http://127.0.0.1:5000 python vscoffline/server.py
```

For production use Gunicorn's `server:create_app()` factory from `vscoffline/`. Importing the module no longer starts background watchers or exits if the default artifact directory is absent. `create_app(start_workers=False)` supports isolated protocol tests. CI additionally builds both images and smoke-tests the gallery container without TLS. The container smoke is defined but has not yet run on this unpublished branch.

## Source references

- [Original project](https://github.com/LOLINTERNETZ/vscodeoffline), MIT license retained in `LICENCE`.
- [VS Code 1.144.0 gallery service](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/common/extensionGalleryService.ts)
- [VS Code 1.144.0 gallery resources](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/common/extensionGalleryManifestService.ts)
- [VS Code 1.144.0 download/verification flow](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/node/extensionDownloader.ts)
- [Microsoft extension security](https://code.visualstudio.com/docs/configure/extensions/extension-runtime-security)
- [Microsoft enterprise extension guidance](https://code.visualstudio.com/docs/enterprise/extensions). Its official private marketplace requires enterprise account access and is not substituted for this project.

Check publisher/Marketplace terms before redistribution. This fork's MIT license does not change the licenses of VS Code binaries, third-party extensions, or Microsoft's verification tool.
