# BrainWatch — Figure Drawing Guide

The report has four `\pqhung{FIG: ...}` placeholders that need to be
replaced with real figures before the final PDF is rendered. This
document is the end-to-end guide: what each figure should contain,
which tool to use, how to export it, where to put the file, and how
to swap the placeholder for `\includegraphics`.

## Quick inventory

| # | Figure | Where it lives in the report | Type | Recommended approach |
|---|---|---|---|---|
| F1 | **Deployed system topology** | `Prototyping.tex` §Architecture overview, label `fig:arch` | Architecture diagram | **TikZ — paste-ready block below** (no external tool needed) |
| F2 | **Lambda triad** | `Background.tex` §Lambda and Kappa, end of section | Concept diagram | **TikZ — paste-ready block below** |
| F3 | **Real-Time Alerts dashboard** | `Background.tex` §Visualisation, end of section (left half of side-by-side) | Stylised mockup | **TikZ — paste-ready block below** (skip the screenshot for now) |
| F4 | **Architecture Status dashboard** | `Background.tex` §Visualisation, end of section (right half of side-by-side) | Stylised mockup | **TikZ — paste-ready block below** |

All four figures ship as TikZ — no external drawing tool needed,
no Grafana screenshot needed. Drop the four `\begin{figure}…\end{figure}`
blocks below into the report and the figures render from the LaTeX
source.

## LaTeX preamble (one-time setup)

Add these to `me310report.tex` if they are not already present (the
amsmath / amssymb / booktabs / tabularx loads landed earlier):

```latex
\usepackage{tikz}
\usetikzlibrary{arrows.meta, positioning, fit, calc, shapes.geometric, backgrounds}
\usepackage{subcaption}
```

`arrows.meta` provides `-Latex`; `positioning` provides `right=of`,
`below=of`; `fit` lets a node wrap a group of other nodes (the EKS
envelope); `backgrounds` lets the EKS envelope sit *behind* its
contained nodes.

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

**Components to draw (each as one box, with resource annotations).**
Every spec below is the real number from the manifests in
`infra/cloud/k8s-overlays/`. The figure should look like a real
deployment diagram — each box carries (workload kind, replicas,
image, CPU req → limit, mem req → limit, PVC size if any).

**EKS cluster envelope (the outer container of the whole diagram).**

```
EKS 1.30, region us-east-1
nodegroup workers: 2 × t3.xlarge (4 vCPU, 16 GiB RAM each)
node root volume: 100 GiB gp3 per node
storage class: gp3 (EBS), CSI driver
namespace: brainwatch
```

Draw the EKS envelope as a labelled outer rectangle that contains
every pod / PVC; S3 buckets and BDSP sit *outside* the envelope.

- **Source lane (outside EKS, top-left):**
  - **BDSP S3** (external, credentialed access).
  - **`s3://brainwatch-capstone-923884399064/raw_edf/`** —
    project mirror bucket (17.05 GiB across 1,571 EDFs across 4 sites).
  - **Local download station** — developer machine running
    `scripts/download_real_edf.py` (one-shot, not in the cluster).

- **Streamer + bronze lane (inside EKS):**
  - **`bronze-streamer`** — Deployment, **replicas: 1**,
    `strategy: Recreate` (single-writer guarantee on bronze-pvc).
    - InitContainer: `amazon/aws-cli:2.17.0` (pulls wheel + scripts
      from S3).
    - Main container: `python:3.11-slim`.
    - **CPU: 500m → 1**,  **memory: 1 Gi → 2 Gi**.
    - Env: `RAW_EDF_BUCKET=brainwatch-capstone-923884399064`,
      `SLEEP_BETWEEN_EDF=20` (demo cadence; `=2` during burst).
  - **`bronze-pvc`** — PVC, **20 GiB gp3** (RWO).
    Capped archive: `ARCHIVE_RAW_CAP_GIB=4`.

