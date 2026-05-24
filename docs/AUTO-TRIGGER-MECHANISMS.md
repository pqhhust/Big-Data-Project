# Auto-trigger mechanisms — how things fire on their own

A guide to **every mechanism** that can fire a job without a human pressing a
button. CronJob is the most famous, but it's one of four families. Picking
the wrong family is one of the most common big-data design mistakes.

---

## TL;DR — the four families

| Family | Trigger | Latency | Cost shape | Example tools |
|---|---|---|---|---|
| **Time-based (cron)** | Wall-clock schedule | minutes–hours | Pay per run | K8s CronJob, Airflow schedule, AWS EventBridge schedule |
| **Event-based (push)** | Something happened | sub-second | Pay per event | S3 ObjectCreated, EventBridge, Kafka push, webhooks |
| **Pull / polling** | Loop that checks | seconds | Pay always-on | `while True: sleep(N)`, Kafka consumer poll, Airflow sensor |
| **Continuous (streaming)** | Always running, processes as data arrives | sub-second | Pay always-on | Spark Structured Streaming, Flink, Kafka Streams |

**Picking one:**

- Need to run "every X minutes regardless of data" → **cron**
- Need "as soon as a specific event happens" → **event-based**
- Need to react to changes in a system you don't control (no events API) → **polling**
- Need "continuously process as data arrives" → **streaming**

---

## 1. Kubernetes CronJob — mechanics

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: {name: silver-batch}
spec:
  schedule: "*/15 * * * *"          # every 15 min
  concurrencyPolicy: Forbid          # don't start a 2nd run if previous still running
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  startingDeadlineSeconds: 180       # if missed by >3 min, skip
  jobTemplate:
    spec:
      backoffLimit: 2                # 3 attempts then give up
      activeDeadlineSeconds: 1800    # kill the Job after 30 min
      template:
        spec:
          restartPolicy: OnFailure
          containers: [...]
```

### How it actually fires

1. **`kube-controller-manager`** runs a controller called the **CronJob
   controller**. It wakes up roughly every 10 seconds.
2. On each wake-up it reads every `CronJob` resource in the cluster, parses
   the `schedule` (cron expression), and compares to "now."
3. If the next scheduled time has passed and no Job exists for that slot,
   it **creates a `Job`** (a one-shot K8s resource).
4. The Job controller then schedules a Pod via the regular Pod scheduler.
5. Pod runs to completion; Job records success/failure; CronJob records the
   run in its `status.lastScheduleTime`.

### The fields that bite you

| Field | What it does | Why it matters |
|---|---|---|
| `concurrencyPolicy` | `Allow` (default — pile up), `Forbid` (skip if previous running), `Replace` (kill previous) | If a silver build takes 20 min and you schedule every 15 min, `Allow` floods your cluster |
| `startingDeadlineSeconds` | If the controller is down past this many seconds, skip the missed run | Prevents a backlog of 50 jobs firing at once after the controller comes back |
| `successfulJobsHistoryLimit` | Keep N successful Job records | etcd bloat if you keep them forever |
| `backoffLimit` | Retry the Job N times if it fails | At N+1, the Job is marked failed |
| `activeDeadlineSeconds` | Hard kill timer per Job | Defense against runaway Spark jobs |

### Cron expression cheat-sheet

```
 ┌────── minute       (0–59)
 │ ┌──── hour         (0–23)
 │ │ ┌── day-of-month (1–31)
 │ │ │ ┌── month      (1–12)
 │ │ │ │ ┌── day-of-week (0–6, 0=Sunday)
 │ │ │ │ │
 * * * * *

  "*/15 * * * *"   every 15 minutes
  "0 */2 * * *"    every 2 hours on the hour
  "0 3 * * *"      daily at 03:00
  "0 3 * * 0"      Sundays at 03:00
  "0 9-17 * * 1-5" hourly during business hours, weekdays
