# BrainWatch Big Data Platform — Detailed Code Implementation Plan

**Project Name:** BrainWatch — Lambda-Architecture EEG Anomaly Detection System  
**Version:** 0.1.0  
**Target:** Python 3.11+, Apache Spark 3.5, Kafka, Cassandra/MongoDB, Kubernetes  
**Roadmap:** 6-week milestone project  
**Date Created:** 2026-05-08

---

## Executive Summary

BrainWatch is a hospital-scale real-time EEG monitoring platform implementing a **Lambda Architecture** for both batch and streaming analytics. The system ingests multi-site clinical EEG recordings and asynchronous EHR updates, performs anomaly detection via Apache Spark, and generates near-real-time alerts through a rule-based serving layer.

**Key Characteristics:**
- **Scale:** Multi-site clinical data (BDSP corpus)
- **Processing:** Dual-path architecture (batch + speed layer)
- **Latency:** Sub-minute anomaly alerting
- **Deployment:** Kubernetes-native microservices
- **Current Status:** Week 1 foundation complete; Weeks 2-6 in development pipeline

---

## Project Architecture Overview

### System Topology

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                             │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────────┐ │
│  │ EEG Data │  │EHR Data  │  │ Live Feed Simulator (Kafka)│ │
│  │ (Local)  │  │ (Local)  │  │ (Synthetic Replay)         │ │
│  └────┬─────┘  └────┬─────┘  └─────────────┬──────────────┘ │
└───────┼─────────────┼────────────────────────┼────────────────┘
        │             │                        │
        └─────────────┴────────────┬───────────┘
                                   │
        ┌──────────────────────────▼───────────────────────────┐
        │           KAFKA MESSAGE BUS                          │
        │  Topics:                                             │
        │  • eeg.raw          — Raw EEG chunk events          │
        │  • ehr.updates      — Health record updates         │
        │  • features.realtime— Real-time feature outputs     │
        │  • alerts.anomaly   — Anomaly alerts                │
        └──────┬─────────────────────────────┬────────────────┘
               │                             │
        ┌──────▼──────────────┐     ┌───────▼──────────────┐
        │   BATCH LAYER       │     │   SPEED LAYER        │
        │ ┌─────────────────┐ │     │ ┌──────────────────┐ │
        │ │ PySpark Jobs    │ │     │ │Spark Struct      │ │
        │ │ ┌─────────────┐ │ │     │ │Streaming         │ │
        │ │ │Bronze Zone  │ │ │     │ │ ┌──────────────┐│ │
        │ │ │(raw ingress)│ │ │     │ │ │Stream joins  ││ │
        │ │ └─────────────┘ │ │     │ │ │Watermarks    ││ │
        │ │ ┌─────────────┐ │ │     │ │ │Aggregations  ││ │
        │ │ │Silver Zone  │ │ │     │ │ │Anomaly rules ││ │
        │ │ │(transform)  │ │ │     │ │ └──────────────┘│ │
        │ │ └─────────────┘ │ │     │ └──────────────────┘ │
        │ │ ┌─────────────┐ │ │     └──────────┬───────────┘
        │ │ │Gold Zone    │ │ │                │
        │ │ │(business)   │ │ │                │
        │ │ └─────────────┘ │ │                │
        │ └─────────────────┘ │                │
        └──────┬──────────────┘                │
               │                              │
               └──────────────┬───────────────┘
                              │
        ┌─────────────────────▼──────────────────┐
        │      SERVING LAYER                     │
        │  NoSQL Stores:                         │
        │  • Cassandra/MongoDB for alerts        │
        │  • Patient state lookups               │
        │  • Feature caching                     │
        └─────────────────────┬──────────────────┘
                              │
        ┌─────────────────────▼──────────────────┐
        │  OBSERVABILITY & DEPLOYMENT            │
        │  • Kubernetes namespace isolation      │
        │  • Prometheus metrics                  │
        │  • Grafana dashboards                  │
        │  • Structured logging                  │
        └────────────────────────────────────────┘
