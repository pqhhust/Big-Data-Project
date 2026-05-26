# BrainWatch — Figure Drawing Guide

The report has four `\pqhung{FIG: ...}` placeholders that need to be
replaced with real figures before the final PDF is rendered. This
document is the end-to-end guide: what each figure should contain,
which tool to use, how to export it, where to put the file, and how
to swap the placeholder for `\includegraphics`.

## Quick inventory

| # | Figure | Where it lives in the report | Type | Suggested tool |
|---|---|---|---|---|
| F1 | **Deployed system topology** | `Prototyping.tex` §Architecture overview, label `fig:arch` | Architecture diagram | **draw.io** or **Excalidraw** (export PDF) |
| F2 | **Lambda triad** | `Background.tex` §Lambda and Kappa, end of section | Concept diagram | **TikZ** (cleanest) or **draw.io** |
| F3 | **Real-Time Alerts dashboard** | `Background.tex` §Visualisation, end of section (left half of the side-by-side) | Screenshot | **Grafana** running locally + screenshot |
| F4 | **Architecture Status dashboard** | `Background.tex` §Visualisation, end of section (right half of the side-by-side) | Screenshot | **Grafana** running locally + screenshot |

Three diagrams + two screenshots = **four `\pqhung{FIG: ...}`
placeholders** in total (F3 and F4 share one side-by-side
placeholder).

---

## Tool choices

### For architecture / concept diagrams (F1, F2)

| Tool | Best for | Output | Recommended? |
|---|---|---|---|
| **draw.io / diagrams.net** | Component diagrams, system topology | PDF (vector), PNG, SVG | ✅ for F1 |
| **Excalidraw** | Sketchy, friendly hand-drawn look | PNG, SVG (paste-into-Overleaf-friendly) | OK alternative |
| **TikZ in-LaTeX** | Diagrams with mathy alignment, ships in the source | PDF (vector) | ✅ for F2 if you're comfortable with TikZ |
| **PlantUML / Mermaid** | Sequence / class diagrams | PNG (raster) | Not recommended (raster only) |
| **Figma / Penpot** | Pixel-perfect visual control | PDF, SVG | OK if your team already knows it |

Default recommendation: **draw.io for F1 (topology), TikZ for F2
(triad)**. Both render as PDF vector graphics, which scale without
pixelation.

### For dashboard screenshots (F3, F4)

Use the running Grafana instance — it already exists in the repo. The
six dashboard JSONs in `infra/cloud/grafana-*.json` are importable
into any Grafana 11 instance. Screenshot at 2× DPI (Retina /
high-DPI) so the figure stays sharp when embedded.

---

## Where the figure files go

Put every figure file under `overleaf/.../Figures/` inside the
Overleaf project. **Do not commit them to `Big-Data-Project/` — the
Overleaf project has its own git remote.**

Suggested filenames (so the `\includegraphics` paths are short and
self-explanatory):

```
Figures/arch-topology.pdf          # F1: system topology (draw.io PDF export)
Figures/lambda-triad.pdf           # F2: Lambda triad (TikZ or draw.io)
Figures/dash-realtime-alerts.png   # F3: Real-Time Alerts dashboard screenshot
Figures/dash-architecture-status.png  # F4: Architecture Status dashboard screenshot
```

> The existing `Figures/Ch1`, `Figures/Ch2`, … subdirectories in the
> Overleaf project are leftovers from the Stanford ME310 template
> (car-sharing photos, persona portraits, etc.) and are unrelated to
> BrainWatch. Leave them in place but do not reference them.

---

## F1 — Deployed system topology

**Placeholder location:** `Prototyping.tex` lines 30–52, inside
`\begin{figure}...\end{figure}` block with `\label{fig:arch}`.

**Purpose.** The single diagram a reader looks at to understand "what
runs where" in the deployed system. Every box in the diagram should
correspond to a real pod or S3 bucket; every arrow should correspond
to a real data flow.

**Components to draw (each as one box).** Group by lane:

- **Source lane (top left):**
  - BDSP S3 (external)
  - `s3://brainwatch-capstone-923884399064/raw_edf/` (project bucket
    holding 17 GiB / 1,571 EDFs)
  - Local download station (developer machine running
    `scripts/download_real_edf.py`)
- **Streamer + bronze lane:**
  - `bronze-streamer` Deployment (the long-running pod)
  - `bronze-pvc` (EBS-backed PVC, 20 GiB)