- **Batch lane (inside EKS):**
  - **`hdfs-bronze-loader`** — CronJob, **schedule: `*/5 * * * *`**
    (every 5 min), `concurrencyPolicy: Forbid`,
    `ttlSecondsAfterFinished: 1800`.
    - Container: `bde2020/hadoop-namenode:2.0.0-hadoop3.2.1-java8`
      (used only for the `hdfs dfs -put` client).
    - Reads `bronze-pvc` (read-only mount), writes
      `hdfs://hdfs-namenode-0.../lake/bronze/{eeg,edf}/`.
    - Post-`-put` assertion: `EXPECTED_STREAMS` non-empty on HDFS.
  - **HDFS NameNode** — StatefulSet, **replicas: 1**,
    `bde2020/hadoop-namenode:2.0.0-hadoop3.2.1-java8`.
    - **CPU: 250m → 1**,  **memory: 768 Mi → 1500 Mi**.
    - PVC: **5 GiB gp3** (`namenode-data`, metadata only).
    - Service: ClusterIP, ports 9870 (UI) + 8020 (RPC).
  - **HDFS DataNode** — StatefulSet, **replicas: 2**, RF=2,
    64 MiB block, `bde2020/hadoop-datanode:2.0.0-hadoop3.2.1-java8`.
    - **CPU: 250m → 1**,  **memory: 512 Mi → 1 Gi** (per pod).
    - PVC: **20 GiB gp3** per replica (`datanode-data-hdfs-datanode-{0,1}`).
    - Total raw capacity: 40 GiB; effective at RF=2: 20 GiB.
  - **`spark-batch-hdfs`** — CronJob, **schedule:
    `2-59/5 * * * *`** (every 5 min, offset by 2 min so the loader
    lands first), `activeDeadlineSeconds: 600`.
    - Container: `spark:3.5.5-scala2.12-java17-python3-ubuntu`.
    - Driver: `--master 'local[4]' --driver-memory 4g
      --shuffle-partitions 16`, `spark.sql.adaptive.enabled=true`.
    - **CPU: 1 → 2**,  **memory: 3 Gi → 5 Gi**.
    - Reads HDFS bronze, writes HDFS silver + gold.
  - **HDFS `/lake/silver`** and **HDFS `/lake/gold`** — logical
    Parquet paths inside the DataNodes (draw as dashed sub-boxes
    inside the DataNode group; not separate pods).

- **Speed lane (inside EKS):**
  - **`kafka-producer`** — Deployment, **replicas: 1**.
    - InitContainer: `amazon/aws-cli:2.17.0`. Main: `python:3.11-slim`.
    - **CPU: 250m → 1**,  **memory: 512 Mi → 1 Gi**.
    - `acks=all`, `linger.ms=20`, `compression.type=gzip`.
  - **Kafka 3.9 KRaft** — StatefulSet, **replicas: 1**,
    `apache/kafka:3.9.0`.
    - **CPU: 500m → 1**,  **memory: 1 Gi → 2 Gi**.
    - PVC: **10 GiB gp3** (`kafka-data-kafka-0`).
    - Topics: `eeg.raw` and `ehr.updates`,
      **4 partitions per topic**, replication factor 1
      (single broker capstone posture).
  - **`speed-layer`** — Deployment, **replicas: 1**,
    `spark:3.5.5-scala2.12-java17-python3-ubuntu`.
    - **CPU: 1 → 2**,  **memory: 3 Gi → 5 Gi**.
    - Spark config: `--master 'local[*]' --driver-memory 3g
      --conf spark.sql.shuffle.partitions=8`,
      `spark.sql.adaptive.enabled=true`.
    - **Runs two streaming queries concurrently** (via
      `speed_layer.main() --mode=both`); draw them as two nested
      boxes inside the speed-layer pod:
      - **Lookup query** (`source='speed_lookup'`): subscribes only
        to `eeg.raw` (30 s watermark), aggregates per
        `(patient_id, window("30 s", "15 s"))`,
        `foreachBatch → fetch_patient_enrichment` against
        Cassandra `patient_state`, `compute_anomaly_score`, INSERT
        alert. `trigger="5 s"`, checkpoint at
        `/data/checkpoints/kafka_speed_layer`.
      - **Join query** (`source='speed_join'`): subscribes to BOTH
        `eeg.raw` (30 s watermark) and `ehr.updates` (30 min
        watermark), left-outer stream-stream JOIN on `patient_id`
        within `±30 min` event-time predicate, aggregates per
        `(patient_id, window("1 m", "30 s"))`,
        `compute_anomaly_score` on the joined row, INSERT alert.
        `trigger="30 s"`, checkpoint at
        `/data/checkpoints/kafka_speed_join`.
    - Both queries share `checkpoints-pvc` (5 GiB gp3); each has its
      own subdirectory so a checkpoint reset on one doesn't disturb
      the other.
  - **`checkpoints-pvc`** — PVC, **5 GiB gp3** (Spark state store).
    Holds two subdirectories: `kafka_speed_layer/` (lookup query
    offsets + state-store deltas) and `kafka_speed_join/` (join
    query offsets + state-store deltas).

