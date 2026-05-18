# BrainWatch — Week 1 Checkpoint Presentation

---

## Slide 1: Problem Statement & Big Data Justification

### BrainWatch: Real-Time EEG Monitoring with EHR Context

**Problem:** Hospitals generate massive, continuous EEG streams (19–256 channels, 256–1024 Hz) that clinicians cannot monitor in real time. Delayed anomaly detection risks patient safety.

**Goal:** Build a Lambda-architecture pipeline that ingests multi-site clinical EEG alongside Electronic Health Records (EHR), performs batch + real-time analytics, and generates sub-minute anomaly alerts.

### Why is this a Big Data problem?

| Dimension | Evidence |
|---|---|
| **Volume** | 300k+ recordings across 5 clinical sites; single session spans minutes to days |
| **Velocity** | Continuous monitoring demands sub-minute ingestion-to-alert latency |
| **Variety** | EEG time-series, EHR structured records, lab values, diagnosis codes — distinct schemas and update cadences |
| **Veracity** | Missing patient IDs, delayed EHR updates, duplicate sessions, schema drift across source systems |

### Scope

- **In scope:** EEG/EHR subset acquisition, stream simulation, Lambda architecture (batch + speed + serving), Apache Spark, Kafka, Kubernetes deployment
- **Out of scope:** Clinical-grade diagnosis, production hospital deployment

---

## Slide 2: Lambda Architecture Design

### Why Lambda over Kappa?

| Criterion | Lambda | Kappa |
|---|---|---|
| Batch backfill from S3 | Native | Via stream replay |
| Real-time alerting | Speed layer | Native |
| Course rubric alignment | Both batch + streaming | Streaming only |

**Decision:** Lambda — batch recomputation is a first-class requirement, and dual-path design satisfies evaluation criteria.

### System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   DATA SOURCES                            │
│   EEG Subset (local)  │  EHR Subset  │  Replay Simulator │
└──────────┬─────────────┴──────┬───────┴────────┬──────────┘
           │                    │                │
           ▼                    ▼                ▼
┌──────────────────────────────────────────────────────────┐
│              MESSAGING LAYER (Kafka)                      │
│   eeg.raw  │  ehr.updates  │  features.realtime  │ ...   │
└──────┬─────────────────────────────────────┬──────────────┘
  ┌────▼────────────────┐    ┌───────────────▼──────────────┐
  │    BATCH LAYER      │    │      SPEED LAYER             │
  │    PySpark Jobs     │    │  Spark Structured Streaming   │
  │  Bronze→Silver→Gold │    │  Watermarks, joins, alerts   │
  └────┬────────────────┘    └───────────────┬──────────────┘
       │                                     │
       ▼                                     ▼
┌──────────────────────────────────────────────────────────┐
│               SERVING LAYER                               │
│    Cassandra / MongoDB — alerts, patient state, features  │
└──────────────────────────┬───────────────────────────────┘
                           ▼
              Dashboards & Monitoring (Prometheus, Grafana)
