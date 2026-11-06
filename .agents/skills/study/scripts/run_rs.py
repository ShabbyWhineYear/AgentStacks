#!/usr/bin/env python3
"""Cargo-backed Rust Script Runner (`run_rs.py`).

Executes any domain Rust script (`wiki/**/scripts/**/*.rs`) via `cargo run --quiet --bin <stem> -- [args...]`
using the unified root `Cargo.toml` workspace (giving full access to external crates like
`serde`, `serde_json`, `hound`, `rustfft`, etc. with incremental compilation).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 run_rs.py <path/to/script.rs_or_bin_name> [args...]", file=sys.stderr)
        return 2

    target_arg = sys.argv[1]
    script_stem = Path(target_arg).stem

    repo_root = Path(__file__).resolve().parents[4]
    cargo_bin = Path.home() / ".cargo" / "bin" / "cargo"
    cargo_cmd = str(cargo_bin) if cargo_bin.exists() else "cargo"

    env = os.environ.copy()
    macos_sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
    if macos_sdk.exists():
        env["SDKROOT"] = str(macos_sdk.resolve())

    res = subprocess.run(
        [cargo_cmd, "run", "--quiet", "--bin", script_stem, "--", *sys.argv[2:]],
        cwd=str(repo_root),
        env=env,
    )
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