- **Batch lane:**
  - `hdfs-bronze-loader` CronJob (every 5 min)
  - HDFS NameNode StatefulSet (5 GiB EBS)
  - HDFS DataNode StatefulSet × 2 (20 GiB EBS each, RF=2)
  - `spark-batch-hdfs` CronJob (every 5 min, runs `run_batch.py`)
  - HDFS `/lake/silver` and `/lake/gold` (logical paths inside HDFS)
- **Speed lane:**
  - `kafka-producer` Deployment
  - Kafka 3.9 KRaft StatefulSet (1 broker, 4 partitions per topic)
  - `speed-layer` Spark Structured Streaming Deployment
- **Serving lane:**
  - Cassandra 4.1 StatefulSet (RF=1)
  - `cassandra-exporter` Deployment (writes alert roll-ups to S3)
  - `cluster-state-exporter` Deployment (writes cluster-state JSON to S3)
  - `s3://brainwatch-dashboard-923884399064/` (top right; static website)
  - Grafana 11 (NodePort)

**Arrows to draw.**

- **Solid arrows** for live data flow:
  - BDSP S3 → local download → `raw_edf/` bucket
  - `raw_edf/` → `bronze-streamer` → `bronze-pvc`
  - `bronze-pvc` → `hdfs-bronze-loader` → HDFS DataNodes
  - HDFS → `spark-batch-hdfs` → HDFS `silver` + `gold`
  - `kafka-producer` → Kafka → `speed-layer` → Cassandra
  - Cassandra → `cassandra-exporter` → S3 dashboard bucket
  - cluster-state queries → `cluster-state-exporter` → S3 dashboard bucket
  - S3 dashboard bucket → Grafana
- **Dashed arrows** for control / metadata:
  - HDFS NameNode ↔ DataNodes (block reports + heartbeats)
  - `cluster-state-exporter` → HDFS NameNode + Cassandra (read-only
    queries)

**Layout suggestion.** Five vertical lanes left-to-right (Source →
Streamer/Bronze → Batch → Speed → Serving). The "Lambda split" —
batch path versus speed path — should be visually obvious: keep the
batch lane and the speed lane on parallel horizontal tracks that
both originate from the bronze PVC and Kafka respectively, and both
terminate at the serving layer.

**Colour suggestion.** Three colours, no more:

- Compute pods → light blue
- Stateful pods + PVCs → light green
- External services (S3, BDSP) → light grey

**Caption to use.** Already in `Prototyping.tex`:

> Deployed topology of BrainWatch on Amazon EKS. The hybrid storage
> layer uses HDFS for the compute-side lake and Amazon S3 for the
> raw archive and the dashboard serving layer.

**How to draw it in draw.io.**

1. Open `https://app.diagrams.net`, create a new diagram.
2. Use the "AWS17" or "Kubernetes" shape library (Extras → Edit
   Diagram → Shape Libraries) for pod, PVC, S3 icons.
3. Lay out the five lanes as described.
4. File → Export as → PDF. Tick "Crop" so there's no whitespace
   around the diagram.
5. Save as `Figures/arch-topology.pdf` in the Overleaf project.

**LaTeX swap.** In `Prototyping.tex` replace the `\pqhung{FIG: insert
the architecture diagram above; ...}` line with:

```latex
\includegraphics[width=\linewidth]{Figures/arch-topology.pdf}
```

The surrounding `\begin{figure}[t]\centering ... \caption{...}
\label{fig:arch}\end{figure}` block already exists — just swap the
placeholder line for the `\includegraphics` line.

---

## F2 — Lambda triad

**Placeholder location:** `Background.tex` end of §Lambda and Kappa.

**Purpose.** Show the canonical three-layer Lambda decomposition
(batch + speed + serving) at the conceptual level. This is the
textbook view, not the BrainWatch-specific topology — keep it
schematic.

**Components to draw (three boxes).**

- **Batch layer** (top-left box). Inside: "HDFS bronze → Spark batch
  → silver / gold Parquet".
- **Speed layer** (bottom-left box). Inside: "Kafka → Spark
  Structured Streaming → Cassandra alerts".
- **Serving layer** (right box, spanning both). Inside: "Grafana
  over S3 JSON + Cassandra".

**Arrows.**

- A wide arrow from the *immutable raw archive* (a small box at the
  far left labelled "Raw archive (S3)") feeding both batch and speed
  layers.
- An arrow from Batch → Serving labelled **batch view**.
- An arrow from Speed → Serving labelled **speed view**.
- A "merge at query time" annotation at the boundary between the
  two arrows and the Serving box.

**Caption to use.** Suggested:

