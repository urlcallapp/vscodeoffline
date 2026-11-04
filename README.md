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

The deployment model redirects VS Code service hostnames to this gallery. There are two separate host lists: the few hosts redirected to the gallery on the offline network, and the real Microsoft services the online sync host must reach. They must not be mixed up.

### Hosts redirected to the gallery (offline network)

| Host | What the gallery serves for it |
| --- | --- |
| `update.code.visualstudio.com` | Update metadata and installer bytes |
| `marketplace.visualstudio.com` | Gallery query and item APIs |
| `main.vscode-cdn.net` | Recommendations and extension-control data |

- The gallery rewrites the asset and download URLs it serves to `PUBLIC_URL`, so clients following those URLs stay on hosts you control, provided `PUBLIC_URL` itself resolves on the offline network with a trusted certificate.
- Microsoft's [network documentation](https://code.visualstudio.com/docs/setup/network) lists `*.vscode-cdn.net` as a wildcard; the mirror impersonates only `main.vscode-cdn.net`. Other subdomains, such as the per-UUID webview content host referenced by `webviewContentExternalBaseUrlTemplate`, are not mirrored.
- Extensions can also fetch their own runtime resources (language servers, toolchains, data downloads) from other hosts. Which additional hosts matter is feature-dependent: assess the client build and extensions in use, and do not point unrecognized hosts at this service.

### Online sync egress (Internet-connected host)

The online sync system must still resolve the real public services, not its offline mirror. Checked against live Microsoft services on 2026-11-04:

