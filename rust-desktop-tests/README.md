# Desktop regression contracts

This standard-library Rust harness checks the v0.15 desktop fixes through the
real Python application. Rust owns expected results, filesystem comparisons,
strict observation decoding and child deadlines. `probe.py` operates PySide6 and
pandas and returns typed observations. It never runs a model or reads real EMR.

Use the project's Python 3.14 environment and the pinned Rust toolchain. Run
from this directory. Place all scratch, caches and Cargo output outside the
checkout under the project's home-directory workspace. For example:

```bash
export PYTHON="$(cd .. && pwd)/.venv/bin/python"
export CARGO_TARGET_DIR="$HOME/Projects/medical/mover/_work/appimage-fix-first/cargo-target"
export MOVER_CONTRACT_SCRATCH="$HOME/Projects/medical/mover/_work/appimage-fix-first/contracts"
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked -- --test-threads=1
```

Each scenario keeps stdout, stderr and any screenshots in a unique directory.
The runner applies a 45-second deadline and a 1 MiB limit per output stream.
It does not delete previous results. The Qt probes run offscreen in fresh
processes with isolated settings, data and cache paths. Use the machine's
resource limits when running the complete gate.

`MOVER_SOURCE_ROOT` optionally selects a different source checkout. `PYTHON`
still selects the interpreter, so the preserved original source can use the
current Python environment. The bridge calls existing production entry points
on both versions. A probe crash is a failure, never evidence of a regression
being detected correctly.

The source run reports the frozen-bundle check as ignored. Run that check
explicitly against the actual Linux onedir bundle after building:

```bash
MOVER_FROZEN_BUNDLE=/path/to/dist/MOVER-SIS-Monitor \
  cargo test --locked --test frozen_bundle -- --ignored
```

Missing artifacts fail this explicit gate. It uses `readelf --version-info`
to inspect required GLIBC versions and checks that both chevron assets are
packaged. The maximum supported requirement for this candidate is GLIBC 2.39.
Named ABI requirements need explicit handling; they cannot be silently ignored.
The `GLIBC_ABI_DT_RELR` requirement is handled as GLIBC 2.36, following the
[glibc change introducing it](https://sourceware.org/pipermail/glibc-cvs/2022q2/078142.html).
Other named tags, including `GLIBC_ABI_GNU2_TLS`, are rejected explicitly for
this baseline. The gate inspects every ELF before reporting incompatible tags
and preserves a complete requirements-to-file audit. The source gate includes
checks that exported version definitions do not count as imported requirements
and that named tags cannot pass through the numeric parser unnoticed.

Accessibility announcement interception proves that the app invokes Qt's API.
It does not prove AT-SPI delivery or screen-reader speech. Offscreen focus
recovery checks the window's retained focus widget, since that platform does
not reactivate a native window after a warning closes. Geometry checks measure
widget clipping at default and double scaling; synthetic screen geometry does
not establish real window-manager behavior. Final native focus, screen fit,
chevron appearance and accessibility delivery need a visible AppImage tour.
The startup scenario calls the real desktop entry point with its event loop
intercepted for observation. It checks logical screen fit, a usable evidence
board height and unclipped header actions at double scaling.

Legacy processed caches without recorded provenance cannot establish their
source identity. That compatibility path is not a successful provenance test.
