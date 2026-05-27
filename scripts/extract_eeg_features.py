#!/usr/bin/env python3
"""Extract deeper per-window EEG features from a local EDF file.

Use this script to test the new feature extractor against real EDF
data on the developer station before the batch path picks it up on
EKS. Reads an EDF via MNE, slides a window over each channel, runs
``brainwatch.processing.eeg_features.extract_window_features``, and
emits the result as JSONL (one row per (channel-window, window)).

Usage::

    python scripts/extract_eeg_features.py \\
        --edf path/to/recording.edf \\
        --window-seconds 4.0 \\
        --output features.jsonl

The output JSONL schema mirrors the gold zone's planned
``patient_features_rich`` table:

    patient_id           text   (parsed from EDF filename)
    session_id           text
    window_start_seconds float  (offset from EDF start)
    window_end_seconds   float
    line_length          float
    spectral_entropy     float
    hjorth_activity      float
    hjorth_mobility      float
    hjorth_complexity    float
    power_{delta..gamma} float  (5 columns)
    rel_power_{delta..gamma} float  (5 columns)

If MNE is not installed, the script raises with a clear install hint
instead of failing on an opaque ImportError; install with
``pip install 'mne==1.7.1' 'numpy<2' 'scipy==1.13.1'`` (the same pin
the bronze-streamer container uses).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brainwatch.processing.eeg_features import extract_window_features  # noqa: E402


def _read_edf(edf_path: str):
    try:
        import mne  # type: ignore
    except ImportError as e:
        raise SystemExit(
            "MNE is not installed in this environment. Install it with:\n"
            "    pip install 'mne==1.7.1' 'numpy<2' 'scipy==1.13.1'\n"
            f"Original error: {e}"
        )
    mne.set_log_level("ERROR")
    raw = mne.io.read_raw_edf(edf_path, preload=True, verbose=False)
    data = raw.get_data()                            # (n_channels, n_samples)
    fs = float(raw.info["sfreq"])
    return data, fs


def _patient_session_from_filename(edf_path: str) -> tuple[str, str]:
    name = os.path.basename(edf_path).rsplit(".", 1)[0]
    parts = name.split("_")
    pid = parts[0] if parts else name
    sid = next((p.split("-", 1)[1] for p in parts if p.startswith("ses-")),
               parts[1] if len(parts) > 1 else "0")
    return pid, sid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--edf", required=True, help="path to an EDF file")
    ap.add_argument("--window-seconds", type=float, default=4.0,
                     help="length of each analysis window (default 4.0 s)")
    ap.add_argument("--step-seconds", type=float, default=None,
                     help="step between window starts (default: equal to "
                          "window-seconds = non-overlapping)")
    ap.add_argument("--output", required=True, help="JSONL output path")
    ap.add_argument("--max-windows", type=int, default=None,
                     help="optional cap so smoke tests stay fast")
    args = ap.parse_args()

    data, fs = _read_edf(args.edf)
    pid, sid = _patient_session_from_filename(args.edf)

    win_n = int(round(args.window_seconds * fs))
    step_n = int(round((args.step_seconds or args.window_seconds) * fs))
    if win_n <= 1 or step_n < 1:
        raise SystemExit(f"window/step too small for fs={fs}; "
                          f"win_n={win_n}, step_n={step_n}")

    n_samples = data.shape[1]
    n_chan = data.shape[0]
    n_windows = max(0, (n_samples - win_n) // step_n + 1)
    if args.max_windows is not None:
        n_windows = min(n_windows, args.max_windows)
    print(f"EDF: {args.edf}", file=sys.stderr)
    print(f"  channels={n_chan}, fs={fs:.1f} Hz, "
          f"samples={n_samples}, duration={n_samples / fs:.1f} s",
          file=sys.stderr)
    print(f"  windows={n_windows} (win={args.window_seconds}s, "
          f"step={(args.step_seconds or args.window_seconds):.1f}s)",
          file=sys.stderr)

    n_written = 0
    with open(args.output, "w") as out:
        for w in range(n_windows):
            s = w * step_n
            e = s + win_n
            if e > n_samples:
                break
            window = data[:, s:e]
            feats = extract_window_features(window, fs)
            row = {
                "patient_id": pid,
                "session_id": sid,
                "window_start_seconds": float(s / fs),
                "window_end_seconds": float(e / fs),
                **feats,
            }
            out.write(json.dumps(row) + "\n")
            n_written += 1
    print(f"wrote {n_written} windows to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