> Figure: The Lambda architecture as a three-layer triad. The batch
> path recomputes comprehensive views periodically; the speed path
> serves a low-latency view of recent events; the serving layer
> merges both at query time. BrainWatch's realisation is in
> Chapter~\ref{cha:prototyping}.

**How to draw it in TikZ.** Drop this into `Background.tex` in
place of the `\pqhung{FIG: ...}` block:

```latex
\begin{figure}[ht]
\centering
\begin{tikzpicture}[
  box/.style={draw, rounded corners, minimum width=4cm, minimum height=1.3cm,
              align=center, font=\small},
  arr/.style={-Latex, thick},
  every node/.style={font=\small}
]
\node[box, fill=gray!10]  (raw)    {Raw archive\\(Amazon S3)};
\node[box, fill=blue!10, right=2cm of raw, yshift=1.5cm]
                          (batch)  {Batch layer\\HDFS bronze $\to$ Spark\\ $\to$ silver / gold Parquet};
\node[box, fill=orange!10, right=2cm of raw, yshift=-1.5cm]
                          (speed)  {Speed layer\\Kafka $\to$ Spark Structured Streaming\\$\to$ Cassandra alerts};
\node[box, fill=green!10, right=2cm of batch, yshift=-1.5cm,
       minimum height=3cm]
                          (serve)  {Serving layer\\Grafana over S3 JSON\\ + Cassandra};
\draw[arr] (raw) -- (batch.west);
\draw[arr] (raw) -- (speed.west);
\draw[arr] (batch.east) -- node[above]{batch view} (serve.north west);
\draw[arr] (speed.east) -- node[below]{speed view} (serve.south west);
\node[below=1cm of serve, font=\footnotesize, text width=4cm,
       align=center, text=gray]
       {Views merged at query time (eventual consistency).};
\end{tikzpicture}
\caption{Lambda architecture as a three-layer triad.}
\label{fig:lambda-triad}
\end{figure}
```

You will need to add `\usepackage{tikz}` and
`\usetikzlibrary{arrows.meta, positioning}` to `me310report.tex` if
not already present. The `arrows.meta` library provides the `Latex`
arrow style.