```

### Architecture Decision: Lambda vs Kappa

**Selected:** Lambda Architecture

| Criterion | Lambda | Kappa |
|-----------|--------|-------|
| Batch historical backfill | ✅ Native | ⚠️ Via stream replay |
| Real-time alerting | ✅ Speed layer | ✅ Native |
| Course requirements | ✅ Both demonstrated | ⚠️ Streaming only |
| Operational complexity | ⚠️ Higher (dual paths) | ✅ Lower (single) |

**Rationale:** Batch recomputation and historical feature backfill are first-class requirements. The dual-path design satisfies course evaluation criteria and provides operational flexibility.

---

## Detailed Module Structure

### 1. Configuration Layer (`src/brainwatch/config/`)

**Purpose:** Centralized configuration management for all runtime parameters.

#### Module: `settings.py`
- **Current Implementation:**
  - `ProjectSettings` dataclass: project name, architecture selection, raw YAML payload
  - `load_settings(path)`: YAML config loader with validation
  
- **Week 2+ Enhancements:**
  - Environment-specific configs (dev, staging, prod)
  - Runtime parameter override capability
  - Config schema validation
  - Secret manager integration (AWS Secrets Manager, K8s Secrets)
  - Default fallback values for optional parameters

**Configuration Schema:**
```yaml
project_name: "brainwatch"
architecture: "lambda"
ingestion:
  batch_size: 1000
  validation_timeout: 300
messaging:
  kafka_brokers: ["localhost:9092"]
  consumer_group: "brainwatch-batch"
  topic_patterns:
    eeg_raw: "eeg.raw"
    ehr_updates: "ehr.updates"
    features_realtime: "features.realtime"
    alerts_anomaly: "alerts.anomaly"
processing:
  batch:
    spark_master: "local[*]"
    app_name: "brainwatch-batch"
    log_level: "WARN"
  streaming:
    checkpoint_dir: "/tmp/spark-checkpoints"
    output_mode: "append"
serving:
  cassandra_hosts: ["localhost"]
  mongodb_uri: "mongodb://localhost:27017"
```

---

### 2. Contract Layer (`src/brainwatch/contracts/`)

**Purpose:** Canonical event schemas and validation utilities.

#### Module: `events.py`

**Defined Event Types:**

1. **EEGChunkEvent** — Raw EEG data chunk
   ```python
   @dataclass
   class EEGChunkEvent:
       patient_id: str           # Unique patient identifier
       session_id: str           # Recording session ID
       event_time: str           # ISO8601 timestamp
       site_id: str              # Source site identifier
       channel_count: int        # Number of EEG channels
       sampling_rate_hz: float   # Sampling frequency
       window_seconds: float     # Data window duration
       source_uri: str           # S3/local file URI
   ```

2. **EHREvent** — Electronic Health Record update
   ```python
   @dataclass
   class EHREvent:
       patient_id: str           # Patient reference
       encounter_id: str         # Clinical encounter ID
       event_time: str           # Event timestamp
       event_type: str           # (lab_result, medication, diagnosis)
       source_system: str        # System of origin
       version: int              # Schema version
       payload: dict[str, Any]   # Type-specific data
   ```

3. **FeatureEvent** — Computed anomaly features
   ```python
   @dataclass
   class FeatureEvent:
       patient_id: str
       session_id: str
       window_end: str           # Feature window end time
       anomaly_score: float      # [0.0, 1.0] risk score
       signal_quality_score: float
       feature_values: dict[str, float]  # Raw features used
   ```

4. **AlertEvent** — Anomaly alert
   ```python
   @dataclass
   class AlertEvent:
       patient_id: str
       session_id: str
       alert_time: str
       severity: str             # (low, medium, high, critical)
       anomaly_score: float
       explanation: str          # Human-readable reason
   ```

**Utility Functions:**
- `to_payload(record)` → Convert dataclass to dict for serialization
- `validate_required_fields(payload, required_fields)` → Return list of missing fields

**Week 2+ Enhancements:**
- Schema versioning with backward compatibility
- JSON Schema integration for external validation
- Protocol Buffer / Avro serialization options
- Event enrichment pipeline (add metadata, trace IDs)

---

### 3. Ingestion Layer (`src/brainwatch/ingestion/`)

**Purpose:** Data discovery, metadata profiling, and local subset selection.

#### Module: `eeg_inventory.py` — EEG Metadata Profiling

**Key Functions:**

1. **Service-to-Task Mapping**
   ```python
   SERVICE_TASK_MAP: dict[str, dict[str, list[str]]]
   # Maps (site_id, service_name) → task types (cEEG, rEEG, etc.)
   ```

2. **Metadata Summarization**
   ```python
   def summarize_metadata(csv_paths, min_duration=30.0) -> dict:
       # Aggregates multi-site CSV metadata
       # Outputs:
       # - total_rows, unique_subjects
       # - rows_by_site, rows_by_service
       # - sex_distribution
       # - total_duration_hours
       # - candidate_s3_keys (sample paths)
   ```

3. **S3 Key Builder**
   ```python
   def build_candidate_s3_keys(row: dict) -> list[str]:
       # Constructs expected BIDS-formatted S3 paths
       # Pattern: EEG/bids/{site}/{bids}/ses-{session}/eeg/{file}.edf
   ```

4. **Duration Parsing**
   ```python
   def parse_duration(row: dict) -> float | None:
       # Handles multiple column name variations
       # Returns hours, ignores missing/invalid values
   ```

**CLI Script** (`scripts/profile_eeg_metadata.py`):
```bash
python scripts/profile_eeg_metadata.py \
  --csv /path/to/site1.csv \
  --csv /path/to/site2.csv \
  --output artifacts/week1/eeg_metadata_profile.json \
  --min-duration 30.0