```

K8s 1.25+ adds the `timeZone` field — use it. Default is the cluster's local
TZ which is **almost always UTC on managed clusters** — pre-1.25, `0 9 * * *`
fires at 09:00 UTC, not 09:00 your-time.

### When CronJob is wrong

- **You need sub-minute reaction** — controller loop is 10 s, scheduler
  latency adds more. Use streaming instead.
- **You need it to fire on a data event, not a clock** — use event-based.
- **You need a DAG of dependent jobs** — use Airflow / Argo Workflows.

---

## 2. Spark Structured Streaming triggers

Spark's `writeStream.trigger(...)` controls **when the next micro-batch runs**.
Four options, used for very different things:

```python
# Option 1 — processing time (default for us)
query.trigger(processingTime="5 seconds")    # try every 5s; skip if previous still running

# Option 2 — once
query.trigger(once=True)                      # run one batch on all currently-available data, then stop

# Option 3 — available now (Spark 3.3+)
query.trigger(availableNow=True)              # like once=True but breaks work into smaller batches

# Option 4 — continuous (experimental, not for production)
query.trigger(continuous="1 second")          # record-at-a-time, sub-second latency, limited operators
```

### How `processingTime` actually works

Inside the streaming engine there's a **microbatch loop**:

```python
# pseudo-code of Spark's streaming engine
while not stopped:
    batch_start = now()
    if has_new_data():
        batch_df = read_from_source()
        result = plan.execute(batch_df)
        write_to_sink(result)
        commit_offsets()
    elapsed = now() - batch_start
    sleep(max(0, trigger_interval - elapsed))
```

If a batch takes **longer** than the trigger interval, the next batch starts
immediately (no waiting). This is **back-pressure-friendly**: slow batches
just push out the cadence, they don't pile up. Spark UI shows this as
"Trigger Duration" vs "Processing Time."

### Our speed-layer trigger choice

In `src/brainwatch/processing/speed_layer.py` line 244:
```python
query = scored.writeStream.foreachBatch(_write_batch) \
              .outputMode("append") \
              .trigger(processingTime="5 seconds") \
              .start()
```

**Why 5 seconds, not 1 or 60?**

- 1 s: too aggressive — overhead of starting a Spark batch (planning,
  serialization, Cassandra connect) dominates the actual work.
- 60 s: too slow — clinicians waiting for an alert would see a stale
  dashboard.
- **5 s**: amortizes the per-batch overhead, still feels live in the
  dashboard. The 3 s exporter polling on top adds bounded extra latency, so
  end-to-end p95 ≈ 12 s.

### When you'd use `trigger(availableNow=True)`

This is the **modern replacement for `once=True`** and a clever fit for
**incremental batch CronJob workloads.** Instead of writing your own
"read everything since last watermark" code, you just:

```python
spark.readStream.format("delta").load(bronze_path) \
     .dropDuplicates([...]) \
     .writeStream.format("delta") \
     .trigger(availableNow=True) \
     .option("checkpointLocation", "...") \
     .start(silver_path) \
     .awaitTermination()