- **Serving lane (inside EKS):**
  - **Cassandra 4.1** — StatefulSet, **replicas: 1**, `RF=1`,
    `SimpleStrategy`, `cassandra:4.1`.
    - **CPU: 500m → 2**,  **memory: 1 Gi → 4 Gi**.
    - PVC: **20 GiB gp3** (`cassandra-data-cassandra-0`).
    - Schema: `alerts(patient_id, alert_time, severity, anomaly_score,
      explanation)` PK `(patient_id, alert_time)`,
      clustering DESC.
  - **`cassandra-exporter`** — Deployment, **replicas: 1**,
    `python:3.11-slim`.
    - **CPU: 100m → 500m**,  **memory: 256 Mi → 768 Mi**.
    - Polls Cassandra every 30 s, writes alert roll-up JSONs to
      `s3://brainwatch-dashboard-923884399064/`.
  - **`cluster-state-exporter`** — Deployment, **replicas: 1**,
    `alpine/k8s:1.30.0`.
    - **CPU: 50m → 250m**,  **memory: 128 Mi → 384 Mi**.
    - ServiceAccount `cluster-state-reader` with `get/list` on
      pods/deployments/statefulsets/jobs/cronjobs (namespace) + nodes
      (ClusterRole).
    - Probes: `kubectl get pods/nodes/cronjobs/jobs`,
      `kubectl exec sts/hdfs-namenode -- hdfs dfsadmin -report`,
      `kubectl exec sts/cassandra -- cqlsh -e "SELECT COUNT(*)..."`.
    - Writes 7 flat JSONs to S3 every 30 s.
  - **Grafana 11** — Deployment, **replicas: 1**,
    `grafana/grafana:11.2.0`.
    - **CPU: 250m → 1**,  **memory: 384 Mi → 1 Gi**.
    - PVC: **5 GiB gp3** (`grafana-data`).
    - Service: NodePort (port 30030 → 3000).
    - Datasource: `yesoreyeram-infinity-datasource` over HTTPS to
      the S3 dashboard bucket.

- **Serving sinks (outside EKS, top-right):**
  - **`s3://brainwatch-dashboard-923884399064/`** — static-website
    S3 bucket holding the alert roll-up JSONs and the cluster-state
    JSONs (consumed by Grafana via Infinity datasource).

**Cluster resource totals (good to call out on the diagram).**

| Quantity | Value |
|---|---|
| EKS worker nodes | 2 × t3.xlarge (8 vCPU, 32 GiB RAM total) |
| Total pod CPU requests | ~5.6 vCPU |
| Total pod memory requests | ~12 GiB |
| Total EBS provisioned | 5 + 20 + 20 + 10 + 20 + 5 + 5 = **85 GiB** across 7 PVCs |
| EBS snapshots at pause | 8 (the 7 PVCs above + bronze-pvc) |
| Headroom under EKS node capacity | ~30% CPU / ~60% memory at steady state |

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

**LaTeX swap — paste-ready TikZ.** Replace the entire
`\begin{figure}[t]...\end{figure}` block in `Prototyping.tex`
(currently wrapping the `\pqhung{FIG: insert the architecture diagram
above; ...}` placeholder) with:

```latex
\begin{figure}[t]
\centering
\resizebox{\linewidth}{!}{%
\begin{tikzpicture}[
  font=\footnotesize,
  pod/.style={draw, rounded corners, fill=blue!8,
              minimum width=2.2cm, minimum height=0.9cm, align=center},
  cron/.style={draw, rounded corners, fill=orange!10,
               minimum width=2.2cm, minimum height=0.9cm, align=center},
  pvc/.style={draw, rounded corners=2pt, fill=green!10,
              minimum width=1.6cm, minimum height=0.6cm, align=center,
              font=\scriptsize},
  ext/.style={draw, rounded corners, fill=gray!10,
              minimum width=2.0cm, minimum height=0.9cm, align=center},
  arr/.style={-Latex, thick},
  ctrl/.style={-Latex, thick, dashed, gray},
  envelope/.style={draw, dashed, rounded corners, inner sep=8pt,
                   label={[anchor=north west,
                           font=\scriptsize\bfseries,
                           xshift=4pt, yshift=-2pt]north west:EKS 1.30
                           cluster --- 2$\times$t3.xlarge (8\,vCPU,
                           32\,GiB), namespace \texttt{brainwatch}}},
  every node/.style={inner sep=3pt},
]
% ----- external (outside EKS) -----
\node[ext] (bdsp)   at (0, 6.5) {BDSP S3\\\scriptsize(credentialed)};
\node[ext, right=0.6cm of bdsp] (bucket)
                                 {\texttt{raw\_edf/}\\\scriptsize 17\,GiB \(\cdot\) 1{,}571 EDFs};
\node[ext, right=4.0cm of bucket] (dashbucket)
                                 {\texttt{dashboard/}\\\scriptsize S3 static site};
% ----- ingestion lane -----
\node[pod] (streamer) at (0, 4.5) {bronze-streamer\\\scriptsize Deploy 1, 500m$\to$1, 1$\to$2\,Gi};
\node[pvc, below=0.3cm of streamer]
                       (bronzepvc) {bronze-pvc 20\,Gi};
\node[pod, right=0.4cm of streamer]
                       (kafkaprod) {kafka-producer\\\scriptsize Deploy 1, 250m$\to$1};
% ----- storage + bus -----
\node[pod, right=0.5cm of kafkaprod]
                       (kafka)     {Kafka 3.9 KRaft\\\scriptsize STS 1, PVC 10\,Gi, 4 parts};
\node[cron, right=0.6cm of kafka]
                       (loader)    {hdfs-bronze-loader\\\scriptsize CronJob */5 min};
\node[pod, right=0.6cm of loader, minimum height=2.3cm,
        align=center, label={[font=\scriptsize\bfseries]above:HDFS RF=2}]
                       (hdfsbox)
   {NameNode 5\,Gi\\[-1pt]\hrulefill\\[-1pt]DN-0 20\,Gi\\[-1pt]DN-1 20\,Gi};
% ----- compute lane -----
\node[cron, below=0.5cm of loader]
                       (sparkbatch){spark-batch-hdfs\\\scriptsize CronJob 2-59/5 min, local[4]};
\node[pod, below=0.5cm of kafka, minimum width=2.5cm, minimum height=1.4cm,
        align=center,
        label={[font=\scriptsize\bfseries]above:speed-layer (Deploy 1)}]
                       (speed)     {lookup query\\\textit{source}=speed\_lookup\\[-1pt]\hrulefill\\[-1pt]join query\\\textit{source}=speed\_join};
\node[pvc, below=0.3cm of speed]
                       (chkpt)     {checkpoints-pvc 5\,Gi};
% ----- serving lane -----
\node[pod, below=0.6cm of hdfsbox]
                       (cassandra) {Cassandra 4.1\\\scriptsize STS 1, PVC 20\,Gi, RF=1};
\node[pod, below=0.5cm of cassandra]
                       (cassexp)   {cassandra-exporter\\\scriptsize Deploy 1};
\node[pod, below=0.5cm of speed]
                       (clusexp)   {cluster-state-exporter\\\scriptsize Deploy 1};
\node[pod, below=0.5cm of clusexp]
                       (grafana)   {Grafana 11\\\scriptsize Deploy 1, NodePort};
% ----- arrows -----
\draw[arr] (bdsp) -- (bucket);
\draw[arr] (bucket) -- (streamer);
\draw[arr] (streamer) -- (bronzepvc);
\draw[arr] (bronzepvc.east) -- ++(0.3,0) |- (loader.west);
\draw[arr] (loader) -- (hdfsbox);
\draw[arr] (hdfsbox.south) |- (sparkbatch.east);
\draw[arr] (sparkbatch) -- (loader);                 % batch reads from HDFS via loader path
\draw[arr] (kafkaprod) -- (kafka);
\draw[arr] (kafka)     -- (speed);
\draw[arr] (speed)     -- (cassandra);
\draw[arr] (cassandra) -- (cassexp);
\draw[arr] (cassexp.east) to[bend right=20]
                          (dashbucket.south west);
\draw[ctrl] (clusexp) -- (cassandra);                % read-only probes
\draw[ctrl] (clusexp.east) -- ++(0.3,0) |- (hdfsbox.south west);
\draw[arr]  (clusexp.east) to[bend right=8] (dashbucket);
\draw[arr]  (dashbucket) -- (grafana);
% ----- EKS envelope -----
\begin{scope}[on background layer]
  \node[envelope, fit=(streamer)(bronzepvc)(kafkaprod)(kafka)(loader)
                       (hdfsbox)(sparkbatch)(speed)(chkpt)
                       (cassandra)(cassexp)(clusexp)(grafana)] {};
\end{scope}
\end{tikzpicture}%
}
\caption{Deployed topology of BrainWatch on Amazon EKS. The hybrid
storage layer uses HDFS for the compute-side lake and Amazon S3 for
the raw archive and the dashboard serving layer. Solid arrows carry
data; dashed arrows are read-only probes from the cluster-state
exporter.}
\label{fig:arch}
\end{figure}
```