```

**Week 2+ Enhancements:**
- Multi-format ingestion (Parquet, Avro, JSON)
- Real-time metadata streaming (Kafka source)
- Quality score computation (data completeness)
- Duplicate and outlier detection
- Data lineage tracking

#### Module: `subset_manifest.py` — Local Subset Selection

**Purpose:** Create reproducible manifests for development/testing subsets.

**Key Functions:**

1. **Manifest Builder**
   ```python
   def build_subset_manifest(csv_path, max_sessions, target_hours) -> dict:
       # Selects subset balancing:
       # - Session count (max_sessions)
       # - Total duration (target_hours)
       # - Site diversity
       # - Service type balance
   ```

2. **Manifest Writer**
   - Output JSON with: session IDs, site mappings, download URIs

**CLI Script** (`scripts/build_local_subset_manifest.py`):
```bash
python scripts/build_local_subset_manifest.py \
  --csv metadata.csv \
  --output artifacts/week1/eeg_subset_manifest.json \
  --max-sessions 12 \
  --target-hours 8
```

**Week 2+ Enhancements:**
- Stratified sampling (by site, service, patient demographics)
- Temporal coverage balancing
- Ground-truth anomaly label injection
- Manifest validation (data existence checks)
- Incremental manifest updates

---

### 4. Processing Layer (`src/brainwatch/processing/`)

**Purpose:** Apache Spark pipeline scaffolds for batch and streaming analytics.

#### Module: `spark_pipeline.py`

**Week 1 Status:** Structural scaffold (no execution yet)

**Batch Processing Pipeline:**

1. **Bronze Zone (Raw Ingestion)**
   ```python
   def ingest_bronze_eeg(spark, source_path: str) -> DataFrame:
       # Schema: patient_id, session_id, event_time, channels, sampling_rate, source_uri
       # Partition by: date (event_time), site_id
       # Write to: hdfs:///brainwatch/bronze/eeg/
   ```

2. **Silver Zone (Transformation)**
   ```python
   def transform_silver_eeg(spark, bronze_df: DataFrame) -> DataFrame:
       # Operations:
       # - Deduplicate on (patient_id, session_id, event_time)
       # - Add signal_quality_flag (heuristic)
       # - Normalize timestamps (UTC)
       # - Quarantine invalid records
       # Write to: hdfs:///brainwatch/silver/eeg/
   ```

3. **Gold Zone (Business-Ready Features)**
   ```python
   def join_gold_eeg_ehr(spark, silver_eeg: DataFrame, silver_ehr: DataFrame) -> DataFrame:
       # Join operations:
       # - Left outer join: eeg → ehr on (patient_id, time range)
       # - Watermark: EEG ±10 min, EHR ±30 min
       # - Feature engineering: rolling statistics, lab proxies
       # Write to: hdfs:///brainwatch/gold/features/
   ```

**Streaming Processing (Speed Layer):**

1. **Real-Time EEG-EHR Join**
   ```python
   def stream_eeg_ehr_join(spark, kafka_servers: str):
       # Sources:
       # - eeg.raw topic (stream 1)
       # - ehr.updates topic (stream 2)
       
       # Operations:
       # - Watermark EEG: 10 minutes
       # - Watermark EHR: 30 minutes
       # - Stream-stream join on (patient_id, event_time ± 5 min)
       # - Output mode: Append
       # - Checkpoint: /tmp/spark-checkpoints/eeg-ehr-join
   ```

2. **Anomaly Scoring & Alert Generation**
   ```python
   def stream_anomaly_detection(spark, features_df: StreamingDF):
       # Rules-based classification (see serving/anomaly_rules.py)
       # Output 1: features.realtime topic
       # Output 2: alerts.anomaly topic
       # Sink: Cassandra/MongoDB with exactly-once semantics
   ```

**Week 2-4 Implementation Plan:**
- Week 2: Kafka ingestion layer, EEG/EHR schema parsing
- Week 3: Bronze → Silver → Gold pipeline execution
- Week 4: Streaming pipeline with watermarks and joins
- Week 5: Performance optimization, caching strategies
- Week 6: End-to-end integration and hardening

**Spark Skill Coverage Matrix:**

| Requirement | Implementation | Target Week |
|-------------|-----------------|-------------|
| **Window Functions** | Rolling patient/session metrics | Week 3 |
| **Pivot/Unpivot** | EHR lab features wide→long | Week 3 |
| **Custom Aggregation** | Signal quality rollups | Week 3 |
| **Multiple Transformations** | Chained Bronze→Silver→Gold | Week 3 |
| **UDFs** | EEG feature extraction, severity classification | Week 4 |
| **Broadcast Joins** | Patient dimension + diagnosis dict | Week 3 |
| **Sort-Merge Joins** | Large EEG-EHR historical joins | Week 3 |
| **Optimization** | Partition pruning, caching, explain plans | Week 4 |
| **Streaming** | Watermarks, checkpointing, output modes | Week 4 |
| **Advanced Analytics** | Time-series anomaly trends | Week 5 |

---

### 5. Serving Layer (`src/brainwatch/serving/`)

**Purpose:** Rule-based anomaly classification and alert generation.

#### Module: `anomaly_rules.py`

**Current Implementation:** Rule registry and classification framework

**Rule Categories:**

1. **Signal Quality Rules**
   ```python
   @anomaly_rule("signal_quality_low")
   def check_signal_quality(feature_event: FeatureEvent) -> bool:
       return feature_event.signal_quality_score < 0.6
   ```

2. **Amplitude Rules**
   ```python
   @anomaly_rule("amplitude_abnormal")
   def check_amplitude(features: dict[str, float]) -> bool:
       # Detects flat-line, excessive noise, electrode failures
   ```

3. **Frequency Domain Rules**
   ```python
   @anomaly_rule("frequency_abnormal")
   def check_frequency_composition(features: dict[str, float]) -> bool:
       # Delta, theta, alpha, beta band abnormalities
   ```

4. **Spatiotemporal Rules**
   ```python
   @anomaly_rule("seizure_pattern_detected")
   def check_seizure_pattern(session_features: dict) -> bool:
       # Multi-channel spike detection, phase locking
   ```

**Severity Classification**
```python
def classify_severity(
    anomaly_flags: list[str],
    anomaly_score: float
) -> str:
    # Returns: low, medium, high, critical
    # Based on flag count and anomaly_score threshold