```

Spark uses the **checkpoint** to find what's new since last run, processes it
in multiple small batches (vs `once=True`'s single big batch), then exits.
Run this from a K8s CronJob every 15 min → poor man's streaming silver.

---

## 3. Event-based triggers (push)

The trigger fires because **something specific happened**, not because the
clock said so.

### 3.1 S3 ObjectCreated → Lambda / EventBridge

```
[new EDF arrives in s3://bronze/edf/...]
        │
        ▼  S3 emits ObjectCreated event
[EventBridge rule] ── matches → [SQS queue]
                                    │
                                    ▼
                            [Lambda or worker pod]
                            processes the new file
```

**Latency:** event reaches the consumer in **~200 ms** typically.

**Why this is great:** you only pay for processing when there's actual work.
No always-on compute.

**Why production hospitals don't use this for EEG:** EDF files arrive 100×
per second. Lambda has a hard limit of 1000 concurrent invocations per region
(soft) — you'd hit it during peak. Use Kafka + Spark Streaming instead.

**Where it's perfect:** the **occasional bulk load** of a back-fill EDF
dataset. New file lands → trigger fires → batch job runs → done.

### 3.2 Kafka — push from broker to consumer

Kafka is **technically pull** — consumers poll the broker — but the **broker
buffers a long poll** so latency feels push-like (~10 ms).

```python
consumer = KafkaConsumer("eeg.raw", bootstrap_servers="kafka:9092")
for msg in consumer:                  # blocks until next message available
    handle(msg)
```

Under the hood:

1. Consumer sends `Fetch` request with `max.wait.ms=500` (default).
2. Broker holds the request open until either:
   (a) there's `min.bytes` of new data, or
   (b) `max.wait.ms` elapses.
3. Returns batch.
4. Consumer commits offset after processing.

This is **long-polling** disguised as push — best of both worlds.

### 3.3 Webhooks

Service A calls `POST https://serviceB/hook` when something happens. The HTTP
request *is* the trigger.

In our stack: **none** (we're a closed pipeline). In real hospitals: the EHR
might POST to your "new admission" webhook → triggers patient-onboarding
flow.

### 3.4 Database triggers / CDC

**Debezium** reads the database's write-ahead log and emits a Kafka event per
row change. Postgres / MySQL / MongoDB / Cassandra (with CDC enabled) all
support this.

```
[INSERT INTO alerts ...]
        │
        ▼ Cassandra writes to commitlog + CDC dir
[Debezium connector] tails CDC dir → emits to Kafka
        │
        ▼
[alerts.cdc topic] → downstream consumers
```

**Why we don't use this today:** our exporter polls Cassandra every 3 s,
which is fine at our volume. **What it would replace:** the polling exporter,
giving sub-second latency for free.

---

## 4. Polling — the lazy default

A loop that asks "is there new work?" at fixed intervals.

```python
# scripts/cassandra_to_s3_exporter.py (us)
while True:
    rows = session.execute("SELECT * FROM alerts LIMIT 5000")
    upload_rollups(rows)
    time.sleep(3)
```

**Pros:** dead simple. No infrastructure needed. Works against any system
that has a query API.

**Cons:**

- **Always-on cost** — even when there's nothing to do.
- **Latency = sleep_interval** in the worst case.
- **Wasteful queries** — most polls return nothing new but still hit the DB.

### When polling is actually right

- The target system has **no events API** (most legacy systems).
- The cost of the query is negligible.
- You don't need sub-second latency.

### Airflow Sensors — polling in disguise

Airflow's `S3KeySensor`, `HttpSensor`, `SqlSensor` are all polling loops
wrapped in DAG-task UI. Useful but they **consume worker slots** while
waiting — modern Airflow has "deferrable operators" that release the slot
between polls.

---

## 5. Continuous streaming — always-on processing

```python
# Spark Structured Streaming — already covered in §2
query = spark.readStream.format("kafka")...writeStream...start()
query.awaitTermination()
```

The pod is up forever. Processes events as they arrive. **This is what our
speed layer does.**

**Other streaming engines, same family:**

- **Apache Flink** — true record-at-a-time, lower latency than Spark
  micro-batch, more complex ops.
- **Kafka Streams** — JVM library, no separate cluster, runs as a JAR.
- **ksqlDB** — SQL on top of Kafka Streams.
- **Apache Beam** — portable API; runs on Flink, Spark, Dataflow.

**Trade-off:** always-on compute. Doesn't fit on a CronJob. You pay 24/7.
But you get sub-second latency.

---

## 6. Workflow orchestrators — DAGs of triggers

When you have **dependent jobs** ("silver depends on bronze, gold depends on
silver"), CronJob alone forces you to schedule each job pessimistically
("silver at :15, gold at :30, hope silver finished by then"). Wrong tool.

### Apache Airflow

```python
with DAG("brainwatch_batch", schedule="*/15 * * * *") as dag:
    bronze = SparkSubmitOperator(task_id="bronze", ...)
    silver = SparkSubmitOperator(task_id="silver", ...)
    gold   = SparkSubmitOperator(task_id="gold",   ...)
    bronze >> silver >> gold       # dependency arrow
```

The Airflow **scheduler** is a process that:

1. Polls the metadata DB for DAGs that are due.
2. Computes the DAG topological order.
3. Schedules each task when its dependencies finish (success/skipped/etc.).
4. Tracks state in the DB. Retries on failure per task.

Used by basically every data team > 5 people.

### Argo Workflows (K8s-native)

```yaml
apiVersion: argoproj.io/v1alpha1
kind: CronWorkflow
metadata: {name: brainwatch-batch}
spec:
  schedule: "*/15 * * * *"
  workflowSpec:
    entrypoint: pipeline
    templates:
      - name: pipeline
        dag:
          tasks:
            - {name: silver, template: spark-submit, arguments: {parameters: [{name: job, value: silver}]}}
            - {name: gold,   template: spark-submit, dependencies: [silver],
               arguments: {parameters: [{name: job, value: gold}]}}
```

**Why K8s teams pick Argo over Airflow:** it's CRDs all the way down. Every
task is a Pod. No separate Airflow DB to operate. Native to your cluster.

### Modern (Prefect, Dagster)

Both move declaration into Python and add type safety, asset-based thinking,
and better dev ergonomics. Picking between them is a team preference.

---

## 7. What our project uses today

| Component | Mechanism | Cadence | Where in code |
|---|---|---|---|
| Speed-layer micro-batch | **Spark `trigger(processingTime="5 seconds")`** | 5 s | `speed_layer.py:244` |
| EHR ghost consumer | Spark streaming, no trigger override | 0–500 ms (long-poll) | `speed_layer.py:162` |
| Cassandra→S3 exporter | **Polling `while True: sleep(3)`** | 3 s | `scripts/cassandra_to_s3_exporter.py` |
| Kafka producer driver | Manual loop, replays bronze | continuous until done | `scripts/kafka_producer_driver.py` |
| **Bronze sync → HDFS** | **K8s CronJob `*/5 * * * *`** | every 5 min | `infra/cloud/k8s-overlays/batch-on-hdfs.yaml` (`hdfs-bronze-loader`) |
| **Silver + gold rebuild** | **K8s CronJob `2-59/5 * * * *`** (2 min offset) | every 5 min | `infra/cloud/k8s-overlays/batch-on-hdfs.yaml` (`spark-batch-hdfs`) |
| Cassandra schema init | K8s `Job` (one-shot) | once at deploy | `real-pipeline.yaml:9` |
| HDFS dir bootstrap | K8s `Job` (one-shot) | once at deploy | `hdfs.yaml` |
| Dashboard refresh | Grafana panel `refresh: "30s"` (browser-side poll) | 30 s | `grafana-*.json` |

End-to-end alert latency:

```
EDF event in Kafka → speed-layer micro-batch:    0–5  s   (Spark trigger)
        → Cassandra insert:                      ~50 ms   (foreachBatch)
        → exporter polls Cassandra:              0–3  s   (sleep)
        → S3 upload:                             ~200 ms
        → Grafana panel polls S3:                0–30 s   (refresh)
        ─────────────────────────────────────────────
        worst case p99:                          ~38 s
        typical p50:                             ~12 s
```

The two biggest contributors are **polling intervals** — the exporter (3 s)
and the dashboard (30 s). If you wanted sub-5 s end-to-end, replace both
with push: Cassandra CDC + Grafana live streaming.

---

## 8. What we'd add for production

### 8.1 K8s CronJob for periodic batch — **SHIPPED**

Already deployed to the EKS cluster in
`infra/cloud/k8s-overlays/batch-on-hdfs.yaml` — every 5 min the loader syncs
the bronze PVC to HDFS, and 2 min later the Spark batch rebuilds silver +
gold on HDFS:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: {name: spark-batch-hdfs, namespace: brainwatch}
spec:
  schedule: "2-59/5 * * * *"            # offset by 2 min from the loader
  concurrencyPolicy: Forbid             # never overlap
  startingDeadlineSeconds: 180          # skip missed slots if scheduler stalled
  successfulJobsHistoryLimit: 2
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 600
      template:
        spec: { /* … pulls wheel from S3, runs run_batch.py against LAKE_BASE=hdfs://… */ }
```

```bash
# Inspect:
kubectl -n brainwatch get cronjobs
kubectl -n brainwatch get jobs                # CronJob children
kubectl -n brainwatch logs job/spark-batch-hdfs-<suffix>

# Trigger an ad-hoc run any time:
kubectl -n brainwatch create job --from=cronjob/spark-batch-hdfs adhoc-$(date +%s)
```

Cost: each run is ~35–60 s of compute on a single t3.xlarge worker; ~$1/day
extra at every-5-min cadence.

### 8.2 S3 event → restart speed layer on EDF backfill

When a back-fill EDF batch arrives in S3, fire the bronze writer once:

```
S3 bucket [PutObject events] → EventBridge rule (s3://bronze/edf/* prefix)
                                  → SQS queue
                                  → K8s Job (created via Argo Events) →
                                    spark-submit edf_to_bronze.py
```

This is the **modern serverless ingest** pattern. Cost: ~$0 unless something
actually arrives.

### 8.3 Cassandra CDC → push exporter (replace polling)

Enable CDC on the `alerts` table:
```sql
ALTER TABLE brainwatch.alerts WITH cdc=true;
```

Run **Debezium for Cassandra** as a Kafka Connect connector. Every INSERT
emits a Kafka event on `cdc.brainwatch.alerts`. The exporter becomes:

```python
for msg in consumer:
    upload_rollup_delta(msg)        # sub-second, no SELECT *
```

End-to-end alert latency drops to **~2 s p99** (vs ~38 s).

### 8.4 Argo Workflows for the batch DAG

```
silver_eeg ──┐
             ├──► gold_patient_features ──► export_gold ──► refresh_dashboard
silver_ehr ──┘
```

Fire on schedule + on-demand via `argo submit`. Replaces the single
`run_batch.py` with parallel-where-possible task graph.

---

## 9. Choosing the right mechanism — decision tree

```
Need to react to something?
├─ Yes
│  ├─ "Something" is wall-clock time
│  │  ├─ Standalone job              → K8s CronJob
│  │  └─ Multiple dependent jobs     → Airflow / Argo Workflows
│  ├─ "Something" is a data event you can subscribe to
│  │  ├─ S3 object created          → S3 events + EventBridge + Lambda/Job
│  │  ├─ Row in Kafka topic         → Kafka consumer (push-via-long-poll)
│  │  ├─ DB row changed             → Debezium CDC → Kafka
│  │  └─ HTTP from another system   → Webhook endpoint
│  └─ "Something" is in a system with no events API
│     └─ Polling loop                → while True: check + sleep
└─ No, I just need to keep processing as fast as data arrives
   ├─ Latency budget > 1 s          → Spark Structured Streaming (micro-batch)
   └─ Latency budget < 100 ms        → Flink (record-at-a-time)
```

---

## 10. Defense-grade answers

If asked "how would you auto-trigger your batch path?":

> "Three options, in order of effort and capability. **Smallest:** a K8s
> CronJob at `*/15 * * * *` with `concurrencyPolicy: Forbid` running
> `run_batch.py --incremental`; the manifest is at
> `infra/k8s/spark-batch-cronjob.yaml`. **Medium:** Argo Workflows or
> Airflow if we want a DAG of dependent batch jobs with retries and
> visibility. **Largest:** Spark Structured Streaming with
> `trigger(availableNow=True)` from a CronJob — gives us 'streaming
> medallion' semantics without paying for always-on compute."

If asked "why doesn't your exporter use events instead of polling?":

> "It easily could — Cassandra CDC + Debezium would push every alert as a
> Kafka event, and the exporter becomes a Kafka consumer with sub-second
> latency instead of 3 s polling. We chose polling because (a) at our
> volume, `SELECT *` every 3 s on a single table is trivial, (b) CDC adds
> a Kafka Connect cluster as an operational dependency, and (c) the 3-second
> dashboard refresh is the bigger latency contributor anyway, so optimizing
> the exporter wouldn't move the user-visible number. In production with
> 10× the alert volume, we'd switch to CDC."

---

*See also: `REAL-VS-DEMO.md` §4.7 (the polling exporter and what replaces it
in production), `STUDY-GUIDE.md` §8 (K8s deploy), `QA-BANK.md` §11 (K8s
questions).*
