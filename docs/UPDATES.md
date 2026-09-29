# Desktop updates

MOVER SIS Monitor v0.9.0 checks a signed update manifest at
`https://j0nsh1n.github.io/mover-sis-ventilation-dashboard/updates/latest.json`.
GitHub Pages serves the small manifest. Public GitHub Releases serve the Linux
and Windows packages. The app fetches these URLs directly, without the GitHub
API or an account. The v0.8.0 app has no default manifest URL, so install
v0.9.0 manually once to start automatic checks.

## What the app does

- A frozen Linux or Windows app checks at launch, when it becomes active, and
  about once per minute while open. Source checkouts do not poll.
- The request contains no case data, research data, machine identifier, or
  telemetry. The app sends an HTTP `If-None-Match` header when it has an ETag.
- The app verifies the manifest's Ed25519 signature with its embedded public
  key before reading its version or package URLs.
- For a newer version, the app downloads the package in the background and
  checks its exact byte count and SHA-256 digest. It stages the verified files
  beside the installed app and offers a restart to apply them.
- The running install stays intact until restart. The updater keeps the prior
  install for rollback if the new one fails its startup check.

Set `MOVER_UPDATE_MANIFEST_URL` to another HTTPS URL to use a different signed
manifest. Set it to an empty string to disable automatic checks. A manifest
signed by a different key is rejected even when the URL was overridden.

GitHub Pages currently sends `Cache-Control: max-age=600` for the manifest.
Polling every minute does not guarantee that every client sees a release
within one minute. The manifest's signature and the package digests protect
the update if a cache or host serves unexpected bytes.

## Signed manifest

The stable Pages URL serves a schema 2 JSON envelope. Its `manifest` field is
a UTF-8 JSON string with the schema 1 payload below. `signature` is 128
lowercase hexadecimal characters encoding an Ed25519 signature over the exact
UTF-8 bytes of that string.

```json
{
  "schema": 2,
  "manifest": "{\"schema\":1,\"version\":\"0.9.0\",\"packages\":{...}}",
  "signature": "<128 lowercase hexadecimal characters>"
}
```

The signed payload names both platforms and their immutable release assets.

```json
{
  "schema": 1,
  "version": "0.9.0",
  "packages": {
    "linux-x86_64": {
      "url": "https://github.com/j0nsh1n/mover-sis-ventilation-dashboard/releases/download/v0.9.0/MOVER-SIS-Monitor-v0.9.0-linux-x86_64.tar.gz",
      "sha256": "<64 lowercase hexadecimal characters>",
      "size": 1
    },
    "windows-x86_64": {
      "url": "https://github.com/j0nsh1n/mover-sis-ventilation-dashboard/releases/download/v0.9.0/MOVER-SIS-Monitor-v0.9.0-windows-x86_64.zip",
      "sha256": "<64 lowercase hexadecimal characters>",
      "size": 1
    }
  }
}
```

The example sizes and digests are placeholders. The release workflow computes
them from the actual archives. The app rejects an unknown schema, invalid
signature, unsupported platform, non-HTTPS URL, invalid size, or bad digest.

## Publish sequence

`.github/workflows/release.yml` builds both packages from the same commit.
It creates a draft release, computes the package metadata, verifies that the
signing secret matches the public key embedded in the app, signs the manifest
with `tools/update-manifest/`, and attaches it to the draft release. It checks
draft asset metadata through the authenticated release listing, publishes the
release, checks final metadata and public package URLs, and deploys the signed
manifest to GitHub Pages. Pages receives only the small manifest and a landing
page, not the packages.

The Actions secret `MOVER_UPDATE_SIGNING_KEY_HEX` holds the private Ed25519
key. Only the public key is in the app. Keep the secret in GitHub Actions; do
not put it in the repository or a package. If signing or public asset
validation fails, the stable manifest must stay on the previous release.

A published version's archives must stay unchanged. To fix a release, bump
`VERSION` and publish a new version. Update the corresponding value in
`src/__version__.py` at the same time.

## Manual installation and recovery

Download a package from the [latest release](https://github.com/j0nsh1n/mover-sis-ventilation-dashboard/releases/latest)
if automatic checks are disabled or the installed version is older than
v0.9.0. Extract the archive and launch `launch.sh` on Linux or
`MOVER-SIS-Monitor.exe` on Windows. The app's Settings panel still holds the
local EMR, wave, and model paths. Updates never upload those files.

If a newer build does not start, use the prior install kept beside it, or
download the previous release package. This is a research and education tool,
not a clinical update channel.