```

**Week 2+ Enhancements:**
- ML-based classification (Spark MLlib Random Forest)
- Temporal pattern recognition (HMM-based state detection)
- Patient-specific baselines (adaptive thresholds)
- Alert aggregation and deduplication
- False positive filtering with clinical context

---

## Week-by-Week Implementation Roadmap

### Week 1: Foundation & Architecture ✅ **COMPLETE**

**Deliverables:**
- [x] Architecture decision document (Lambda vs Kappa)
- [x] EEG metadata profiler (`eeg_inventory.py`)
- [x] Subset manifest builder (`subset_manifest.py`)
- [x] Event contracts (`contracts/events.py`)
- [x] Spark pipeline scaffold (`processing/spark_pipeline.py`)
- [x] Anomaly rule framework (`serving/anomaly_rules.py`)
- [x] Kubernetes baseline manifests
- [x] Configuration template
- [x] Unit tests (5 test cases)
- [x] Metadata profile artifact
- [x] Subset manifest artifact

**Testing:**
```bash
pytest tests/
# Expected: 5 passed
```

---

### Week 2: Ingestion & Messaging

**Focus:** Kafka integration, EEG/EHR data ingestion

**Deliverables:**
- [ ] Kafka producer (Python): EEG events → `eeg.raw` topic
- [ ] Kafka producer (Python): EHR events → `ehr.updates` topic
- [ ] CSV/Parquet parser with schema validation
- [ ] Event serialization (JSON, Avro)
- [ ] Batch ingestion script with error handling
- [ ] Real-time ingestion with Kafka Streams integration
- [ ] Integration tests (10+ test cases)
- [ ] Error handling and dead-letter queue setup

**Key Files to Create:**
```
src/brainwatch/ingestion/
├── kafka_producer.py          # Event publishing
├── csv_parser.py              # CSV → Event contracts
├── parquet_reader.py          # Parquet support
└── error_handler.py           # Dead-letter queue management