The `\resizebox{\linewidth}{!}{ ... }` wrapper makes the diagram
auto-fit page width; remove it if the figure already fits, or replace
with `[scale=0.85]` on the `\begin{tikzpicture}` for fine control.
Every numeric annotation (`RF=2`, `5\,Gi`, `4 parts`, etc.) comes
straight from the manifests in `infra/cloud/k8s-overlays/` — the
inventory table in the next section is the source of truth.

---

## F2 — Lambda triad

**Placeholder location:** `Background.tex` end of §Lambda and Kappa.

**Purpose.** Show the canonical three-layer Lambda decomposition
(batch + speed + serving) at the conceptual level. This is the
textbook view, not the BrainWatch-specific topology — keep it
schematic.

**Components to draw (three boxes, each annotated with BrainWatch
numbers so the triad is project-specific, not generic textbook).**

- **Batch layer** (top-left box). Inside:
  - "HDFS bronze → Spark batch → silver / gold Parquet"
  - CronJob `spark-batch-hdfs`, schedule `*/5 min`
  - Spark 3.5.5, `local[4]`, 4 GiB driver, 16 shuffle partitions
  - **Per-fire runtime: ~50 s** (47.8 s on 8.2 GiB local)
  - Silver: 0.87 MiB, Gold: 16.9 KiB after each fire
- **Speed layer** (bottom-left box). Inside:
  - "Kafka 3.9 KRaft → Spark Structured Streaming → Cassandra"
  - 4 partitions/topic, 1.3M+ events each
  - **Two concurrent queries:**
    - **Lookup** (`source='speed_lookup'`): 30 s watermark,
      30 s/15 s window, `trigger=5 s`, Cassandra
      `patient_state` lookup in `foreachBatch`. **p50 ≈ 12 s**.
    - **Join** (`source='speed_join'`): canonical Kafka
      stream-stream join, 30 s EEG + 30 min EHR watermarks,
      ±30 min predicate, 1 min/30 s window, `trigger=30 s`.
      ≈ 60 s emission (append + windowed agg).
  - **60–100 alerts per micro-batch** (lookup path)
