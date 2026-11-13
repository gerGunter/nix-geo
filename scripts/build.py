#!/usr/bin/env python3
"""Build NixVPN geosite.dat from upstream GeoSite binary releases."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from geosite_merge import combine

ROSCOM_URL = "https://raw.githubusercontent.com/hydraponique/roscomvpn-geosite/master/release/geosite.dat"
ROSCOM_SHA = ROSCOM_URL + ".sha256"
V2FLY_URL = "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat"
V2FLY_SHA = V2FLY_URL + ".sha256sum"
MAX_DOWNLOAD = 64 * 1024 * 1024


def get_url(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "nix-geo-builder/1.0", "Accept": "application/octet-stream, */*"})
    last_exc = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=50) as response:
                data = response.read(MAX_DOWNLOAD + 1)
                if len(data) > MAX_DOWNLOAD:
                    raise ValueError(f"Download too large: {url}")
                if not data:
                    raise ValueError(f"Empty download: {url}")
                return data
        except (HTTPError, URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt != 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Cannot download {url}: {last_exc}")


def verify_digest(data: bytes, checksum_text: bytes, name: str) -> str:
    match = re.search(rb"\b[a-fA-F0-9]{64}\b", checksum_text)
    if not match:
        raise ValueError(f"Cannot parse SHA256 checksum for {name}")
    expected = match.group().decode("ascii").lower()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA256 mismatch for {name}: expected {expected}, got {actual}")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roscom-file", type=Path, help="Use a local source file (offline mode)")
    parser.add_argument("--v2fly-file", type=Path, help="Use a local source file (offline mode)")
    parser.add_argument("--output", type=Path, default=Path("dist/geosite.dat"))
    parser.add_argument("--manifest", type=Path, default=Path("dist/manifest.json"))
    args = parser.parse_args()
    if bool(args.roscom_file) != bool(args.v2fly_file):
        parser.error("Specify BOTH --roscom-file and --v2fly-file for offline mode")

    if args.roscom_file:
        roscom = args.roscom_file.read_bytes()
        v2fly = args.v2fly_file.read_bytes()
        roscom_digest = hashlib.sha256(roscom).hexdigest()
        v2fly_digest = hashlib.sha256(v2fly).hexdigest()
        source = "local-files"
    else:
        roscom = get_url(ROSCOM_URL)
        roscom_digest = verify_digest(roscom, get_url(ROSCOM_SHA), "RoscomVPN")
        v2fly = get_url(V2FLY_URL)
        v2fly_digest = verify_digest(v2fly, get_url(V2FLY_SHA), "V2Fly")
        source = "upstream-downloads"

    combined, stats = combine(roscom, v2fly)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(combined)
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "roscom": {"url": ROSCOM_URL, "sha256": roscom_digest, "bytes": len(roscom)},
        "v2fly": {"url": V2FLY_URL, "sha256": v2fly_digest, "bytes": len(v2fly)},
        "output": {"filename": args.output.name, "sha256": hashlib.sha256(combined).hexdigest(), "bytes": len(combined)},
        **stats,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
