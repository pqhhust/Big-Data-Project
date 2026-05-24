#!/usr/bin/env python3
"""Long-running 'data stream simulator' — produces bronze JSONL continuously
from raw EDFs stored on S3, as if a hospital were sending new EEG recordings.

Why this exists
---------------
The one-shot ``edf_to_bronze.py`` is fine for an initial backfill but the
batch CronJob (``spark-batch-hdfs``) sees the same bronze every run unless
something is *adding* new files. This streamer is the "something":

    s3://<bucket>/<prefix>/...edf     (raw, immutable, stored once)
                │
                ▼ this script — one EDF every ``SLEEP_BETWEEN_EDF`` seconds
                ▼   download → mne parse → measured features → JSONL
                ▼
    /data/lake/bronze_real/eeg/site=<X>/date=<Y>/<patient>_<session>.jsonl
                │
                ▼ (every 5 min — existing hdfs-bronze-loader CronJob)
                ▼
    HDFS /lake/bronze
                │
                ▼ (every 5 min — existing spark-batch-hdfs CronJob)
                ▼
    HDFS /lake/silver, /lake/gold  ← counts grow over time

Restart-safety
--------------
State (which EDFs have been processed) is written to
``/data/lake/_state/bronze_streamer.json`` on every successful parse, so the
pod can restart and resume from where it left off.

Environment
-----------
    RAW_EDF_BUCKET       (required)  S3 bucket holding the raw EDFs
    RAW_EDF_PREFIX       (default "raw_edf/")
    BRONZE_DIR           (default "/data/lake/bronze_real")
    SLEEP_BETWEEN_EDF    (default "20")  seconds between parses
    WINDOW_SECONDS       (default "10.0")
    MAX_WINDOWS_PER_FILE (default "600")
    AWS_ACCESS_KEY_ID    (from Secret)
    AWS_SECRET_ACCESS_KEY(from Secret)
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import numpy as np


def _quality(window: np.ndarray) -> tuple[float, float, float, float]:
    """Matches scripts/edf_to_bronze.py:_quality. Returns measured features."""
    if window.size == 0:
        return 0.0, 1.0, 0.0, 0.0
    uv = window * 1e6
    mean_amp = float(np.mean(np.abs(uv)))
    per_ch_std = np.std(uv, axis=1)
    flat_frac = float(np.mean(per_ch_std < 0.5))
    rail = np.max(np.abs(uv)) if uv.size else 0.0
    clipping_frac = float(np.mean(np.abs(uv) >= rail * 0.999)) if rail > 0 else 0.0
    quality = max(0.0, min(1.0, 1.0 - flat_frac - 0.5 * clipping_frac))
    return mean_amp, flat_frac, clipping_frac, quality


def _site_from_key(key: str) -> str:
    parts = key.split("/")
    for p in parts:
        if p.startswith(("S00", "I00")):
            return p
    return "UNKNOWN"


def _patient_session_from_key(key: str) -> tuple[str, str]:
    """raw_edf/<site>/sub-<patient>/ses-<session>/eeg/<file>.edf → (<patient>, <session>).

    Pick the FIRST `sub-<dirname>` and `ses-<dirname>` so we never match the
    filename `sub-<patient>_ses-<n>_task-EEG_eeg.edf` and pull the suffix in.
    """
    parts = key.split("/")
    patient = "unknown"
    session = "1"
    for p in parts[:-1]:                            # skip the filename
        if p.startswith("sub-") and patient == "unknown":
            patient = p[len("sub-"):]
        elif p.startswith("ses-") and session == "1":
            session = p[len("ses-"):]
    if patient == "unknown":
        patient = Path(key).stem
    return patient, session


def _list_edf_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".edf"):
                keys.append(obj["Key"])
    keys.sort()
    return keys


_stopped = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stopped
    _stopped = True


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    bucket = os.environ["RAW_EDF_BUCKET"]
    prefix = os.environ.get("RAW_EDF_PREFIX", "raw_edf/")
    bronze_dir = Path(os.environ.get("BRONZE_DIR", "/data/lake/bronze_real"))
    state_path = bronze_dir.parent / "_state" / "bronze_streamer.json"
    sleep_s = int(os.environ.get("SLEEP_BETWEEN_EDF", "20"))
    window_s = float(os.environ.get("WINDOW_SECONDS", "10.0"))
    max_windows = int(os.environ.get("MAX_WINDOWS_PER_FILE", "600"))
    # Simulate a real hospital ingest: every incoming EDF is *archived* into
    # bronze (raw signal) alongside the derived JSONL features. Bronze size
    # therefore grows with each EDF the streamer pulls. Capped at
    # ARCHIVE_RAW_CAP_GIB so HDFS (RF=2, 40 GiB total) doesn't fill.
    archive_raw = os.environ.get("ARCHIVE_RAW_EDF", "true").lower() == "true"
    archive_cap_bytes = int(float(os.environ.get("ARCHIVE_RAW_CAP_GIB", "4")) * 1024 ** 3)
    edf_archive_dir = bronze_dir / "edf"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        try:
            processed = set(json.loads(state_path.read_text()))
        except json.JSONDecodeError:
            processed = set()
    else:
        processed = set()

    import mne
    mne.set_log_level("ERROR")
    s3 = boto3.client("s3")

    base_time = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)
    print(f"[stream] bucket={bucket}  prefix={prefix}  bronze_dir={bronze_dir}", flush=True)
    print(f"[stream] resume from state file: {len(processed)} EDFs already processed", flush=True)

    fi = len(processed)
    while not _stopped:
        keys = _list_edf_keys(s3, bucket, prefix)
        new_keys = [k for k in keys if k not in processed]
        if not new_keys:
            print(f"[stream] idle — no new EDFs in s3://{bucket}/{prefix} "
                  f"(processed={len(processed)}); sleeping 60 s", flush=True)
            time.sleep(60)
            continue

        for key in new_keys:
            if _stopped:
                break
            site = _site_from_key(key)
            patient_id, session = _patient_session_from_key(key)

            local = f"/tmp/{Path(key).name}"
            try:
                s3.download_file(bucket, key, local)
            except Exception as e:
                print(f"[stream] download FAIL {key}: {str(e)[:120]}", flush=True)
                continue

            try:
                raw = mne.io.read_raw_edf(local, preload=False, verbose=False)
            except Exception as e:
                print(f"[stream] mne FAIL {key}: {str(e)[:120]}", flush=True)
                try: os.unlink(local)
                except OSError: pass
                processed.add(key)  # don't retry forever on bad EDF
                continue

            sfreq = float(raw.info["sfreq"])
            n_channels = len(raw.ch_names)
            n_times = raw.n_times
            win_samples = int(window_s * sfreq)
            if win_samples <= 0:
                processed.add(key)
                try: os.unlink(local)
                except OSError: pass
                continue
            n_windows = min(max_windows, n_times // win_samples)

            date_part = (base_time + timedelta(minutes=fi)).strftime("%Y-%m-%d")
            part_dir = bronze_dir / "eeg" / f"site={site}" / f"date={date_part}"
            part_dir.mkdir(parents=True, exist_ok=True)
            out_path = part_dir / f"eeg_stream_{patient_id}_{session}.jsonl"

            file_events = 0
            with out_path.open("w") as out:
                for w in range(n_windows):
                    start = w * win_samples
                    stop = start + win_samples
                    try:
                        data = raw.get_data(start=start, stop=stop)
                    except Exception:
                        break
                    mean_amp, flat_frac, clip_frac, quality = _quality(data)
                    event_time = (base_time + timedelta(seconds=w * window_s)
                                  + timedelta(minutes=fi)).isoformat()
                    evt = {
                        "patient_id":          patient_id,
                        "session_id":          str(session),
                        "event_time":          event_time,
                        "site_id":             site,
                        "channel_count":       n_channels,
                        "sampling_rate_hz":    round(sfreq, 2),
                        "window_seconds":      window_s,
                        "source_uri":          f"s3://{bucket}/{key}",
                        "mean_amplitude_uv":   round(mean_amp, 3),
                        "flat_channel_frac":   round(flat_frac, 4),
                        "clipping_frac":       round(clip_frac, 4),
                        "signal_quality_score": round(quality, 4),
                    }
                    out.write(json.dumps(evt) + "\n")
                    file_events += 1

            # ── Archive the raw EDF into bronze (real-hospital simulation) ──
            archived_note = ""
            if archive_raw:
                # Check current archive size; stop archiving past the cap.
                current_archive_bytes = 0
                if edf_archive_dir.exists():
                    for f in edf_archive_dir.rglob("*.edf"):
                        try: current_archive_bytes += f.stat().st_size
                        except OSError: pass
                if current_archive_bytes < archive_cap_bytes:
                    dest = edf_archive_dir / f"site={site}" / f"date={date_part}" / Path(key).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        import shutil
                        shutil.copy2(local, dest)
                        archived_note = f"  +archive={Path(key).name} ({dest.stat().st_size // 1024} KB)"
                    except OSError as e:
                        archived_note = f"  archive_FAIL: {str(e)[:60]}"
                else:
                    archived_note = f"  archive_CAP_REACHED ({current_archive_bytes // (1024**2)} MiB)"

            processed.add(key)
            state_path.write_text(json.dumps(sorted(processed)))
            try: os.unlink(local)
            except OSError: pass

            print(f"[stream] +{site}/{patient_id} ses-{session}  windows={file_events}  "
                  f"(processed={len(processed)}/{len(keys)}){archived_note}", flush=True)
            fi += 1

            if _stopped:
                break
            time.sleep(sleep_s)

    print(f"[stream] stopped — total processed: {len(processed)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