- **Serving layer** (right box, spanning both). Inside:
  - "Grafana 11 over S3 JSON + Cassandra"
  - Cassandra PK `(patient_id, alert_time)` absorbs replays
  - Two dashboards: Real-Time Alerts + Architecture Status
  - **Survives full cluster teardown** (S3 keeps serving)
  - Paused storage cost ≈ \$1/month

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

**How to draw it in TikZ (with BrainWatch numbers baked in).** Drop
this into `Background.tex` in place of the `\pqhung{FIG: ...}` block:

```latex
\begin{figure}[ht]
\centering
\begin{tikzpicture}[
  box/.style={draw, rounded corners, minimum width=5.4cm,
              minimum height=1.8cm, align=center, font=\small},
  smallbox/.style={draw, rounded corners, minimum width=3.0cm,
                   minimum height=1.4cm, align=center, font=\small},
  arr/.style={-Latex, thick},
  every node/.style={font=\small}
]
\node[smallbox, fill=gray!10]  (raw) {Raw archive\\(Amazon S3)\\17 GiB / 1{,}571 EDFs};
\node[box, fill=blue!10, right=2cm of raw, yshift=1.8cm] (batch)
   {\textbf{Batch layer}\\HDFS bronze $\to$ Spark $\to$ silver / gold\\
    CronJob \texttt{*/5 min}, Spark 3.5.5 \texttt{local[4]}\\
    runtime $\sim 50$~s, silver 0.87~MiB / gold 16.9~KiB};
\node[box, fill=orange!10, right=2cm of raw, yshift=-1.8cm] (speed)
   {\textbf{Speed layer}\\Kafka 3.9 KRaft $\to$ Spark Structured\\
    Streaming $\to$ Cassandra alerts\\
    30~s watermark, 30/15~s window, trigger 5~s\\
    60--100 alerts / micro-batch, p50 $\approx 12$~s};
\node[box, fill=green!10, right=2cm of batch, yshift=-1.8cm,
       minimum height=4cm] (serve)
   {\textbf{Serving layer}\\Grafana 11 over S3 JSON\\ + Cassandra (RF=1)\\
    PK \texttt{(patient\_id, alert\_time)}\\
    survives cluster teardown};
\draw[arr] (raw) -- (batch.west);
\draw[arr] (raw) -- (speed.west);
\draw[arr] (batch.east) -- node[above]{batch view} (serve.north west);
\draw[arr] (speed.east) -- node[below]{speed view} (serve.south west);
\node[below=0.8cm of serve, font=\footnotesize, text width=5cm,
       align=center, text=gray]
       {Views merged at query time\\(eventual consistency).};
\end{tikzpicture}
\caption{Lambda architecture as a three-layer triad, annotated with
the BrainWatch realisation of each layer.}
\label{fig:lambda-triad}
\end{figure}
```

You will need to add `\usepackage{tikz}` and
`\usetikzlibrary{arrows.meta, positioning}` to `me310report.tex` if
not already present.

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

## Paste-ready TikZ for F3 + F4 (skip the screenshots)

If you'd rather ship the figures without bringing up Grafana, the
following TikZ block produces a stylised side-by-side mockup of both
dashboards. Drop it in `Background.tex` in place of the
`\pqhung{FIG: Two Grafana dashboard screenshots side by side ...}`
block:

