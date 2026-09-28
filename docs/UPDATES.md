# Desktop update contract

The contract for public desktop updates of MOVER SIS Monitor: how the app
checks for a new version, what the update host must provide, and how a
release gets published. The published v0.7.0 app uses manual downloads. The
v0.8.0 updater remains inactive until a public host is configured.

## Current state

- The source repo stays private. The product direction is public, downloadable
  packages on a static host. The host is not chosen yet, so no production
  update URL exists.
- `.github/workflows/release.yml` builds
  `MOVER-SIS-Monitor-v<version>-linux-x86_64.tar.gz` and
  `MOVER-SIS-Monitor-v<version>-windows-x86_64.zip` and publishes them to a
  GitHub Release. Today this is the only distribution path.
- Until a host is configured, update checks stay disabled and the app makes
  no update-related network requests.

## Product rules

1. Update work never blocks the UI. Checks, downloads, and file work run in
   the background.
2. The updater sends no research data and no case data anywhere. Its only
   network requests are the manifest GET and, for a newer version, the
   package download. No telemetry, no machine identifiers.
3. A download starts only when the manifest version is strictly newer than
   the running version (semver compare against `VERSION`). The updater never
   downgrades.
4. A verified package is staged automatically. The app offers a visible
   restart control and applies the update only after it exits. The live
   install is never modified while the app runs.

## Update check

The app checks one small static manifest:

- At launch, when the app becomes active again, and about once per minute
  while the app is open.
- The check is `GET <manifest-url>` with `If-None-Match` set to the cached
  ETag. A `304` response ends the check with no body. A `200` response
  carries the manifest.
- The manifest request has a 10-second timeout. Errors do not show dialogs;
  the app waits for the next scheduled check. A failed package download is
  retried after five minutes.
- The manifest stays small (a few kilobytes), so the once-per-minute cadence
  costs at most one small request or one `304`.

## Signed manifest v2

One stable URL serves a signed envelope. Its `manifest` field is a UTF-8 JSON
string with the schema 1 payload below. The `signature` is 128 lowercase hex
characters encoding an Ed25519 signature over the exact UTF-8 bytes of that
string. The app verifies the signature against its embedded public key before
it parses any package URL or version.

```json
{
  "schema": 2,
  "manifest": "{\"schema\":1,\"version\":\"0.8.0\",\"packages\":{...}}",
  "signature": "<128 lowercase hex characters>"
}
```

The payload has this shape (placeholder values; `updates.example.com` is not
a real host):

```json
{
  "schema": 1,
  "version": "0.8.0",
  "packages": {
    "linux-x86_64": {
      "url": "https://updates.example.com/mover-sis-monitor/v0.8.0/MOVER-SIS-Monitor-v0.8.0-linux-x86_64.tar.gz",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size": 418381824
    },
    "windows-x86_64": {
      "url": "https://updates.example.com/mover-sis-monitor/v0.8.0/MOVER-SIS-Monitor-v0.8.0-windows-x86_64.zip",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size": 441450496
    }
  }
}
```

Field rules:

- The envelope `schema` is `2`; the payload `schema` is `1`. A client rejects
  unknown values, unsigned responses, and invalid signatures.
- `version` is the semver of the release the manifest points at.
- Each key under `packages` is a platform: `linux-x86_64` or
  `windows-x86_64`.
- `url` is the HTTPS package URL for that platform.
- `sha256` is the lowercase hex digest of the package file.
- `size` is the exact file size in bytes.

### Publish order

The manifest is published last, after both archives:

1. Upload the Linux and Windows archives to their final versioned URLs.
2. Download both back and verify the size and SHA256 of each.
3. Sign the payload with the Rust CLI in `tools/update-manifest/` using the
   repository secret `MOVER_UPDATE_SIGNING_KEY_HEX`.
4. Publish the signed envelope with one atomic write at the stable URL.

Never point the manifest at a URL whose bytes are not final, and never edit
an archive after the manifest names it. Fix a bad release by publishing a new
version.

The release workflow reads `MOVER_UPDATE_SIGNING_KEY_HEX` from GitHub Actions
secrets. When the repository variable `MOVER_UPDATE_BASE_URL` is set, it checks
that the key matches the public key embedded in the app, signs the two release
archives, and attaches `update-manifest.json` to the draft GitHub Release.
The GitHub Release is in the private source repository; this attachment is
not the public manifest. The host operator must upload the two archives to
their versioned public URLs, verify the downloaded bytes, and then copy the
signed manifest to the stable public URL. No host or variable is configured
yet.

## Download, verify, stage, apply, rollback

1. Stream the package for the running platform to a temporary file outside
   the live install. Packages are 400 MB and up.
2. Verify before use: the byte count must equal `size` and the SHA256
   must equal `sha256`. A mismatch deletes the staged files and leaves the
   running version alone.
3. Stage the verified archive in a sibling `<install>.staging` directory.
4. Apply after the app exits or restarts: rename the live install to
   `<install>.backup`, rename `<install>.staging` into place, then delete the staging
   directory.
5. Keep one previous version on disk. If the new install exits during its
   startup check, restore the backup (this extends
   today's practice of keeping the 0.6.0 bundle for rollback).

## Host requirements

The host serves static files only. Any CDN or object storage that meets this
list works.

| Requirement | Reason |
| --- | --- |
| HTTPS with a valid certificate | Packages contain executable code. |
| One stable public manifest URL | The app never learns a new URL from the network. |
| Short `Cache-Control` (for example `max-age=60`) and an `ETag` on the manifest | Checks run about once per minute; `304` responses keep them cheap. |
| Immutable, versioned package URLs | A published version's bytes never change. |
| Long-lived cache headers on packages (`max-age=31536000`, `immutable`) | Repeated downloads stay cheap. |
| Range requests (optional) | Would allow a future client to resume large downloads; the current client restarts them. |
| No authentication | Packages are public downloads. |

CORS is not required. The client is the desktop app, not a browser.

## Enabling updates

There is no production manifest URL yet. The updater stays off and sends
nothing until a host is selected and the manifest URL is configured through
`MOVER_UPDATE_MANIFEST_URL` in the installed app's environment. Until then,
users update by downloading the latest release. The private source repository
is not an update host. The host must expose both packages without GitHub
authentication and publish the signed envelope only after both files are
available and their downloaded bytes match the signed size and SHA256 values.
