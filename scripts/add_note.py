#!/usr/bin/env python3
"""Append a timestamped observation to the project notes log + sync to S3.

Lets you jot findings while inspecting the data in the web explorer, e.g.::

    python scripts/add_note.py --layer silver --text "dedup dropped 565 dup EEG windows" --author quang
    python scripts/add_note.py --layer gold --tag anomaly --text "I0009 has the highest critical rate"

Notes land in ``dashboard/public/notes.json`` (newest-first) and, if AWS creds
are present, are uploaded to the dashboard S3 bucket so the Grafana "Notes"
panel shows them live.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_NOTES = Path("dashboard/public/notes.json")


def load_notes(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return []
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="The observation")
    parser.add_argument("--layer", default="general",
                        choices=["general", "bronze", "silver", "gold", "speed", "serving", "deploy", "anomaly"])
    parser.add_argument("--tag", default="")
    parser.add_argument("--author", default=os.environ.get("USER", "team"))
    parser.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    parser.add_argument("--bucket", default="brainwatch-dashboard-923884399064")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    notes = load_notes(args.notes)
    notes.insert(0, {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layer": args.layer,
        "tag": args.tag,
        "author": args.author,
        "note": args.text,
    })
    args.notes.parent.mkdir(parents=True, exist_ok=True)
    args.notes.write_text(json.dumps(notes, indent=2))
    print(f"[note] saved ({len(notes)} total) → {args.notes}")

    if not args.no_upload:
        try:
            import boto3
            boto3.client("s3").put_object(
                Bucket=args.bucket, Key="notes.json",
                Body=json.dumps(notes).encode("utf-8"),
                CacheControl="no-cache,max-age=0", ContentType="application/json",
            )
            print(f"[note] uploaded to s3://{args.bucket}/notes.json")
        except Exception as e:
            print(f"[note] S3 upload skipped: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
