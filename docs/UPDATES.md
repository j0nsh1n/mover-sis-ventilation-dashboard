# Desktop update contract

The contract for public desktop updates of MOVER SIS Monitor: how the app
checks for a new version, what the update host must provide, and how a
release gets published. This is a plan for future work. The current release,
v0.7.0, ships as a manual download and contains no update checker.

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
   network requests are the manifest GET and, after the user accepts an
   update, the package download. No telemetry, no machine identifiers.
3. A download starts only when the manifest version is strictly newer than
   the running version (semver compare against `VERSION`). The updater never
   downgrades.
4. An update applies only after the app exits or restarts. The live install
   is never modified while the app runs.

## Update check

The app checks one small static manifest:

- At launch, after the system resumes from sleep, and about once per minute
  while the app is open (add jitter to the interval).
- The check is `GET <manifest-url>` with `If-None-Match` set to the cached
  ETag. A `304` response ends the check with no body. A `200` response
  carries the manifest.
- Timeouts are short: 5 seconds to connect and 10 seconds for the whole
  request. Any error or timeout is logged and the app waits for the next
  scheduled check. Failures never show dialogs.
- The manifest stays small (a few kilobytes), so the once-per-minute cadence
  costs at most one small request or one `304`.

## Manifest v1

One stable URL serves the manifest at all times. Example (placeholder
values; `updates.example.com` is not a real host):

```json
{
  "schema_version": 1,
  "version": "0.8.0",
  "packages": {
    "linux-x86_64": {
      "url": "https://updates.example.com/mover-sis-monitor/v0.8.0/MOVER-SIS-Monitor-v0.8.0-linux-x86_64.tar.gz",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size_bytes": 418381824
    },
    "windows-x86_64": {
      "url": "https://updates.example.com/mover-sis-monitor/v0.8.0/MOVER-SIS-Monitor-v0.8.0-windows-x86_64.zip",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "size_bytes": 441450496
    }
  }
}
```

Field rules:

- `schema_version` is `1`. A client that sees an unknown value ignores the
  manifest.
- `version` is the semver of the release the manifest points at.
- Each key under `packages` is a platform: `linux-x86_64` or
  `windows-x86_64`.
- `url` is the HTTPS package URL for that platform.
- `sha256` is the lowercase hex digest of the package file.
- `size_bytes` is the exact file size in bytes.

### Publish order

The manifest is published last, after both archives:

1. Upload the Linux and Windows archives to their final versioned URLs.
2. Download both back and verify the size and SHA256 of each.
3. Publish the manifest with one atomic write, at the same URL every time.

Never point the manifest at a URL whose bytes are not final, and never edit
an archive after the manifest names it. Fix a bad release by publishing a new
version.

## Download, verify, stage, apply, rollback

1. Download the package for the running platform into a staging directory
   next to the live install (on Linux, under
   `~/.local/share/mover-sis-monitor`). Resume partial downloads with range
   requests when the host supports them; packages are 400 MB and up.
2. Verify before use: the byte count must equal `size_bytes` and the SHA256
   must equal `sha256`. A mismatch deletes the staged files and leaves the
   running version alone.
3. Stage the verified archive unpacked in `staged-<version>`.
4. Apply after the app exits or restarts: rename the live install to
   `previous`, rename `staged-<version>` into place, then delete the staging
   directory.
5. Keep one previous version on disk until the new version has started once.
   If the new install fails to start, restore `previous` (this extends
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
| Range requests (ideally) | Resumable downloads for 400 MB packages. |
| No authentication | Packages are public downloads. |

CORS is not required. The client is the desktop app, not a browser.

## Enabling updates

There is no production manifest URL yet. The updater stays off and sends
nothing until a host is selected and the manifest URL is configured in the
app (setting or environment variable; chosen at implementation time). Until
then, users update by downloading the latest release the way they do today.