```latex
\begin{figure}[ht]
\centering
\begin{subfigure}{0.49\linewidth}
\centering
\begin{tikzpicture}[
  font=\scriptsize,
  panel/.style={draw, rounded corners, minimum width=3.4cm,
                minimum height=1.1cm, align=center, fill=blue!4},
  hdr/.style={draw, rounded corners, fill=blue!20, minimum width=7.0cm,
              minimum height=0.6cm, align=center, font=\bfseries\small},
  tag/.style={font=\bfseries},
]
\node[hdr] (title) at (0, 0) {Real-Time Alerts};
\node[panel, below=0.25cm of title, xshift=-1.9cm] (sev)
    {\tag{Severity stat}\\critical 5 \(\cdot\) warning 12\\advisory 38 \(\cdot\) normal 145};
\node[panel, right=0.2cm of sev] (rate)
    {\tag{Alert rate / site}\\\textit{time series}\\last 30 min};
\node[panel, below=0.25cm of sev, minimum width=7.0cm,
      minimum height=1.4cm] (table)
    {\tag{Recent alerts table}\\P003 \(\cdot\) 12:04 \(\cdot\) critical\\P017 \(\cdot\) 12:03 \(\cdot\) warning\\P021 \(\cdot\) 12:02 \(\cdot\) advisory \(\cdot\) \dots};
\node[panel, below=0.25cm of table, minimum width=7.0cm] (bar)
    {\tag{Alert-by-severity bar chart}};
\end{tikzpicture}
\caption{Clinician-facing surface, refreshed every 30\,s from S3.}
\end{subfigure}\hfill
\begin{subfigure}{0.49\linewidth}
\centering
\begin{tikzpicture}[
  font=\scriptsize,
  panel/.style={draw, rounded corners, minimum width=3.4cm,
                minimum height=1.1cm, align=center, fill=orange!6},
  hdr/.style={draw, rounded corners, fill=orange!25, minimum width=7.0cm,
              minimum height=0.6cm, align=center, font=\bfseries\small},
  tag/.style={font=\bfseries},
]
\node[hdr] (title) at (0, 0) {Architecture Status};
\node[panel, below=0.25cm of title, xshift=-1.9cm] (pods)
    {\tag{Pods by app}\\bronze-streamer: 1\\speed-layer: 1 \(\cdot\) Cassandra: 1};
\node[panel, right=0.2cm of pods] (nodes)
    {\tag{EKS nodes}\\2 \(\times\) t3.xlarge\\both Ready};
\node[panel, below=0.25cm of pods, minimum width=7.0cm] (crons)
    {\tag{CronJobs}\\hdfs-bronze-loader \(\cdot\) spark-batch-hdfs\\last fire \(\sim\) 30\,s ago};
\node[panel, below=0.25cm of crons] (hdfs)
    {\tag{HDFS capacity}\\used \(\sim\)9.4\,GiB / 40\,GiB \(\cdot\) RF=2\\under-rep blocks: 0};
\node[panel, right=0.2cm of hdfs] (cass)
    {\tag{Cassandra}\\alerts: 100\,K+\\(speed\_lookup + speed\_join)};
\end{tikzpicture}
\caption{Platform-engineer surface, same S3-backed refresh.}
\end{subfigure}
\caption{The two BrainWatch Grafana dashboards. Both read static JSON
from Amazon S3, so they keep rendering when the EKS cluster is torn
down. Mockup-style rendering --- panel labels show what each tile
contains; substitute real screenshots later if desired.}
\label{fig:dashboards}
\end{figure}
```

Required preamble: `tikz`, `\usetikzlibrary{positioning}`,
`subcaption` (already named in the one-time setup at the top).

---

## Reference: the resource table to embed in F1

If the topology diagram (F1) gets too crowded with per-box
annotations, an alternative is to keep each box minimal (name only)
and place a **legend table** beside the diagram with all the
resource numbers. The table below is ready to paste into the same
figure float as F1 (e.g. via a TikZ-and-tabular `minipage` pair).