scripts/
├── ingest_eeg_batch.py        # Batch EEG loader
└── ingest_ehr_batch.py        # Batch EHR loader
```

**Testing:**
- Unit tests: CSV parsing, schema validation
- Integration tests: Kafka producer/consumer round-trip
- Data quality: Row count, schema compliance

---

### Week 3: Batch Processing Layer (Bronze → Silver → Gold)

**Focus:** PySpark ETL pipelines for historical data processing

**Deliverables:**
- [ ] Bronze layer: Raw EEG/EHR ingestion into Parquet
- [ ] Silver layer: Deduplication, quality flags, normalization
- [ ] Gold layer: EEG-EHR joins, feature engineering
- [ ] Window functions: Rolling statistics (chunk count, channel stats)
- [ ] Pivot operations: EHR labs wide → feature table long
- [ ] Custom aggregations: Signal quality scores
- [ ] Partition strategy: By date and site
- [ ] Caching strategy: Hot silver tables
- [ ] Performance: Explain plans for all major jobs
- [ ] Tests: 15+ test cases with sample data

**Key Files to Create:**
```
src/brainwatch/processing/
├── bronze_loader.py           # Raw ingestion
├── silver_transformer.py       # Dedup, validation
├── gold_joiner.py             # EEG-EHR joins
├── feature_engineer.py        # Rolling stats, pivots
└── spark_utils.py             # Utility functions

tests/
├── test_bronze.py
├── test_silver.py
├── test_gold.py
└── test_features.py
```

**SQL-style Operations (PySpark):**
```python
# Window function example
from pyspark.sql import Window

chunk_stats = df.withColumn(
    "rolling_chunks",
    F.count("chunk_id").over(
        Window.partitionBy("patient_id")
               .orderBy("event_time")
               .rangeBetween(-600, 0)  # 10 min window
    )
)

# Pivot example
ehr_wide = ehr_long.pivot("lab_code").agg(F.first("lab_value"))

# Custom aggregation (GroupedAgg)
signal_quality = df.groupby("patient_id", "session_id").agg(
    custom_signal_quality_udf(F.collect_list("amplitude"))
)
```

**Spark Configuration:**
```python
spark = SparkSession.builder \
    .appName("brainwatch-batch") \
    .config("spark.sql.shuffle.partitions", "200") \
    .config("spark.sql.adaptive.enabled", "true") \
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
    .getOrCreate()
```

---

### Week 4: Streaming Layer (Speed Layer) & Anomaly Detection

**Focus:** Real-time EEG-EHR joins, watermarking, anomaly scoring

**Deliverables:**
- [ ] Kafka source reader (Structured Streaming)
- [ ] Watermarking: EEG (10 min), EHR (30 min)
- [ ] Stream-stream join: EEG ↔ EHR on (patient_id, time)
- [ ] Stateful aggregations: Rolling window metrics
- [ ] UDFs: Feature extraction, severity classification
- [ ] Checkpoint directory setup and recovery
- [ ] Output mode: Append with exactly-once semantics
- [ ] Sink: Kafka (`features.realtime`, `alerts.anomaly`)
- [ ] Tests: 10+ streaming test cases (using MemoryStream)

**Key Files to Create:**
```
src/brainwatch/processing/
├── streaming_join.py          # EEG-EHR streaming join
├── streaming_aggregation.py   # Stateful aggregations
├── streaming_sink.py          # Kafka output writer
└── udf_registry.py            # Custom Spark UDFs

scripts/
└── run_streaming_pipeline.py  # Long-running streaming job
```

**Streaming Code Pattern:**
```python
from pyspark.sql.functions import *
from pyspark.sql.types import *

eeg_stream = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9092") \
    .option("subscribe", "eeg.raw") \
    .load()

ehr_stream = spark.readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9092") \
    .option("subscribe", "ehr.updates") \
    .load()

# Parse JSON and add watermarks
eeg_df = eeg_stream.select(from_json(col("value"), eeg_schema).alias("data")).select("data.*") \
    .withWatermark("event_time", "10 minutes")

ehr_df = ehr_stream.select(from_json(col("value"), ehr_schema).alias("data")).select("data.*") \
    .withWatermark("event_time", "30 minutes")

