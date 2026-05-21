#!/usr/bin/env python3
"""Parse real BDSP EDF files with mne and emit measured bronze EEG events.

For every ``.edf`` in the download manifest this reads the real signal in
``window-seconds`` chunks and computes *measured* features per chunk:

    - channel_count       (real, from the EDF header)
    - sampling_rate_hz     (real, from the EDF header)
    - mean_amplitude_uv    (mean abs amplitude across channels)
    - flat_channel_frac    (fraction of channels with near-zero variance)
    - clipping_frac        (fraction of samples at the rail)
    - signal_quality_score (1 - flat - clipping, clamped)  ← now MEASURED

Output: partitioned JSONL under ``--bronze`` (same layout as BronzeWriter),
so the existing silver/gold batch runs unchanged on real data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _quality(window: np.ndarray) -> tuple[float, float, float, float]:
    """Return (mean_amplitude_uv, flat_frac, clipping_frac, signal_quality)."""
    if window.size == 0:
        return 0.0, 1.0, 0.0, 0.0
    # window shape: (n_channels, n_samples), volts -> microvolts
    uv = window * 1e6
    mean_amp = float(np.mean(np.abs(uv)))
    per_ch_std = np.std(uv, axis=1)
    flat_frac = float(np.mean(per_ch_std < 0.5))           # ~flat channels
    rail = np.max(np.abs(uv)) if uv.size else 0.0
    clipping_frac = float(np.mean(np.abs(uv) >= rail * 0.999)) if rail > 0 else 0.0
    quality = max(0.0, min(1.0, 1.0 - flat_frac - 0.5 * clipping_frac))
    return mean_amp, flat_frac, clipping_frac, quality


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/raw/eeg/_download_manifest.json"))
    parser.add_argument("--bronze", type=Path, default=Path("data/lake/bronze_real"))
    parser.add_argument("--window-seconds", type=float, default=10.0)
    parser.add_argument("--max-windows-per-file", type=int, default=600,
                        help="Cap windows/file (≈1h at 10s) so the parse stays tractable")
    args = parser.parse_args()

    import mne
    mne.set_log_level("ERROR")

    manifest = json.loads(args.manifest.read_text())
    records = manifest["records"]
    print(f"[edf2bronze] {len(records)} EDF files to parse", flush=True)

    eeg_dir = args.bronze / "eeg"
    eeg_dir.mkdir(parents=True, exist_ok=True)

    total_events = 0
    base_time = datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)

    for fi, rec in enumerate(records):
        path = rec["local_path"]
        site = rec["SiteID"]
        subject = rec["BidsFolder"]
        session = rec["SessionID"]
        patient_id = subject.replace("sub-", "")
        try:
            raw = mne.io.read_raw_edf(path, preload=False, verbose=False)
        except Exception as e:
            print(f"[edf2bronze] FAIL read {path}: {str(e)[:100]}", flush=True)
            continue

        sfreq = float(raw.info["sfreq"])
        n_channels = len(raw.ch_names)
        n_times = raw.n_times
        win_samples = int(args.window_seconds * sfreq)
        if win_samples <= 0:
            continue
        n_windows = min(args.max_windows_per_file, n_times // win_samples)

        # bronze partition: eeg/site=<site>/date=<YYYY-MM-DD>/<file>.jsonl
        date_part = (base_time + timedelta(minutes=fi)).strftime("%Y-%m-%d")
        part_dir = eeg_dir / f"site={site}" / f"date={date_part}"
        part_dir.mkdir(parents=True, exist_ok=True)
        out_path = part_dir / f"eeg_real_{patient_id}_{session}.jsonl"

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
                event_time = (base_time + timedelta(seconds=w * args.window_seconds)
                              + timedelta(minutes=fi)).isoformat()
                evt = {
                    "patient_id":          patient_id,
                    "session_id":          str(session),
                    "event_time":          event_time,
                    "site_id":             site,
                    "channel_count":       n_channels,
                    "sampling_rate_hz":    round(sfreq, 2),
                    "window_seconds":      args.window_seconds,
                    "source_uri":          rec["s3_key"],
                    "mean_amplitude_uv":   round(mean_amp, 3),
                    "flat_channel_frac":   round(flat_frac, 4),
                    "clipping_frac":       round(clip_frac, 4),
                    "signal_quality_score": round(quality, 4),
                }
                out.write(json.dumps(evt) + "\n")
                file_events += 1
        total_events += file_events
        print(f"[edf2bronze] {fi+1}/{len(records)}  {site}/{patient_id}/ses-{session}  "
              f"sfreq={sfreq:.0f}Hz ch={n_channels} windows={file_events}", flush=True)

    summary = {
        "bronze_real_events": total_events,
        "files_parsed":       len(records),
        "bronze_path":        str(args.bronze),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
