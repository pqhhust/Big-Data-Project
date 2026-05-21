#!/usr/bin/env python3
"""Download real BDSP (Harvard/MGH) EEG EDF via the credentialed access point.

Metadata-driven + breadth-first: reads the per-site
``EEG/eeg-metadata/<SITE>_eeg_metadata_*.csv`` tables, selects the SHORTEST
recordings first (more subjects per gigabyte), round-robins across sites, and
downloads real ``.edf`` files until the cumulative size reaches ``--target-gb``.

Writes per-site metadata CSVs (real ``DurationInSeconds`` from BDSP) + a
download manifest.

Credentials: 2-line ``rootkey.csv`` via ``--credentials`` / ``$BDSP_CREDENTIALS``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ACCESS_POINT = "arn:aws:s3:us-east-1:184438910517:accesspoint/bdsp-credentialed-access-point"
BIDS_ROOT = "EEG/bids"
META_PREFIX = "EEG/eeg-metadata/"
DEFAULT_SITES = ["S0001", "S0002", "I0002", "I0003", "I0009"]


def _load_creds(path: str) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
    return {"aws_access_key_id": row["Access key ID"].strip(),
            "aws_secret_access_key": row["Secret access key"].strip()}


def _find_site_meta_keys(s3) -> dict[str, str]:
    """site -> metadata CSV key."""
    out: dict[str, str] = {}
    resp = s3.list_objects_v2(Bucket=ACCESS_POINT, Prefix=META_PREFIX, MaxKeys=100)
    for o in resp.get("Contents", []):
        k = o["Key"]
        if k.endswith(".csv"):
            site = k.split("/")[-1].split("_")[0]
            out[site] = k
    return out


def _list_session_edf(s3, site: str, bids: str, session: str):
    """Return (key, size) of the first *_eeg.edf in a session, or None."""
    prefix = f"{BIDS_ROOT}/{site}/{bids}/ses-{session}/eeg/"
    resp = s3.list_objects_v2(Bucket=ACCESS_POINT, Prefix=prefix, MaxKeys=50)
    edfs = [(o["Key"], o["Size"]) for o in resp.get("Contents", []) if o["Key"].endswith("_eeg.edf")]
    if not edfs:
        return None
    # smallest edf in the session
    return min(edfs, key=lambda t: t[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", default=os.getenv("BDSP_CREDENTIALS",
                        "/mnt/disk1/aiotlab/pqhung/courseworks/credentials/rootkey.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/raw/eeg"))
    parser.add_argument("--meta-out", type=Path, default=Path("data/raw/metadata"))
    parser.add_argument("--target-gb", type=float, default=8.5)
    parser.add_argument("--sites", nargs="*", default=DEFAULT_SITES)
    parser.add_argument("--min-duration", type=float, default=120.0,  # 2 min
                        help="Min recording duration (s) to consider")
    parser.add_argument("--max-duration", type=float, default=1800.0,  # 30 min
                        help="Max recording duration (s) — keeps files small for breadth")
    parser.add_argument("--max-file-mb", type=float, default=400.0)
    args = parser.parse_args()

    import boto3
    from botocore.config import Config

    creds = _load_creds(args.credentials)
    s3 = boto3.client("s3", region_name="us-east-1",
                      config=Config(retries={"max_attempts": 5, "mode": "standard"}), **creds)

    args.out.mkdir(parents=True, exist_ok=True)
    args.meta_out.mkdir(parents=True, exist_ok=True)
    target_bytes = int(args.target_gb * 1024 ** 3)
    max_file = int(args.max_file_mb * 1024 ** 2)

    # 1. Locate + download per-site metadata CSVs
    meta_keys = _find_site_meta_keys(s3)
    print(f"[edf] metadata CSVs: {meta_keys}", flush=True)

    # 2. Parse candidates per site, filtered + sorted by duration ascending
    candidates: dict[str, list[dict]] = {}
    for site in args.sites:
        key = meta_keys.get(site)
        if not key:
            print(f"[edf] {site}: no metadata CSV", flush=True)
            candidates[site] = []
            continue
        local_csv = args.meta_out / key.split("/")[-1]
        if not local_csv.exists():
            s3.download_file(ACCESS_POINT, key, str(local_csv))
        rows = []
        with local_csv.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    dur = float(r.get("DurationInSeconds") or 0)
                except ValueError:
                    continue
                bids = r.get("BidsFolder") or ""
                ses = r.get("SessionID") or ""
                if not (bids and ses) or not (args.min_duration <= dur <= args.max_duration):
                    continue
                rows.append({"site": site, "bids": bids, "session": ses, "duration": dur,
                             "service": r.get("ServiceName", ""), "folder": r.get("EEGFolder", "")})
        rows.sort(key=lambda x: x["duration"])  # shortest first → breadth
        candidates[site] = rows
        print(f"[edf] {site}: {len(rows)} candidate recordings in [{args.min_duration},{args.max_duration}]s", flush=True)

    # 3. Round-robin download until target
    per_site_meta: dict[str, list[dict]] = {s: [] for s in args.sites}
    manifest_records: list[dict] = []
    downloaded_bytes = 0
    files = 0
    iters = {s: iter(candidates[s]) for s in args.sites}
    exhausted: set[str] = set()
    seen_subjects: set[str] = set()

    while downloaded_bytes < target_bytes and len(exhausted) < len(args.sites):
        for site in args.sites:
            if downloaded_bytes >= target_bytes:
                break
            if site in exhausted:
                continue
            # advance to a fresh subject for breadth
            cand = None
            while True:
                try:
                    c = next(iters[site])
                except StopIteration:
                    exhausted.add(site)
                    break
                if c["bids"] in seen_subjects:
                    continue
                cand = c
                break
            if cand is None:
                continue
            found = _list_session_edf(s3, site, cand["bids"], cand["session"])
            if not found:
                continue
            edf_key, size = found
            if size > max_file:
                continue
            seen_subjects.add(cand["bids"])
            fname = edf_key.split("/")[-1]
            local_dir = args.out / site / cand["bids"] / f"ses-{cand['session']}"
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / fname
            if not (local_path.exists() and local_path.stat().st_size == size):
                try:
                    s3.download_file(ACCESS_POINT, edf_key, str(local_path))
                except Exception as e:
                    print(f"[edf] FAIL {edf_key}: {str(e)[:90]}", flush=True)
                    continue
            downloaded_bytes += size
            files += 1
            rec = {
                "SiteID": site, "BidsFolder": cand["bids"], "SessionID": cand["session"],
                "Task": fname.split("task-")[-1].split("_eeg.edf")[0] if "task-" in fname else "EEG",
                "DurationInSeconds": cand["duration"], "ServiceName": cand["service"],
                "EEGFolder": cand["folder"], "s3_key": edf_key,
                "local_path": str(local_path), "size_bytes": size,
            }
            per_site_meta[site].append(rec)
            manifest_records.append(rec)
            if files % 10 == 0 or downloaded_bytes >= target_bytes:
                print(f"[edf] {downloaded_bytes/1024**3:5.2f} GiB  files={files:4d}  "
                      f"subjects={len(seen_subjects)}  last={site}/{cand['bids']} ({size/1024**2:.0f} MiB, {cand['duration']:.0f}s)", flush=True)

    # 4. Per-site metadata CSVs + manifest
    fields = ["SiteID", "BidsFolder", "SessionID", "Task", "DurationInSeconds",
              "ServiceName", "EEGFolder", "s3_key", "local_path", "size_bytes"]
    for site, recs in per_site_meta.items():
        if not recs:
            continue
        with (args.out / f"{site}_meta.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(recs)
        print(f"[edf] wrote {args.out / f'{site}_meta.csv'} ({len(recs)} rows)")

    manifest = {
        "source": "BDSP credentialed access point (Harvard/MGH)",
        "access_point": ACCESS_POINT,
        "total_files": files,
        "total_subjects": len(seen_subjects),
        "total_bytes": downloaded_bytes,
        "total_gib": round(downloaded_bytes / 1024 ** 3, 3),
        "duration_window_s": [args.min_duration, args.max_duration],
        "sites": {s: len(r) for s, r in per_site_meta.items()},
        "records": manifest_records,
    }
    (args.out / "_download_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "records"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
