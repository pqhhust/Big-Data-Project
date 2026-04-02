# BrainWatch — Architecture Design Document

## 1. Overview

BrainWatch implements a **Lambda Architecture** to combine batch and real-time processing of clinical EEG recordings enriched with Electronic Health Record (EHR) context. The system detects neurological anomalies with sub-minute latency while maintaining historical feature recomputation capabilities.

## 2. Architecture Rationale

| Criterion | Lambda | Kappa |
|---|---|---|
| Batch backfill from S3 | Native | Via stream replay |
| Real-time alerting | Speed layer | Native |
| Course rubric alignment | Both batch + streaming demonstrated | Streaming only |
| Operational complexity | Higher (two paths) | Lower (single path) |

**Decision:** Lambda — batch recomputation and historical feature backfill are first-class requirements that benefit from a dedicated batch path, and the dual-path design directly satisfies course evaluation criteria.

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      DATA SOURCES                            │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │ EEG Subset  │  │ EHR Subset  │  │ Synthetic Live Feed  │ │
│  │ (local)     │  │ (local)     │  │ (replay simulator)   │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘ │
└─────────┼────────────────┼─────────────────────┼─────────────┘
          │                │                     │
          ▼                ▼                     ▼
┌──────────────────────────────────────────────────────────────┐
│                     MESSAGING LAYER                          │
│  Kafka Topics: eeg.raw │ ehr.updates │ features.realtime │   │
│                alerts.anomaly                                │
└──────┬──────────────────────────────────────────┬────────────┘
       │                                          │
  ┌────▼──────────────────┐    ┌──────────────────▼───────────┐
  │     BATCH LAYER       │    │       SPEED LAYER            │
  │  PySpark Jobs         │    │  Spark Structured Streaming  │
  │  Bronze → Silver →    │    │  Watermarks, stateful agg,   │
  │  Gold transformations │    │  stream-stream joins         │
  └────┬──────────────────┘    └──────────────────┬───────────┘
       │                                          │
       ▼                                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     SERVING LAYER                            │
│  Cassandra / MongoDB — alerts, patient state, feature lookup │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                  OBSERVABILITY & DEPLOYMENT                   │
│  Kubernetes │ Prometheus │ Grafana │ Structured Logs          │
└──────────────────────────────────────────────────────────────┘
```

## 4. Component Matrix

| Layer | Component | Technology | Purpose |
|---|---|---|---|
| Source | EEG/EHR subsets | Local landing zone | Multi-site clinical data (BDSP) |
| Ingestion | Profiler, manifest builder | Python | Metadata inventory & subset selection |
| Messaging | Event bus | Apache Kafka | Decoupled, partitioned event transport |
| Batch | Historical processing | PySpark | Bronze → Silver → Gold lake transformations |
| Speed | Real-time processing | Spark Structured Streaming | Watermarked joins, stateful aggregation, alerting |
| Serving | Query store | Cassandra / MongoDB | Low-latency alert & patient-state lookup |
| Deployment | Runtime platform | Kubernetes | Namespace isolation, reproducible deployment |
| Observability | Metrics & logs | Prometheus, Grafana | Lag monitoring, batch duration, alert rates |

## 5. Data Lake Zones

### Bronze (Raw)
- Raw EEG session manifests (as-ingested)
- Raw EHR normalized events (schema-validated, no business logic)

### Silver (Cleaned)
- Cleaned EEG session catalog (deduplicated, quality-flagged)
- Cleaned EHR updates (patient-keyed, version-resolved)
- Patient/session reference tables

### Gold (Business-Ready)
- Joined EEG-EHR feature tables
- Alert trend summaries
- Dashboard-ready patient risk state

## 6. Streaming Design

1. EEG chunk events are replayed or emitted into `eeg.raw`
2. EHR updates are published into `ehr.updates`
3. Spark Structured Streaming performs:
   - **Watermarking** on event timestamps (10 min EEG / 30 min EHR)
   - **Stream-stream join** (`leftOuter` on `patient_id` + time window)
   - **Stateful aggregations** (rolling chunk count, channel stats, lab values)
   - **Anomaly scoring** (rule-based classification)
4. Outputs are written to:
   - `features.realtime` topic
   - `alerts.anomaly` topic
   - NoSQL serving sink (checkpointed, exactly-once)

## 7. Spark Skill Coverage

| Requirement | Planned Implementation |
|---|---|
| Window functions | Rolling patient/session metrics; windowed alert counts |
| Pivot / Unpivot | EHR lab features pivoted wide; feature export unpivoted long |
| Custom aggregation | Session-level signal quality scores; abnormal-event rollups |
| Multiple transformations | Chained Bronze → Silver normalization pipelines |
| UDFs | EEG-derived feature logic; alert severity classification |
| Broadcast joins | Small patient dimension + diagnosis dictionary lookups |
| Sort-merge joins | Large historical EEG-EHR feature table joins |
| Optimization | Partition pruning (date/site); cache hot silver tables; explain plans |
| Streaming | Watermarks, checkpointing, output mode selection |
| Advanced analytics | Time-series anomaly trends; optional Spark MLlib baseline |

## 8. Deployment Topology

- **Namespace:** `brainwatch` (Kubernetes)
- **ConfigMaps:** Topic names, lake zone prefixes, feature thresholds
- **Secrets:** Object-store credentials, service accounts (when applicable)
- **Jobs:**
  - Spark batch job template (historical processing)
  - Spark streaming job template (real-time pipeline)
  - Kafka broker deployment (or managed service connection)

## 9. Monitoring & Observability

| Metric | Source | Alert Threshold |
|---|---|---|
| Kafka consumer lag | Kafka metrics | > 10k messages |
| Micro-batch duration | Spark Streaming UI | > 60s |
| Records in/out per topic | Kafka + Spark | Anomalous drop > 50% |
| Checkpoint freshness | HDFS / object store | Stale > 5 min |
| Alert rate by severity | Serving layer | Spike > 3σ from baseline |
| Failed / quarantined records | Bronze zone | > 1% of batch |

## 10. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Inconsistent patient identifiers across EEG/EHR | Broken joins, missed alerts | Explicit alignment rules; quarantine unmatched records |
| Delayed EHR updates | Stale context in speed layer | Watermark tolerance; late-data handling policy |
| Spark resource pressure during historical joins | OOM, slow batches | Partition pruning; caching strategy; explain plan review |
| Schema drift across source systems | Pipeline failures | Bronze-layer schema validation; contract versioning |