```

### Data Lake Zones

- **Bronze (Raw):** As-ingested EEG session manifests + schema-validated EHR events
- **Silver (Cleaned):** Deduplicated, quality-flagged, patient-keyed records
- **Gold (Business-Ready):** Joined EEG-EHR feature tables, alert summaries

---

## Slide 3: Data Sources & Metadata Profiling

### BDSP Clinical EEG Corpus — 5 Sites

| Site | Rows | Unique Subjects | Type |
|------|------|-----------------|------|
| S0001 | 96,768 | ~35k | BWH (Boston) |
| S0002 | 66,604 | ~25k | MGH (Boston) |
| I0002 | 74,488 | ~28k | External site |
| I0003 | 61,255 | ~22k | External site |
| I0009 | 7,626 | ~5k | External site |
| **Total** | **306,741** | **115,060** | |

### Metadata Profiling Results

- **Total valid hours** (≥30s recordings): **3,268,944 hours**
- **Service types:** LTM (82k), Routine (63k), EMU (14k), OR (501)
- **Sex distribution:** 65k Female, 68k Male, 5k Unknown
- **Data quality flags:** 427 short sessions (<30s), 11,579 missing duration

### Subset Selection Strategy

- Filter by duration (5–90 min), sort shortest-first
- Select 12 sessions across S0001 + S0002 → **~1 hour** for local development
- BIDS-formatted S3 paths: `EEG/bids/{site}/{subject}/ses-{id}/eeg/{file}.edf`

---

## Slide 4: Week 1 Implementation

### Code Architecture

```
src/brainwatch/
├── config/settings.py          # YAML config loader (ProjectSettings dataclass)
├── contracts/events.py         # 4 canonical event schemas
├── ingestion/
│   ├── eeg_inventory.py        # Metadata profiling (summarize_metadata)
│   └── subset_manifest.py      # Subset selection (select_subset)
├── processing/spark_pipeline.py # Structured Streaming skeleton
└── serving/anomaly_rules.py     # Rule-based alert classification
```

### Key Modules

| Module | What It Does | Key Functions |
|--------|-------------|---------------|
| **events.py** | 4 dataclasses: `EEGChunkEvent`, `EHREvent`, `FeatureEvent`, `AlertEvent` | `to_payload()`, `validate_required_fields()` |
| **eeg_inventory.py** | Reads 5 site CSVs, resolves service→task mapping, builds BIDS S3 keys, computes site/service/sex stats | `summarize_metadata()`, `build_candidate_s3_keys()` |
| **subset_manifest.py** | Filters candidates by duration bounds, sorts shortest-first, accumulates up to target hours | `select_subset()`, `write_manifest()` |
| **spark_pipeline.py** | Week-1 streaming skeleton: Kafka source, EEG+EHR schemas, watermarking (10min/30min), stream-stream `leftOuter` join, windowed aggregation, anomaly scoring | `build_realtime_query()` |
| **anomaly_rules.py** | `classify_anomaly(score, quality)` → suppressed / critical / warning / normal | Rule thresholds: quality<0.3→suppress, score≥0.85→critical, score≥0.6→warning |

### Infrastructure

- **Kubernetes:** namespace `brainwatch`, ConfigMap (5 topic names + 3 lake prefixes), Spark Job template
- **Configuration:** `project.example.yaml` with all parameters (data sources, Kafka, lakehouse, serving, monitoring)
- **Tests:** 5 unit tests covering metadata profiling, subset selection, and anomaly rules

### Spark Skill Coverage Plan (10 Required Techniques)

| Technique | Planned Week | Implementation |
|-----------|-------------|----------------|
| Window functions | 3 | Rolling patient/session metrics |
| Pivot / Unpivot | 3 | EHR lab features wide/long |
| Custom aggregation | 3 | Session-level quality scores |
| Multiple transformations | 3 | Bronze→Silver pipelines |
| UDFs | 4 | EEG feature logic, alert severity |
| Broadcast joins | 3 | Patient dimension lookups |
| Sort-merge joins | 3 | Historical EEG-EHR joins |
| Optimization | 3 | Partition pruning, caching, explain plans |
| Streaming | 4 | Watermarks, checkpointing, output modes |
| Advanced analytics | 5 | Anomaly trends, optional MLlib |

---

## Slide 5: Week 1 Results & Roadmap

### Generated Artifacts

| Artifact | Description |
|----------|-------------|
| `eeg_metadata_profile.json` | 306,741 rows profiled across 5 sites, 115,060 unique subjects, 3.27M valid hours |
| `eeg_subset_manifest.json` | 12 selected sessions (~1 hour), BIDS S3 paths for local staging |

### Test Results

```
tests/test_eeg_inventory.py      ✓  Row counting, subject dedup, S3 key generation
tests/test_subset_manifest.py    ✓  Max-sessions limit, duration-based ordering
tests/test_anomaly_rules.py      ✓  Suppressed / critical / warning classification (3 cases)
────────────────────────────────────
5 passed in 0.15s
```

### Team Role Matrix

| Role | Responsibility |
|------|---------------|
| **M1 — Project Lead** | Architecture ownership, report, milestone coordination |
| **M2 — EEG Data Engineer** | EEG ingestion, metadata profiling, data quality |
| **M3 — EHR & Feature Engineer** | EHR normalization, feature engineering, joins |
| **M4 — Spark & Streaming Engineer** | Batch/streaming Spark jobs, Kafka integration |
| **M5 — Infrastructure Engineer** | Kubernetes, monitoring, CI/CD, integration testing |

### 6-Week Roadmap

| Week | Focus | Status |
|------|-------|--------|
| **1** | **Foundation & Scaffold** | **Complete** ✓ |
| 2 | Ingestion Layer (Kafka, producers, bronze zone) | Next |
| 3 | Batch Layer (Bronze→Silver→Gold Spark jobs) | Planned |
| 4 | Speed Layer (Structured Streaming + alerts) | Planned |
| 5 | Serving Layer, dashboards, hardening | Planned |
| 6 | End-to-end integration, report, demo | Planned |

### Risk Mitigations

- **No live EEG stream?** → Replay session windows from manifests into Kafka
- **Spark cluster delays?** → Local-mode code + minimal K8s templates ready
- **EHR schema messiness?** → Canonical contract frozen in Week 1
- **Scope creep?** → Prioritize end-to-end reliability over advanced modeling