**LaTeX swap (if you'd rather use a PDF from draw.io).**

```latex
\begin{figure}[ht]
\centering
\includegraphics[width=0.85\linewidth]{Figures/lambda-triad.pdf}
\caption{Lambda architecture as a three-layer triad.}
\label{fig:lambda-triad}
\end{figure}
```

---

## F3 — Real-Time Alerts dashboard (screenshot)

**Placeholder location:** `Background.tex` end of §Visualisation
(left half of the side-by-side).

**Purpose.** Show the clinician-facing surface — what the dashboard
looks like during a live demonstration.

**Capture this from the Grafana JSON in the code repo:**
`infra/cloud/grafana-dashboard.json` (the main pipeline / alerts
dashboard).

**Panels that should be visible in the screenshot.**

- Per-patient severity stat panel (top row)
- Per-site alert rate time series (middle)
- Recent alerts table (right or below)
- Alert-by-severity bar chart (bottom or right column)

**How to capture (zero-cluster path — use the artefacts already in
the repo).**

1. Start a local Grafana 11:
   ```bash
   docker run -d --name grafana-local -p 3000:3000 grafana/grafana:11
   ```
2. Open `http://localhost:3000`, log in as `admin` / `admin`.
3. Install the Infinity datasource: Configuration → Plugins →
   search "Infinity" → Install.
4. Add a new Infinity datasource pointing at *file URLs* (Configuration
   → Data sources → Add → Infinity → leave URL empty, save).
5. Dashboards → New → Import → Upload JSON file. Pick
   `infra/cloud/grafana-dashboard.json` from the code repo.
6. For the panels to render real data, either:
   - **Point Grafana at S3.** If the S3 dashboard bucket
     (`brainwatch-dashboard-923884399064`) is reachable, the panels
     resolve directly. This is the "real numbers" path.
   - **Use the local export.** Run
     `python scripts/cassandra_to_s3_exporter.py --local-only`
     against your local Cassandra; copy the produced JSONs to
     `./serve/` and start a local web server
     (`python -m http.server -d ./serve 8000`); change each panel's
     URL from the S3 host to `http://host.docker.internal:8000/...`.
7. Wait one refresh cycle (30 s).
8. Take a screenshot at 2× DPI:
   - **macOS:** Cmd-Shift-4 then Spacebar then click the window.
   - **Linux:** `gnome-screenshot -w` (window mode) or
     `grim -g "$(slurp)"` on Wayland.
   - **Browser-native:** use Firefox / Chrome devtools, run
     `document.querySelector('.dashboard-container').scrollIntoView();
     window.print();` then save as PDF.
9. Save as `Figures/dash-realtime-alerts.png` in the Overleaf project.

**Recommended resolution.** 1920 × 1080 minimum; 2560 × 1440 ideal.

**LaTeX swap (for the side-by-side with F4).**

```latex
\begin{figure}[ht]
\centering
\begin{minipage}{0.48\linewidth}
  \centering
  \includegraphics[width=\linewidth]{Figures/dash-realtime-alerts.png}
  \subcaption{Real-Time Alerts dashboard.}
\end{minipage}\hfill
\begin{minipage}{0.48\linewidth}
  \centering
  \includegraphics[width=\linewidth]{Figures/dash-architecture-status.png}
  \subcaption{Architecture Status dashboard.}
\end{minipage}
\caption{The two BrainWatch Grafana dashboards. Both read static JSON
from Amazon S3, so they keep rendering when the EKS cluster is torn
down.}
\label{fig:dashboards}
\end{figure}
```

Add `\usepackage{subcaption}` to `me310report.tex` if not already
present.

---

## F4 — Architecture Status dashboard (screenshot)

**Placeholder location:** same side-by-side block in
`Background.tex` §Visualisation.

**Purpose.** Show the platform-engineer-facing surface — pod
inventory, HDFS health, CronJob fire times, the Cassandra alert
count.

**Capture this from the Grafana JSON in the code repo:**
`infra/cloud/grafana-cluster-status-dashboard.json`.

**Panels that should be visible.**

- Pod count by app (stat panel row)
- EKS worker node table
- CronJob schedules with last-fire times
- HDFS capacity bar (with the under-replicated-block count)
- Cassandra alert count (single stat panel)

**How to capture.** Same procedure as F3, but import
`grafana-cluster-status-dashboard.json` instead.

**Save as.** `Figures/dash-architecture-status.png` in the Overleaf
project.

---

## How to swap a placeholder for the real figure (quick recipe)

Each `\pqhung{FIG: ...}` block is wrapped in a description string,
not LaTeX figure code. The minimum swap is:

**Before:**
```latex
\pqhung{FIG: Lambda triad diagram ... at query time.}
```

**After (with a saved PDF):**
```latex
\begin{figure}[ht]
\centering
\includegraphics[width=0.85\linewidth]{Figures/lambda-triad.pdf}
\caption{Lambda architecture as a three-layer triad.}
\label{fig:lambda-triad}
\end{figure}
```

**After (with the TikZ block):** drop in the TikZ figure from F2
above. No image file needed.

Two cleanups when you swap:

1. Drop the `\pqhung{FIG: ...}` line entirely (do not just comment
   it out — leaving the red `[FIG: ...]` annotation in a final PDF
   is the same as leaving a TODO).
2. Make sure the surrounding paragraph still flows. The current
   `\pqhung{FIG: ...}` lines sit between paragraphs in display
   position; the `\begin{figure}...\end{figure}` block replaces them
   cleanly.

---

## Drawing checklist (for the team)

- [ ] F1 — architecture topology, `Figures/arch-topology.pdf`
- [ ] F2 — Lambda triad, `Figures/lambda-triad.pdf` *or* TikZ block
- [ ] F3 — Real-Time Alerts screenshot,
      `Figures/dash-realtime-alerts.png`
- [ ] F4 — Architecture Status screenshot,
      `Figures/dash-architecture-status.png`
- [ ] All four `\pqhung{FIG: ...}` placeholders deleted and replaced
- [ ] `\usepackage{subcaption}` added to `me310report.tex` (for
      the F3/F4 side-by-side)
- [ ] `\usepackage{tikz}` and `\usetikzlibrary{arrows.meta, positioning}`
      added to `me310report.tex` if you used the F2 TikZ block
- [ ] Recompile the report and confirm no remaining red `[FIG: ...]`
      annotations appear in the PDF

---

## File format quick reference

| File type | Use for | LaTeX preference |
|---|---|---|
| `.pdf` | Diagrams (vector) | **Best** — scales without pixelation |
| `.png` | Screenshots, photos | OK; export at 2× DPI |
| `.svg` | Diagrams | Convert to PDF before `\includegraphics` |
| `.jpg` | Photos only | Avoid for diagrams (lossy compression) |

For diagrams created in draw.io / Figma / Excalidraw, always export
PDF (or convert SVG → PDF via Inkscape if your tool doesn't export
PDF directly). For Grafana dashboards, PNG at 2× DPI is sufficient.
