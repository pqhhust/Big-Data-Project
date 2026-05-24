# Real-life vs demo — how a dynamic Lambda system actually works

A comprehensive comparison of **what BrainWatch ships today** vs **what a
real hospital production deployment would do** at every layer, so you can
answer "is it actually dynamic?" with depth.

> **Short version:** Our **speed layer is genuinely real-time** (it would
> survive a production audit unchanged); our **batch path is now dynamic**
> via K8s CronJobs (every 5 min). The remaining gap to "real hospital" is
> moving to streaming bronze→silver→gold on Delta Lake (Lakehouse pattern).
> Demo cost stays ~$1/month when paused.

---

## Table of contents

1. [Why this question matters](#1-why-this-question-matters)
2. [What "dynamic" means at each layer](#2-what-dynamic-means-at-each-layer)
3. [Side-by-side: demo vs production](#3-side-by-side-demo-vs-production)
4. [Per-layer deep dive](#4-per-layer-deep-dive)
5. [Components production adds that we don't have](#5-components-production-adds)
6. [Failure modes and how each system recovers](#6-failure-modes)
7. [The operational reality (people + cost)](#7-the-operational-reality)
8. [Migration path: demo → production](#8-migration-path-demo--production)
9. [What to say at defense](#9-what-to-say-at-defense)

---

## 1. Why this question matters

A hospital with 200 EEG monitors generates **~500 EDF chunks per second,
continuously, forever**. Patient admissions, lab results, medication orders
hit the EHR 24/7. The system that ingests all of this is fundamentally
different from one that downloads a fixed 8.5 GiB cohort once and runs the
pipeline against it.

The Lambda architecture **is the same** — batch layer + speed layer + serving
layer. What changes is the **cadence at which each layer wakes up, and what
"new data" looks like at the boundary.**

---

## 2. What "dynamic" means at each layer

"Dynamic" is not one thing. It means a different thing at each layer:

| Layer | "Dynamic" means | Cadence in real life |
|---|---|---|
| Ingest | New events arrive without human action | continuous (per EDF chunk, per HL7 message) |
| Bronze | Append-only, never blocks ingest | continuous |
| Silver | Reflects bronze within N minutes | streaming (Delta) or 15-min CronJob |
| Gold | Reflects silver within N hours | hourly or nightly CronJob |
| Speed | Sub-minute alerts on the live signal | continuous, 5–30 s windows |
| Serving | Queryable as data arrives | continuous |
| Dashboard | Updates without a page reload | 3–30 s refresh |

Our demo is **continuous at speed + serving + dashboard**, **one-shot at
ingest + bronze + silver + gold**. Real production is **continuous everywhere
except gold** (which is usually scheduled because it does expensive aggregates).

---

## 3. Side-by-side: demo vs production

| Concern | Our demo today | Real hospital production |
|---|---|---|
| **Data source** | Static BDSP S3 — 8.5 GiB downloaded once | Live EDF stream from Natus / Persyst / Nihon Kohden bedside monitors |
| **EDF arrival** | `scripts/download_real_edf.py` runs once | Each monitor writes an EDF chunk every ~10 s; a vendor "stream gateway" pushes them to a queue |
| **EHR arrival** | `scripts/build_real_ehr.py` generates synthetic events keyed to real patients | HL7 v2 messages (ADT, ORU, ORM) or FHIR resources pushed by the hospital EHR (Epic, Cerner) via an interface engine (Mirth, Rhapsody) |
| **Bronze ingest** | `edf_to_bronze.py` (offline) + `hdfs-bronze-loader` CronJob every 5 min (PVC → HDFS) | Kafka Connect or a per-device Python producer publishes to `eeg.raw`; a Spark Structured Streaming job writes to bronze on Delta Lake |
| **Bronze format** | JSONL files per partition | Delta Lake / Apache Iceberg / Hudi (supports streaming upserts) |
| **Silver build** | **`spark-batch-hdfs` CronJob every 5 min** on HDFS | (a) 15-min CronJob, or (b) Spark Structured Streaming with checkpointed micro-batches |
| **Gold build** | Same CronJob — silver and gold in one pass | Nightly CronJob + incremental hourly refresh |
| **Speed layer** | Continuous, Kafka→Spark→Cassandra (already production-grade) | Same — this part doesn't change |
| **Cassandra** | Single node, RF=1 | Multi-AZ cluster, RF=3, repair scheduled |
| **Kafka** | Single broker | 3+ brokers across AZs, RF=3, MirrorMaker to DR region |
| **Schema** | Hardcoded in `contracts/events.py` | Schema Registry (Confluent or Apicurio), backward-compatible evolution rules |
| **Exporter → S3** | Polling every 3 s | Same pattern, or push from `foreachBatch` directly to S3 |
| **Dashboard** | Grafana → S3 (no auth) | Grafana behind SSO (Okta/Azure AD), per-clinician permissions, audit logged |
| **Compute scheduling** | We start/stop the cluster by hand | EKS managed node group with cluster autoscaler; HPA on speed-layer; nightly batch nodes spin up via Karpenter |
| **Storage** | EBS gp3 per pod | Same, plus S3 for cold tier, plus Glacier for >1y archive |
| **Disaster recovery** | EBS snapshots in one region | Cross-region snapshot replication; warm-standby cluster |
| **Compliance** | None | HIPAA: BAA with AWS, KMS encryption (key per tenant), CloudTrail audit logs, PHI tokenization at boundary |
| **Identity / access** | AWS root key in env vars | IRSA (IAM-roles-for-service-accounts), short-lived tokens, no long-lived secrets in pods |
| **Observability** | Print to stdout, `kubectl logs` | Prometheus + Grafana + Loki + Tempo (or Datadog); SLO dashboards; PagerDuty |
| **Failure recovery** | Restart the pod by hand | StatefulSet PodDisruptionBudget + Karpenter consolidation; runbooks; automatic re-attach via PVC |
| **Schema change** | Edit Python, redeploy | Rolling schema migration via Schema Registry compatibility rules |
| **Cost** | $0.40/h running, $1/mo paused | $5k–$50k/month per hospital depending on bed count |
| **People** | 5 students, ad-hoc | 2 platform engineers + 1 SRE on-call per shift + 1 clinical informaticist |

---

## 4. Per-layer deep dive

### 4.1 Data source

**Demo:** A fixed S3 corpus. The "stream" is `scripts/kafka_producer_driver.py`
replaying bronze JSONL files into Kafka at 150 events/sec. This is the **demo
substitute for a live device stream**.

**Real:** Bedside EEG monitors emit EDF chunks via vendor protocols:

- **Natus** monitors write EDF to a shared SMB drive; a gateway tails the
  directory.
- **Persyst** uses a proprietary HTTP API; a gateway polls.
- **Modern (post-2020)** systems support **HL7 v2 messaging** or
  **DICOM-Waveform** over MLLP.

The gateway turns these into events on `eeg.raw`. EHR events arrive via an
**interface engine** (Mirth Connect, Rhapsody) translating HL7 v2 (ADT^A04
admission, ORU^R01 lab result, ORM^O01 med order) into FHIR-shaped JSON on
`ehr.updates`.

**What you'd change in our code:** swap `kafka_producer_driver.py` for a
collection of small **vendor adapters**:

```python
# scripts/adapters/natus_smb_adapter.py
def watch_directory(path, kafka_producer):
    for new_edf in tail_smb_directory(path):
        chunks = chunk_edf_into_10s_windows(new_edf)
        for chunk in chunks:
            kafka_producer.send("eeg.raw", chunk.to_json())
```

One adapter per vendor; bronze and downstream stay identical.

### 4.2 Ingest → Bronze

**Demo:** `edf_to_bronze.py` is a Python CLI you run once. It opens every EDF
with MNE, computes per-window features, writes JSONL.

**Real:** A **Spark Structured Streaming job** subscribes to `eeg.raw`, parses
the value, writes to bronze as a **streaming append** on a transactional table
format:

```python
spark.readStream.format("kafka").option("subscribe", "eeg.raw").load() \
     .select(from_json("value", eeg_schema).alias("e")).select("e.*") \
     .writeStream.format("delta") \
     .outputMode("append") \
     .option("checkpointLocation", "...") \
     .start("s3://lake/bronze/eeg")
```

`Delta Lake` matters because the next layer (silver) wants to **stream off
bronze**, which requires per-batch commit metadata. Parquet alone doesn't give
you that.

**Why we don't have this today:** our cohort is finite, so a one-shot
JSONL-writing script is cheaper and simpler. There's nothing to *stream into*
bronze that doesn't already exist.

### 4.3 Bronze → Silver (the most-different layer)

**Demo:** `run_batch.py` reads all bronze, dedups, writes Parquet, exits.

**Real:** Two flavors:

**Flavor A — Incremental CronJob (cheap, simple):**
```yaml
apiVersion: batch/v1
kind: CronJob
metadata: {name: silver-incremental}
spec:
  schedule: "*/15 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: spark
              command: ["spark-submit", "/code/run_batch.py", "--incremental"]
```

The `--incremental` flag reads a **watermark file** (`silver/_state/last.txt`)
that stores the last `ingestion_date` processed, then only reads bronze
partitions newer than that:

```python
last = read_state_file()  # e.g. "2026-05-22"
new_bronze = spark.read.parquet(bronze_path) \
                  .filter(F.col("ingestion_date") > last)
# ... dedup + write ...
write_state_file(today_iso())
```

Late-arriving bronze (back-dated EDFs) is handled with a **look-back window**:
read partitions from `last - 3 days` so we re-process recent stuff in case it
got updated.

**Flavor B — True streaming silver (Delta Lake Lakehouse):**
```python
spark.readStream.format("delta").load("s3://lake/bronze/eeg") \
     .dropDuplicates(["patient_id", "session_id", "event_time"]) \
     .withColumn("quality_flag", quality_udf(...)) \
     .writeStream.format("delta") \
     .outputMode("append") \
     .partitionBy("site_id", "ingestion_date") \
     .option("checkpointLocation", "s3://lake/_chk/silver") \
     .start("s3://lake/silver/eeg")
```

Silver is always seconds behind bronze. This is what Databricks customers do
by default.

**Trade-off:** Flavor A is 1 day of work, $30/mo extra compute. Flavor B is
1 week of work and adds Delta Lake as a major dependency (jars, schema
evolution, vacuum jobs). Most teams start with A, migrate to B when they
outgrow the cron cadence.

### 4.4 Silver → Gold (incremental aggregates)

**Demo:** Full recompute on every `run_batch.py`.

**Real:** Gold is almost always **incremental**, because the aggregations are
expensive. The pattern:

1. **Pre-aggregate** in silver: keep daily per-patient rollups.
2. **Compose** in gold: re-aggregate the rollups, not the raw rows.
3. **Materialize** common queries: pre-compute "alerts by hour by site for
   the last 30 days" because the dashboard wants it.

Cadence: **hourly CronJob** for hot rollups (today/this week), **nightly
CronJob** for cold rollups (full historical).

If you've adopted Flavor B (streaming silver), then gold uses Delta's **MERGE
INTO** for streaming upserts. This is the bleeding edge.

### 4.5 Speed layer (the one part that's already production-grade)

This is the layer that doesn't change much from demo to real life. You'd
add:

1. **`maxOffsetsPerTrigger` tuning** based on real burst rates (currently 5000).
2. **State store size monitoring** — alert when state > 80% of executor heap.
3. **Backpressure** — Kafka `fetch.max.bytes` and Spark `kafka.consumer.poll.ms`
   tuned to the highest sustained event rate (peak admission hours).
4. **Per-patient rate limiting** — refuse new events for a patient generating
   > N events/sec (a stuck monitor) before they DoS the system.
5. **Exactly-once to a second sink** — emit alerts to a Kafka `alerts.anomaly`
   topic *in addition to* Cassandra, for downstream consumers (paging,
   audit log).

The architectural shape — Kafka source, watermark, windowed agg, UDF score,
`foreachBatch` to Cassandra — is **already what real hospitals run**.

### 4.6 Serving — Cassandra

**Demo:** Single Cassandra node, `RF=1`, no compaction tuning.

**Real:** Multi-AZ Cassandra:

- **`RF=3`** across 3 AZs in the same region.
- **`LOCAL_QUORUM`** on writes (2/3 must ack) for safety.
- **Repair scheduled** weekly (`nodetool repair --full`).
- **Compaction strategy:** `TimeWindowCompactionStrategy` because our alerts
  are time-series — buckets data by time, compaction cost stays bounded.
- **TTL on alerts:** 90 days. Past 90 days, alerts age out of the hot store;
  cold copy stays in S3 (gold/alerts daily snapshot).
- **Hinted handoff + read repair** for transient node failures.

We've done none of this; for a 8.5 GiB demo, a single node with `RF=1` is
fine.

### 4.7 Exporter → S3 → Dashboard

**Demo:** `cassandra_to_s3_exporter.py` polls every 3 s, builds rollups,
uploads JSON.

**Real:** Three changes:

1. **Push, don't poll.** The speed-layer `foreachBatch` writes rollup deltas
   directly to S3 *and* to Cassandra in the same micro-batch. The polling
   exporter is wasteful (it `SELECT *` every 3 s — fine at our scale,
   bad at scale).

2. **Streaming rollups via Materialized Views** or **Apache Pinot**. For
   high-volume "alerts per hour by site," Pinot or Druid do this natively
   with sub-second query latency.

3. **CDN in front of S3.** Real dashboards are accessed from clinician
   workstations across the hospital. CloudFront sits in front of the S3
   bucket → 50 ms anywhere instead of 300 ms.

### 4.8 Dashboard — Grafana

**Demo:** Grafana, no auth, NodePort, Infinity datasource on S3 (no CORS
issues because S3 website serves CORS open).

**Real:**

- **SSO** via Okta / Azure AD → per-clinician identity in every dashboard view.
- **Role-based dashboards:** ICU nurse sees their unit's alerts;
  neurologist sees their patient list; admin sees pipeline health.
- **Alerting:** Grafana → PagerDuty for SLO violations; Grafana → MS Teams
  for clinical alerts.
- **Audit logging:** every dashboard view logged with `(user, patient_id,
  timestamp)` because HIPAA wants "who looked at what, when."

---

## 5. Components production adds

These don't exist in our demo at all:

### 5.1 Schema Registry
Confluent Schema Registry or Apicurio. Stores AVRO / JSON schemas with
**compatibility rules** (`BACKWARD`, `FULL`). Prevents a producer from
shipping a breaking change without a coordinated consumer update. Without
this, you ship a bad schema once and the whole stream breaks.

### 5.2 Data lineage
OpenLineage + Marquez (or Datakin). Tracks "this gold row came from these
silver rows came from these bronze rows came from these Kafka offsets." When
a clinician questions an alert ("why did patient X get flagged at 14:32?"),
you can trace it back to the exact bronze event.

### 5.3 SRE observability stack
- **Prometheus** for metrics (Kafka lag, Spark batch duration, Cassandra
  read latency, p99 alert latency).
- **Loki** for logs (correlated with traces).
- **Tempo** for distributed traces (one trace ID per EDF chunk, end to end).
- **Grafana** for the observability dashboards (separate Grafana instance
  from the clinical one).
- **PagerDuty** for on-call rotation. SLO violations page; clinical alerts
  go through a different path (vendor-specific clinical alerting).

### 5.4 SLOs (Service Level Objectives)
Production teams commit to numbers like:

- **EEG event → alert visible**: p95 < 60 s, p99 < 180 s.
- **Speed layer uptime**: 99.95% / month (≈ 22 min downtime/mo allowed).
- **Dashboard load time**: p95 < 2 s.
- **Data freshness on gold**: ≤ 1 hour for the most recent shift.

Each SLO has an **error budget**. If you burn through your error budget
this week, you pause feature deploys until reliability comes back.

### 5.5 HIPAA / compliance
- **BAA** (Business Associate Agreement) with AWS.
- **PHI tokenization** at the ingestion boundary — `patient_id` is a
  pseudonym; the real MRN lives in a separate token vault.
- **KMS encryption at rest** for every EBS, every S3, every Cassandra column
  family. Customer-managed keys (CMK) per tenant.
- **TLS everywhere** in transit (Kafka SASL/SSL, Cassandra `internode_encryption`).
- **CloudTrail / audit logs** retained 7 years.
- **Penetration tests** annually.

### 5.6 Multi-tenancy
A real BrainWatch-like vendor serves N hospitals. That means:
- **Per-tenant Kafka topics** (`hospital_a.eeg.raw`, `hospital_b.eeg.raw`).
- **Per-tenant Cassandra keyspaces** (or row-level filters).
- **Per-tenant Grafana orgs.**
- **Per-tenant cost attribution.**

### 5.7 Disaster recovery
- **Cross-region EBS snapshot replication** (we currently snapshot to one
  region).
- **Warm standby cluster** in `us-west-2` (we run in `us-east-1`).
- **RPO / RTO targets:** typically RPO 5 min (data loss tolerated), RTO 1 hour
  (downtime tolerated). Our current setup is RPO ∞ (we don't replicate live),
  RTO ~20 min (resume from snapshot).

---

## 6. Failure modes

How each layer fails, and how each system recovers:

| Failure | Demo recovery | Production recovery |
|---|---|---|
| Speed-layer pod dies | StatefulSet restart, replay from Kafka checkpoint | Same + Karpenter spins up replacement node if the cause was node-level + PagerDuty pages if MTTR > 5 min |
| Cassandra node dies | Pod restart; we have RF=1 so the data is *gone* during downtime | RF=3 + hinted handoff → no clinical impact; repair when node returns |
| Kafka broker dies | Stream stalls; producer blocks | RF=3 brokers → leader election picks up; ISR re-balances |
| EBS volume corrupted | Pod CrashLoopBackoff; we'd restore from snapshot | Auto-failover to a replica replica; snapshot used for forensic only |
| Bad bronze JSON | Bronze writer routes to DLQ | Same + DLQ growth alerts SRE within 5 min |
| Schema-breaking producer change | Speed layer crashes, we redeploy | Schema Registry rejects the producer change before deploy; producer rolled back |
| Region down (AZ-level outage) | Everything down; we wait | Failover to multi-AZ replicas; clinical traffic continues |
| Region down (whole region) | Everything down; we wait | DR cluster in second region picks up within RTO |
| Bug in scoring UDF | Bad alerts emitted until we redeploy | Same — there's no clean fix for "logic bug." Mitigation: canary deploys, A/B testing of new scoring logic with clinician feedback. |

---

## 7. The operational reality

### 7.1 People

| | Demo | Production |
|---|---|---|
| Engineers | 5 students | 2 platform engineers + 1 SRE on-call per shift (24/7 coverage = 4 SREs minimum) + 1 clinical informaticist + 1 ML/data scientist + 1 security/compliance engineer |
| On-call rotation | "Ping the lead" | Formal rotation, paid on-call hours, escalation policy, runbooks per alert |
| Deploy cadence | "When we have something" | Daily/weekly, gated by CI + canary + SLO burn-rate checks |

### 7.2 Cost (rough, per hospital)

Demo:
- **Running:** $0.40/h ≈ $290/mo
- **Paused:** $1/mo (5 EBS snapshots + 2 S3 buckets)
- **Per defense run:** $5 (run for ~10 hours total across rehearsal + demo)

Production (one mid-size hospital, ~200 beds):
- **EKS control plane:** $73/mo
- **Compute:** 6× m5.2xlarge baseline × $0.384/h = $1,660/mo
- **Kafka (3× r5.xlarge):** $546/mo
- **Cassandra (3× r5.2xlarge):** $1,090/mo
- **EBS:** ~$200/mo
- **S3 + Glacier:** ~$50/mo (cold tier)
- **NAT + data transfer:** ~$200/mo
- **CloudWatch / Datadog:** ~$300/mo
- **Penetration test annual:** $20k → ~$1,700/mo amortized
- **Total infra:** ~**$5,500/mo** for one hospital
- **People:** ~$1.5M/year for a 5-person platform team
- **Total fully-loaded:** **~$140k/mo per hospital** for vendor margins

(For multi-tenant SaaS, the per-hospital infra cost drops to ~$2k/mo because
of shared capacity. But that requires the multi-tenancy work in §5.6.)

---

## 8. Migration path: demo → production

If you actually wanted to take this to a single real hospital, the staging:

### Stage 1 (weeks 1–2): "Periodic batch"
- Enable `infra/k8s/spark-batch-cronjob.yaml` on EKS, every 15 min.
- Add `--incremental` to `run_batch.py` (watermark file in `silver/_state/`).
- Add a stub HL7 receiver (Mirth Connect on a small EC2) that writes to
  `ehr.updates` instead of `build_real_ehr.py`.
- **Cost delta:** +$30/mo. **Effort:** 1 week.

### Stage 2 (weeks 3–6): "Live EEG"
- Build one vendor adapter (Natus SMB tailer) feeding `eeg.raw`.
- Replace `download_real_edf.py` + `edf_to_bronze.py` with a Spark Structured
  Streaming bronze writer (Delta Lake).
- Wire `_ehr` consumer in `speed_layer.py` to actually do something
  (the noop sink can become a foreachBatch that updates `patient_state`).
- **Cost delta:** +$500/mo (Delta Lake means heavier compute). **Effort:** 1
  month.

### Stage 3 (weeks 7–12): "HA + SRE"
- Multi-AZ Cassandra `RF=3`.
- Multi-AZ Kafka `RF=3`.
- Prometheus + Grafana for ops (separate from clinical Grafana).
- PagerDuty integration.
- Define and instrument the 4 SLOs in §5.4.
- **Cost delta:** +$2k/mo. **Effort:** 6 weeks.

### Stage 4 (months 4–6): "Compliance + DR"
- BAA, KMS encryption everywhere, audit logs, PHI tokenization.
- Cross-region snapshot replication; warm-standby cluster.
- Annual penetration test.
- **Cost delta:** +$2k/mo + $20k one-time.
**Effort:** 3 months including external audit.

### Stage 5 (months 6+): "Multi-tenant"
- Schema Registry, per-tenant topics, per-tenant keyspaces.
- Per-tenant Grafana orgs.
- Per-tenant cost attribution.
- **Effort:** 3+ months.

Total: ~9 months from current demo to production-multi-tenant.

---

## 9. What to say at defense

If asked "is your system dynamic?", structure the answer in three beats:

> **Beat 1 — answer honestly:** "The speed layer is genuinely dynamic — Kafka
> → Spark Structured Streaming → Cassandra → S3 → Grafana, end-to-end every
> few seconds. The batch layer is currently one-shot on demand, which is
> canonical Lambda — Lambda explicitly defines the batch layer as
> *periodic*, not *continuous*."

> **Beat 2 — show you know the upgrade path:** "To make the batch path
> dynamic, the smallest change is enabling our existing CronJob at a 15-min
> cadence and adding a watermark file for incremental processing. The bigger
> change is moving bronze→silver→gold to Spark Structured Streaming on Delta
> Lake — that's the modern Lakehouse pattern."

> **Beat 3 — frame why we shipped it this way:** "We chose the one-shot
> batch because (a) our cohort is finite — 8.5 GiB of fixed BDSP recordings,
> not a live device stream — and (b) we wanted compute cost at zero between
> demos. In a real hospital with live EEG monitors, you'd enable the
> CronJob on day one and migrate to streaming bronze→silver→gold once you
> have the team to operate Delta Lake."

That answer earns full marks because it (1) is **honest**, (2) uses the
**right vocabulary** ("Lambda's batch layer is periodic by definition"),
(3) shows you've thought about the **upgrade path** with specific
technologies, and (4) explains the **engineering trade-off** rather than
pretending the demo is optimal.

If they push further with "but in a hospital you wouldn't run on-demand
batches" — agree, point at §8's Stage 1 (enable the CronJob, 1 week of
work), and note that we have the manifest sitting ready in
`infra/k8s/spark-batch-cronjob.yaml`.

---

*See also: `STUDY-GUIDE.md` §9 (EKS deploy), `QA-BANK.md` §15 (killer
questions including scale-to-10×), `CHEATSHEET.md` (one-pager defense).*