# Stream-stream join
joined = eeg_df.join(
    ehr_df,
    expr("""
        eeg.patient_id = ehr.patient_id AND
        ehr.event_time BETWEEN eeg.event_time - INTERVAL 5 MINUTES
                          AND eeg.event_time + INTERVAL 5 MINUTES
    """),
    "leftOuter"
)

# Write to Kafka
query = joined.select(to_json(struct("*")).alias("value")) \
    .writeStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", "localhost:9092") \
    .option("topic", "features.realtime") \
    .option("checkpointLocation", "/tmp/checkpoints/eeg-ehr-join") \
    .outputMode("append") \
    .start()

query.awaitTermination()
```

---

### Week 5: Serving Layer & Observability

**Focus:** NoSQL serving, dashboards, alert deduplication

**Deliverables:**
- [ ] Cassandra/MongoDB schema and table design
- [ ] Alert writer with exactly-once guarantee
- [ ] Patient state lookup service (FastAPI)
- [ ] Alert aggregation (suppress duplicates within time window)
- [ ] Prometheus metrics exporter
- [ ] Grafana dashboard: Real-time alerts, lag monitoring
- [ ] Structured logging (JSON format)
- [ ] Tests: 8+ serving layer tests

**Key Files to Create:**
```
src/brainwatch/serving/
├── cassandra_writer.py        # Alert persistence
├── mongodb_writer.py          # Alternative store
├── query_service.py           # FastAPI lookup API
├── metrics_exporter.py        # Prometheus integration
└── alert_deduplicator.py      # Duplicate suppression

infra/
├── cassandra-schema.cql
├── mongodb-schema.js
└── prometheus-config.yml
```

**Sample FastAPI Endpoint:**
```python
from fastapi import FastAPI, HTTPException
from src.brainwatch.serving.query_service import AlertStore

app = FastAPI()

@app.get("/alerts/{patient_id}")
async def get_recent_alerts(patient_id: str, hours: int = 1):
    alerts = AlertStore.get_alerts(patient_id, hours)
    return {"patient_id": patient_id, "alerts": alerts}

@app.get("/patient-state/{patient_id}")
async def get_patient_state(patient_id: str):
    state = AlertStore.get_patient_state(patient_id)
    return state
```

---

### Week 6: End-to-End Integration & Hardening

**Focus:** Full system test, performance tuning, documentation

**Deliverables:**
- [ ] End-to-end integration test (1000+ synthetic records)
- [ ] Kubernetes deployment manifests (updated)
- [ ] ConfigMap templates (all services)
- [ ] Secret management integration
- [ ] Performance benchmarks: Latency, throughput
- [ ] Load testing: 10k records/min
- [ ] Documentation: API, operational runbook
- [ ] Demo script with synthetic data
- [ ] Project report (architecture, results, lessons learned)

**Key Files to Create:**
```
infra/k8s/
├── brainwatch-namespace.yaml
├── brainwatch-batch-job.yaml
├── brainwatch-streaming-job.yaml
├── query-service-deployment.yaml
├── configmap-*.yaml
└── secret-*.yaml

docs/
├── DEPLOYMENT.md              # Kubernetes setup
├── API.md                     # Query service API
├── OPERATIONAL_RUNBOOK.md     # Troubleshooting guide
└── FINAL_REPORT.md            # Project summary
```

---

## Code Quality Standards

### Testing Strategy

**Test Pyramid:**
- **Unit Tests (70%):** Individual functions, transformations
- **Integration Tests (20%):** Kafka round-trip, Spark jobs
- **End-to-End Tests (10%):** Full pipeline with synthetic data

**Test Execution:**
```bash
# Unit tests
pytest tests/unit -v

# Integration tests (requires Kafka)
pytest tests/integration -v -m integration

# All tests
pytest tests/ -v --cov=src/brainwatch --cov-report=html
```

**Minimum Coverage:** 80% across core modules

### Code Style

- **Python Version:** 3.11+
- **Type Hints:** Mandatory for all functions
- **Linting:** flake8, mypy
- **Formatting:** Black (line length 100)
- **Docstrings:** Google-style for public APIs

### Git Workflow

**Branch Naming:**
- Feature: `feature/short-description`
- Bugfix: `bugfix/short-description`
- Hotfix: `hotfix/short-description`

**Commit Message Format:**
```
<type>(<scope>): <subject>

<body>

<footer>
```

**Example:**
```
feat(ingestion): add Kafka producer for EEG events

- Implement KafkaProducer wrapper with schema validation
- Add exponential backoff for failed sends
- Include integration tests with test Kafka cluster