```latex
\begin{table}[ht]
\centering
\caption{Workload inventory for the topology in Figure~\ref{fig:arch}.}
\label{tab:workloads}
\begin{tabularx}{\linewidth}{@{}lllrr>{\RaggedRight\arraybackslash}X@{}}
\toprule
\textbf{Workload} & \textbf{Kind} & \textbf{Replicas} & \textbf{CPU req $\to$ lim} & \textbf{Mem req $\to$ lim} & \textbf{PVC / image} \\
\midrule
HDFS NameNode       & StatefulSet & 1 & 250m $\to$ 1 & 768Mi $\to$ 1500Mi & 5~GiB; \texttt{bde2020/hadoop-namenode:2.0.0-hadoop3.2.1-java8} \\
HDFS DataNode       & StatefulSet & 2 & 250m $\to$ 1 & 512Mi $\to$ 1Gi    & 20~GiB each; same image \\
Kafka (KRaft)       & StatefulSet & 1 & 500m $\to$ 1 & 1Gi $\to$ 2Gi      & 10~GiB; \texttt{apache/kafka:3.9.0} \\
Cassandra           & StatefulSet & 1 & 500m $\to$ 2 & 1Gi $\to$ 4Gi      & 20~GiB; \texttt{cassandra:4.1} \\
Grafana             & Deployment  & 1 & 250m $\to$ 1 & 384Mi $\to$ 1Gi    & 5~GiB; \texttt{grafana/grafana:11.2.0} \\
Bronze streamer     & Deployment  & 1 & 500m $\to$ 1 & 1Gi $\to$ 2Gi      & uses \texttt{bronze-pvc} 20~GiB; \texttt{python:3.11-slim} \\
Kafka producer      & Deployment  & 1 & 250m $\to$ 1 & 512Mi $\to$ 1Gi    & --- \\
Speed layer         & Deployment  & 1 & 1 $\to$ 2    & 3Gi $\to$ 5Gi      & uses \texttt{checkpoints-pvc} 5~GiB; \texttt{spark:3.5.5-...} \\
Cassandra exporter  & Deployment  & 1 & 100m $\to$ 500m & 256Mi $\to$ 768Mi & writes to S3; \texttt{python:3.11-slim} \\
Cluster-state exp.  & Deployment  & 1 & 50m $\to$ 250m  & 128Mi $\to$ 384Mi & ServiceAccount \texttt{cluster-state-reader}; \texttt{alpine/k8s:1.30.0} \\
HDFS bronze loader  & CronJob     & \texttt{*/5 m} & ---  & --- & reads \texttt{bronze-pvc}, writes HDFS \\
Spark batch (HDFS)  & CronJob     & \texttt{2-59/5 m} & 1 $\to$ 2 & 3Gi $\to$ 5Gi & \texttt{spark:3.5.5-...}, \texttt{local[4]}, 16 shuffle parts \\
\bottomrule
\end{tabularx}
\end{table}
```

## Reference: EBS snapshot inventory (paste-ready table)

Eight snapshots underpin the pause-resume cycle. The canonical
mapping is in `artifacts/eks/snapshots/index.txt`. Annotate F1 or
Vision/Reflections L11 with a small table built from those rows:

```latex
\begin{table}[ht]
\centering
\caption{EBS snapshot inventory at the most recent pause.}
\label{tab:snapshots}
\begin{tabularx}{\linewidth}{@{}lllr@{}}
\toprule
\textbf{PVC} & \textbf{Source volume} & \textbf{Snapshot id} & \textbf{Provisioned} \\
\midrule
\texttt{namenode-data-hdfs-namenode-0}   & \texttt{vol-00ea38d307c16a60b} & \texttt{snap-00171850c89fb181f} & 5~GiB  \\
\texttt{datanode-data-hdfs-datanode-0}   & \texttt{vol-02faf5b4425a87186} & \texttt{snap-03d0070e899587114} & 20~GiB \\
\texttt{datanode-data-hdfs-datanode-1}   & \texttt{vol-065217cc6f51394fe} & \texttt{snap-03fab486ccff11c98} & 20~GiB \\
\texttt{kafka-data-kafka-0}              & \texttt{vol-01ffcd4646c5ffbcd} & \texttt{snap-047f50e399deb4339} & 10~GiB \\
\texttt{cassandra-data-cassandra-0}      & \texttt{vol-06606813872ac7b47} & \texttt{snap-0f39245bdf4b729a9} & 20~GiB \\
\texttt{grafana-data}                    & \texttt{vol-0a7016a6b9f21fef9} & \texttt{snap-08019cf73fb58a4c2} & 5~GiB  \\
\texttt{checkpoints-pvc}                 & \texttt{vol-06e24e1fb3e5eaf25} & \texttt{snap-09282cc948581f732} & 5~GiB  \\
\texttt{bronze-pvc}                      & \texttt{vol-0610a5914ee051970} & \texttt{snap-04cdf68c2b81bb7ea} & 20~GiB \\
\bottomrule
\end{tabularx}
\end{table}
```

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
