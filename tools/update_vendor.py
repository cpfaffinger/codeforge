"""Refresh the vendored browser decoder (zxing-wasm) to the latest release within its major version.

    python tools/update_vendor.py            # latest 3.x
    python tools/update_vendor.py --major 4  # move to another major deliberately

The files are fetched from jsDelivr (the npm registry mirror) into app/static/vendor/ together with
the license; the version is recorded in app/static/vendor/VERSIONS. Run the browser tests afterwards
(pytest tests/e2e) and commit.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request

VENDOR = os.path.join(os.path.dirname(__file__), "..", "app", "static", "vendor")
PACKAGES = {
    "zxing-wasm": {
        "major": 3,
        "files": {
            "dist/iife/reader/index.js": "zxing-reader.iife.js",
            "dist/reader/zxing_reader.wasm": "zxing_reader.wasm",
            "LICENSE": "zxing-wasm.LICENSE",
        },
    },
}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def latest_version(package: str, major: int) -> str:
    meta = json.loads(fetch(f"https://data.jsdelivr.com/v1/package/npm/{package}"))
    versions = [v for v in meta["versions"] if v.split(".")[0] == str(major) and "-" not in v]
    if not versions:
        raise SystemExit(f"no release of {package} with major {major}")
    return versions[0]  # jsdelivr lists newest first


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--major", type=int, help="override the major version of zxing-wasm")
    args = ap.parse_args()
    versions = {}
    for package, spec in PACKAGES.items():
        version = latest_version(package, args.major or spec["major"])
        for src, dst in spec["files"].items():
            data = fetch(f"https://cdn.jsdelivr.net/npm/{package}@{version}/{src}")
            with open(os.path.join(VENDOR, dst), "wb") as fh:
                fh.write(data)
            print(f"{package}@{version}: {src} -> {dst} ({len(data) // 1024} kB)")
        versions[package] = version
    with open(os.path.join(VENDOR, "VERSIONS"), "w", encoding="utf-8", newline="\n") as fh:
        for package, version in versions.items():
            fh.write(f"{package} {version}\n")
        fh.write("zxing-library (ZXing JS, legacy fallback) 0.21.x - unmaintained upstream, kept as is\n")


if __name__ == "__main__":
    main()