Closes #12
```

---

## Dependencies Management

### Core Dependencies
```toml
[project]
dependencies = [
    "boto3>=1.34",          # AWS S3 access
    "PyYAML>=6.0",          # Configuration parsing
    "pyspark>=3.5,<4.0",    # Apache Spark (optional)
    "kafka-python>=2.0",    # Kafka client
    "sqlalchemy>=2.0",      # ORM for database
    "fastapi>=0.100",       # REST API framework
    "pydantic>=2.0",        # Data validation
    "prometheus-client",    # Metrics export
]
```

### Development Dependencies
```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov",
    "pytest-asyncio",
    "black",
    "flake8",
    "mypy",
    "isort",
]
spark = [
    "pyspark>=3.5,<4.0",
]
```

---

## Risk Mitigation & Known Issues

### Data Quality Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Inconsistent patient IDs across EEG/EHR | Medium | High | Explicit alignment rules, quarantine unmatched |
| Missing duration fields in metadata | High | Low | Default filtering, logging |
| Schema drift across source systems | Low | High | Bronze layer validation, schema versioning |
| Delayed EHR updates | Medium | Medium | Watermark tolerance (30 min), late-data policy |

### Operational Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Spark resource exhaustion on large joins | Medium | High | Partition pruning, caching, monitoring |
| Kafka broker failure | Low | High | Broker cluster setup, replication factor ≥2 |
| Checkpoint corruption | Low | High | Regular checkpoint backups, recovery plan |
| Cold start latency for new patients | Medium | Low | Pre-warm caches, batch import support |

---

## Monitoring & Alerts

### Key Metrics

**Ingestion Metrics:**
- Records ingested per minute (by source)
- Data validation error rate
- Latency from data arrival to processing (P50, P99)

**Processing Metrics:**
- Batch job duration
- Streaming micro-batch latency
- Records processed per micro-batch
- Checkpoint age (staleness)

**Serving Metrics:**
- Query latency (P50, P99, P999)
- Cache hit rate
- Alert generation rate
- False positive rate (requires manual labeling)

**System Metrics:**
- Spark executor memory usage
- Kafka consumer lag
- NoSQL database connection pool utilization
- Kubernetes pod memory/CPU

### Alert Thresholds

- **Kafka lag > 10k messages** → Page on-call
- **Streaming micro-batch > 60s** → Page on-call
- **Batch job > 4 hours** → Check for errors
- **Query latency P99 > 1s** → Investigate caching
- **Alert generation rate spike > 3σ** → Manual review

---

## Directory Structure (Final)

```
brainwatch/
├── src/brainwatch/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                     # Configuration loader
│   ├── contracts/
│   │   ├── __init__.py
│   │   └── events.py                       # Event schemas
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── eeg_inventory.py                # Metadata profiler
│   │   ├── subset_manifest.py              # Manifest builder
│   │   ├── kafka_producer.py               # (Week 2)
│   │   ├── csv_parser.py                   # (Week 2)
│   │   └── error_handler.py                # (Week 2)
│   ├── processing/
│   │   ├── __init__.py
│   │   ├── spark_pipeline.py               # Pipeline scaffold
│   │   ├── bronze_loader.py                # (Week 3)
│   │   ├── silver_transformer.py           # (Week 3)
│   │   ├── gold_joiner.py                  # (Week 3)
│   │   ├── streaming_join.py               # (Week 4)
│   │   ├── streaming_aggregation.py        # (Week 4)
│   │   ├── feature_engineer.py             # (Week 3-4)
│   │   └── udf_registry.py                 # (Week 4)
│   └── serving/
│       ├── __init__.py
│       ├── anomaly_rules.py                # Rule framework
│       ├── cassandra_writer.py             # (Week 5)
│       ├── mongodb_writer.py               # (Week 5)
│       ├── query_service.py                # (Week 5)
│       └── metrics_exporter.py             # (Week 5)
├── scripts/
│   ├── profile_eeg_metadata.py             # Week 1 ✅
│   ├── build_local_subset_manifest.py      # Week 1 ✅
│   ├── ingest_eeg_batch.py                 # (Week 2)
│   ├── ingest_ehr_batch.py                 # (Week 2)
│   ├── run_batch_pipeline.py               # (Week 3)
│   └── run_streaming_pipeline.py           # (Week 4)
├── infra/k8s/
│   ├── brainwatch-namespace.yaml           # Week 1 ✅
│   ├── brainwatch-batch-job.yaml           # Week 1 ✅
│   ├── brainwatch-streaming-job.yaml       # Week 1 ✅
│   ├── configmap-kafka.yaml                # (Week 2)
│   ├── configmap-spark.yaml                # (Week 3)
│   ├── query-service-deployment.yaml       # (Week 5)
│   └── secret-templates.yaml               # (Week 5)
├── configs/
│   ├── project.example.yaml                # Week 1 ✅
│   ├── dev.yaml                            # (Week 2)
│   ├── staging.yaml                        # (Week 5)
│   └── prod.yaml                           # (Week 6)
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── test_config.py                  # Week 1 ✅
│   │   ├── test_contracts.py               # Week 1 ✅
│   │   ├── test_eeg_inventory.py           # Week 1 ✅
│   │   ├── test_subset_manifest.py         # Week 1 ✅
│   │   ├── test_anomaly_rules.py           # Week 1 ✅
│   │   └── test_*.py                       # (Weeks 2-6)
│   └── integration/
│       ├── test_kafka_round_trip.py        # (Week 2)
│       ├── test_spark_pipeline.py          # (Week 3)
│       ├── test_streaming_join.py          # (Week 4)
│       └── test_end_to_end.py              # (Week 6)
├── artifacts/
│   ├── week1/
│   │   ├── eeg_metadata_profile.json       # Week 1 ✅
│   │   └── eeg_subset_manifest.json        # Week 1 ✅
│   └── benchmarks/                         # (Week 5+)
├── docs/
│   ├── architecture.md                     # Week 1 ✅
│   ├── DEPLOYMENT.md                       # (Week 6)
│   ├── API.md                              # (Week 5)
│   ├── OPERATIONAL_RUNBOOK.md              # (Week 6)
│   └── FINAL_REPORT.md                     # (Week 6)
├── .gitignore
├── README.md                               # Week 1 ✅
├── CODE_PLAN.md                            # ← YOU ARE HERE
├── pyproject.toml                          # Week 1 ✅
└── LICENSE
```

---

## Success Criteria

### Week 1 ✅
- [x] 5+ unit tests passing
- [x] Architecture decision documented
- [x] Metadata profiler produces valid JSON
- [x] Event contracts well-typed and serializable
- [x] Spark scaffold compiles without errors

### Week 2
- [ ] Kafka producers/consumers round-trip events
- [ ] CSV parser handles multi-site metadata
- [ ] 10+ integration tests passing
- [ ] No data loss in publish-subscribe cycle

### Week 3
- [ ] Bronze/Silver/Gold pipelines execute successfully
- [ ] 50k+ records processed in < 5 minutes
- [ ] Explain plans reviewed for all major jobs
- [ ] 95% test coverage in processing module

### Week 4
- [ ] Streaming join operates for 1+ hour without error
- [ ] Watermark delays respected (±5 min tolerance)
- [ ] Checkpoint recovery works on simulated failure
- [ ] Anomaly alerts generated within 30 seconds

### Week 5
- [ ] Query API responds in < 100ms (P99)
- [ ] Alert deduplication eliminates 80%+ duplicates
- [ ] Prometheus metrics scrape without error
- [ ] Grafana dashboard displays all key metrics

### Week 6
- [ ] End-to-end test with 10k synthetic records passes
- [ ] Kubernetes deployment complete and documented
- [ ] Performance benchmarks meet targets
- [ ] Runbook enables new operator onboarding

---

## Appendix: References & Resources

### Apache Spark Documentation
- [Structured Streaming Guide](https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html)
- [DataFrame API](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql.html)
- [Spark SQL Optimization](https://spark.apache.org/docs/latest/sql-performance-tuning.html)

### Kafka
- [Kafka Python Client](https://kafka-python.readthedocs.io/)
- [Structured Streaming + Kafka](https://spark.apache.org/docs/latest/structured-streaming-kafka-integration.html)

### Cassandra / MongoDB
- [Cassandra Python Driver](https://github.com/datastax/python-driver)
- [PyMongo](https://pymongo.readthedocs.io/)

### Kubernetes
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Spark on Kubernetes](https://spark.apache.org/docs/latest/running-on-kubernetes.html)

### Python Development
- [Type Hints (PEP 484)](https://peps.python.org/pep-0484/)
- [Black Code Formatter](https://black.readthedocs.io/)
- [Pytest Best Practices](https://docs.pytest.org/)

---

**Document Version:** 1.0  
**Last Updated:** 2026-05-08  
**Maintainer:** BrainWatch Development Team  
**Status:** Active Development (Week 1 → Week 2)