| Host | Purpose |
| --- | --- |
| `marketplace.visualstudio.com` | Extension gallery queries |
| `*.gallerycdn.vsassets.io` | Extension asset downloads (primary host returned in gallery metadata) |
| `*.gallery.vsassets.io` | Extension asset downloads (fallback host returned in gallery metadata) |
| `update.code.visualstudio.com` | Installer update metadata |
| `vscode.download.prss.microsoft.com` | Actual VS Code binary downloads (the update API's redirect target) |
| `main.vscode-cdn.net` | Recommendations and the malicious-extension list |

Old releases pointing at the retired `az764295.vo.msecnd.net` CDN are skipped.

### Microsoft hosts this gallery does not serve

Other hostnames in Microsoft's list belong to features this project does not mirror. None of them are served by this gallery, so do not redirect them to it. Handle them per feature: disable what your deployment does not need, and prepare separate offline alternatives for what it does.

| Feature | Hosts |
| --- | --- |
| Settings Sync | `vscode-sync.trafficmanager.net`, `vscode-sync-insiders.trafficmanager.net` |
| `vscode.dev` sign-in redirect | `vscode.dev` |
| Web extensions | `*.vscode-unpkg.net` |
| Experiments | `default.exp-tas.com` |
| Badges | `vsmarketplacebadges.dev` |
| Extension-fetched dependencies (for example C++/C# extension dependencies) | `download.visualstudio.microsoft.com` - needs its own offline preparation if those extensions are used |
| Not product endpoints | GitHub raw content, documentation, link forwarding |

Client distribution, build configuration, and VS Code version can change which hosts are needed. Confirm the actual requests in the target client's Developer Tools/Shared logs. Do not infer VSCodium, Code OSS, VS Code Desktop, VS Code Server, and Web are interchangeable.

### TLS

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
- Check the Marketplace-advertised VSIX SHA-256 when present, including before cached-file reuse. Reject malformed digest metadata and valid-ZIP mismatches without replacing prior bytes. This is an integrity check against upstream metadata, not independent signature authentication.
- Store local SHA-256 receipts. Caches without receipts, including downloads created by the old implementation, are fetched again once. This can increase the first sync's bandwidth and duration.
- Publish extension metadata only after downloads succeed. Follow extension packs and `extensionDependencies` transitively with duplicate/cycle and blocklist guards.

If installation fails, capture:

- The exact client distribution/version and OS/architecture
- Extension ID, version and target platform
- Request URL and status
- Shared log error code

Distinguish:

- TLS trust errors
- Missing signature archives
- Mismatched or corrupted bytes
- Unsupported verifier environments
- Certificate-revocation/trust failures

Language servers, runtimes and other resources downloaded by extensions may also require separate offline preparation.

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

Not implemented:

- Microsoft's authenticated enterprise manifest/sign-in workflow
- Arbitrary Marketplace APIs
- A general-purpose upstream proxy
- Extension content re-signing

Query version flags are not fully projected: cached versions/platform variants are returned, with compatibility selection left to the client. End-to-end installation on Ron's target VS Code distribution still needs its version/error evidence and testing. No claim of full current-client certification is made.

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

For production use Gunicorn's `server:create_app()` factory from `vscoffline/`. Importing the module no longer starts background watchers or exits if the default artifact directory is absent. `create_app(start_workers=False)` supports isolated protocol tests. CI additionally builds both images and smoke-tests the gallery container without TLS; that container smoke passes on the merged main branch.

## Source references

- [Original project](https://github.com/LOLINTERNETZ/vscodeoffline), MIT license retained in `LICENCE`.
- [VS Code 1.144.0 gallery service](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/common/extensionGalleryService.ts)
- [VS Code 1.144.0 gallery resources](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/common/extensionGalleryManifestService.ts)
- [VS Code 1.144.0 download/verification flow](https://raw.githubusercontent.com/microsoft/vscode/1.144.0/src/vs/platform/extensionManagement/node/extensionDownloader.ts)
- [Microsoft network hostnames](https://code.visualstudio.com/docs/setup/network). The DNS lists above were checked against it and against live Microsoft services on 2026-11-04.
- [Microsoft extension security](https://code.visualstudio.com/docs/configure/extensions/extension-runtime-security)
- [Microsoft enterprise extension guidance](https://code.visualstudio.com/docs/enterprise/extensions). Its official private marketplace requires enterprise account access and is not substituted for this project.

Check publisher/Marketplace terms before redistribution. This fork's MIT license does not change the licenses of VS Code binaries, third-party extensions, or Microsoft's verification tool.

## Artifact pages and VS Code's Marketplace link

The compact catalog separates **Extensions** and **Binaries**:

- Extension pages expose the original mirrored icon, version/platform choices, readme, changelog, manifest, license, asset links and full metadata.
- Binaries are indexed from `installers/<platform>/<quality>/*.json`; each platform/channel has a detail page with actual version, architecture, commit, hashes and download metadata.

Only existing mirrored metadata is shown. Markdown/manifest/license artifacts are displayed as safe text, not executed as HTML.

VS Code 1.144.0 constructs its Marketplace detail URL from the client product/gallery `itemUrl` plus `?itemName={publisher}.{name}`. The server supports:

```text
GET /items?itemName=publisher.extension
```

- It redirects to that exact local `/extensions/publisher.extension` page, case-insensitively matching the mirrored canonical ID.
- Unknown extensions return 404; malformed IDs return 400.
- There is no redirect to the public Marketplace or a generic search page.
- The optional review query is accepted, but this mirror has no review-writing service.

In the inherited DNS-mirror deployment, a standard client's `https://marketplace.visualstudio.com/items?itemName=...` URL must resolve to this service with correctly trusted matching TLS. If the client distribution exposes its own supported gallery configuration, its item URL must point to this service's `/items`; do not assume `PUBLIC_URL` alone changes the client-generated Marketplace link. This fork does not edit/repackage VS Code or silently alter client configuration. Microsoft's enterprise Private Marketplace policy is a different authenticated workflow, not a promise that arbitrary custom item URLs are a supported user setting in branded VS Code.

Source trace: the tagged `extensionGalleryManifestService.ts` creates the item template; `extensionGalleryService.ts` expands publisher/name; `extensionsWorkbenchService.ts` exposes that as `extension.url`; `extensionEditor.ts` opens it from the Marketplace resource link. Local browser navigation through `/items?itemName=ms-vscode.hexeditor` was tested to land on its exact extension page. Clicking from Ron's actual VS Code desktop still needs his client/network validation.

The GitHub releases API listed 1.144.0 at inspection time, while the live Microsoft updater served a 1.144.1 CLI Alpine x64 binary. The latter was downloaded locally and checked against its advertised SHA-256 for the Binaries UI test. It is not bundled in this repository. The protocol inspection above remains explicitly pinned to the available 1.144.0 source tag rather than claiming every shipping build is certified.
