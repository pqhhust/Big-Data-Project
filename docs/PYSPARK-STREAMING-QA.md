# PySpark Structured Streaming — Q&A Bank for BrainWatch

Every streaming question I can imagine for our speed layer. Grouped by topic
so you can drill the area you're weakest on. Source code that everything
references: `src/brainwatch/processing/speed_layer.py`.

> **How to use:** read the bolded one-line answer first, then the supporting
> detail. If the supporting detail is more than you need, the one-liner is
> enough on its own.

---

## Table of contents

- [A. Structured Streaming fundamentals](#a-structured-streaming-fundamentals)
- [B. Triggers](#b-triggers)
- [C. Output modes](#c-output-modes)
- [D. Watermarks](#d-watermarks)
- [E. Windows](#e-windows)
- [F. State management](#f-state-management)
- [G. Checkpointing](#g-checkpointing)
- [H. Kafka source](#h-kafka-source)
- [I. UDFs](#i-udfs)
- [J. foreachBatch sink](#j-foreachbatch-sink)
- [K. Joins](#k-joins)
- [L. Performance & tuning](#l-performance--tuning)
- [M. Deployment on Kubernetes](#m-deployment-on-kubernetes)
- [N. Observability & debug](#n-observability--debug)
- [O. Errors we actually hit and fixed](#o-errors-we-actually-hit-and-fixed)
- [P. Comparisons](#p-comparisons)

---

## A. Structured Streaming fundamentals

### A.1 What is Structured Streaming?
**A high-level streaming API where you write a DataFrame query and Spark runs
it incrementally as new data arrives.** Built on top of Spark SQL/Catalyst.
Same DataFrame operations as batch (`select`, `filter`, `groupBy`, `join`).
The streaming nature shows up in **sources/sinks**, **triggers**, **output
modes**, and **watermarks**.

### A.2 How is it different from DStreams (the older API)?
- DStreams = RDD of micro-batches. Lower-level, two APIs (batch vs streaming).
- Structured Streaming = DataFrame, **one API for both**. Catalyst can
  optimize the same way. Supports event-time semantics, watermarks,
  exactly-once.
- DStreams is deprecated; new code should be Structured Streaming.

### A.3 Where in our project is Structured Streaming used?
`src/brainwatch/processing/speed_layer.py` — two variants:
- `build_streaming_pipeline` — reads bronze Parquet as a streaming source.
- `build_kafka_streaming_pipeline` — reads Kafka, what runs on EKS.

### A.4 Why streaming and not "batch every 30 s"?
Three reasons our speed layer is genuinely streaming:
1. **State is preserved across batches** — windowed aggregation needs to
   remember the window's running count.
2. **Watermark logic** — Spark drops/cleans up state based on event-time, not
   wall-clock; batch can't reason about event-time across runs.
3. **Latency** — micro-batch overhead is amortized. Cold-starting a Spark
   batch every 30 s would dominate the actual work.

### A.5 What does Spark do with a streaming query under the hood?
1. Compiles the DataFrame plan once (Catalyst).
2. Wraps it in an `IncrementalExecution`.
3. Spins up a thread (`StreamExecution`) that loops:
   read offsets → execute plan on new data → write sink → commit offsets.
4. State is read/written from a **state store** between batches.
5. Checkpoint dir persists offsets + state + commit log for restart-safety.

### A.6 What are the supported sources and sinks?
- **Sources:** Kafka, file (`parquet`/`json`/`csv`/`delta`), rate (test),
  socket (debug).
- **Sinks:** file, Kafka, foreach/foreachBatch, console, memory.
- For arbitrary external systems (Cassandra, Redis, …): use **`foreachBatch`**.

### A.7 What's "exactly-once" in Structured Streaming?
The contract requires three legs:
1. **Replayable source** — Kafka offsets in checkpoint.
2. **Idempotent sink** — your writes must be safe to replay.
3. **Deterministic transforms** — no `hash()` salt, no `current_timestamp()`
   in the query (use event-time).
Our speed layer satisfies all three: Kafka offsets, Cassandra PK upsert,
`zlib.crc32` instead of `hash()`.

### A.8 What can you NOT do in streaming that you can in batch?
- **No `sort` without aggregation** (would require unbounded buffer).
- **No `distinct` without watermark + windowing** (same reason).
- **No `limit` in update/append mode** before aggregation.
- **No multiple aggregations** in append mode after a windowed agg (Spark
  3.4+ relaxed this).

---

## B. Triggers

### B.1 What's a trigger?
**The cadence at which Spark runs the next micro-batch.** Set via
`writeStream.trigger(...)`.

### B.2 What are the trigger options?
```python
trigger(processingTime="5 seconds")    # micro-batch every N seconds (default)
trigger(once=True)                      # run one batch on all available data, exit
trigger(availableNow=True)              # like once, but multiple small batches; Spark 3.3+
trigger(continuous="1 second")          # record-at-a-time, experimental, limited operators
```

### B.3 What did we pick and why?
**`trigger(processingTime="5 seconds")`** in
`speed_layer.py:244`. Five seconds amortizes per-batch overhead (planning,
serialization, Cassandra connect) and still feels live in the dashboard.

### B.4 What happens if a batch takes longer than the trigger?
The next batch starts immediately (no wait). **Triggers don't pile up** —
they're upper-bounded by previous-batch duration. Spark UI shows this as
"Trigger Duration" vs "Processing Time."

### B.5 What's the difference between `once=True` and `availableNow=True`?
- `once=True`: process all available data in **one giant batch** → can OOM.
- `availableNow=True`: process all available data in **multiple bounded
  micro-batches** (respects `maxOffsetsPerTrigger`), then exit. Safer.

### B.6 What's the continuous trigger and why don't we use it?
Record-at-a-time processing with sub-second latency, but:
- **Supports only `map`/`filter`/`select`** — no aggregation, no windowing,
  no join.
- Experimental since Spark 2.3. Not production-grade.

### B.7 Can you change the trigger across restarts?
**Yes, the trigger is not part of the checkpoint.** You can run with
`processingTime="5s"` today and `availableNow=True` tomorrow against the
same checkpoint dir. The query plan is what must stay stable.

---

## C. Output modes

### C.1 What are the three output modes?
- **append** — only new rows since last batch.
- **update** — new + changed rows since last batch.
- **complete** — entire result table on every batch (small results only).

### C.2 Which one are we using?
**`append`** (`speed_layer.py:242`). With `outputMode("append")` and a
watermark, windowed agg results are emitted **once the window closes** (i.e.,
once watermark passes window end).

### C.3 Why did we switch from `update` to `append`?
The `build_streaming_pipeline` variant used `update` so partial window
updates flowed continuously. But the EKS variant has the constraint:
**stream-stream join + windowed aggregation in update mode is not supported
by Spark**. We dropped the stream-stream join, kept the windowed agg, and
switched to append. (Documented lesson learned.)

### C.4 What operations are forbidden in each mode?
| Operation | append | update | complete |
|---|---|---|---|
| Aggregation without watermark | ❌ | ✅ | ✅ |
| Aggregation with watermark | ✅ (delayed) | ✅ | ✅ |
| Stream-stream join | ✅ (with watermark) | ❌ | ❌ |
| Stream-stream join after agg | ❌ | ❌ | ❌ |
| `limit` before agg | ❌ | ❌ | ❌ |

### C.5 In append mode, when does a windowed result actually emit?
**When `watermark > window.end`.** Concretely: window `[10:00:00, 10:00:30]`
emits when the watermark (max event-time − 30 s) exceeds `10:00:30`. That's
why a 30 s window + 30 s watermark means windows emit ~60 s after their
start.

---

## D. Watermarks

### D.1 What is a watermark?
**An event-time threshold.** Spark uses it to bound state and to drop late
data:
```
watermark = max(event_time_seen_so_far) - allowed_lateness
```
Anything older than the watermark is "late and can't affect results."

### D.2 Where is the watermark set in our code?
`speed_layer.py:150`:
```python
eeg = ... .withWatermark("event_time", "30 seconds")
```
30-second allowed lateness on the EEG stream.

### D.3 Why 30 seconds in the Kafka variant but 10/30 minutes in the Parquet variant?
- **Parquet variant** (`build_streaming_pipeline`): bronze files land in batches,
  so events can be many minutes late. 10 min EEG / 30 min EHR allows that.
- **Kafka variant** (EKS demo): events arrive directly from a producer with
  no buffering; latency should be tens of seconds. Tighter watermark = less
  state, faster window emission.

### D.4 What happens to events past the watermark?
**Dropped** in update/append modes. They are not joined, not added to
windowed aggregations. (Complete mode would still accept them but we don't
use complete.)

### D.5 What's the relationship between watermark and state cleanup?
Spark **garbage-collects state** older than the watermark every micro-batch:
- Windowed agg buckets older than watermark are evicted (after emitting
  their final value).
- Stream-stream join state on rows older than watermark is dropped.
This is **the** mechanism that bounds memory in long-running streams.

### D.6 Why do you need a watermark for windowed aggregation?
**Without a watermark, all windows stay open forever** → unbounded state →
OOM in days. Watermark says "windows ending before X are closed; you can
free their state."

### D.7 Why do you need a watermark for stream-stream join?
**Without watermarks on both sides, Spark would buffer the entire stream**
waiting for a match → unbounded state. Watermarks let Spark drop rows that
are too old to ever match.

### D.8 What if you set the watermark too tight?
**Late events drop on the floor.** A 5 s watermark would lose any event
arriving more than 5 s after its event-time, which is most things in a real
pipeline. Underestimating lateness = data loss.

### D.9 What if you set the watermark too loose?
**State grows.** 24-hour watermark means windowed agg state for the whole
day stays in memory. Each window key × hour × counter is a state entry.
Overestimating lateness = OOM eventually.

### D.10 How do you pick the watermark?
Empirically: measure your **p99 event-time-to-arrival lateness** in
production for a week, set watermark to that + a 2× safety margin. For us,
30 s is comfortable because our producer ships events within a few seconds
of generation.

---

## E. Windows

### E.1 What are the three window types?
- **Tumbling**: non-overlapping fixed-size — `window(t, "30 seconds")`.
- **Sliding**: overlapping fixed-size — `window(t, "30 seconds", "15 seconds")`
  (window-size, slide-interval).
- **Session**: gap-based — `session_window(t, "5 minutes")` (closes when no
  events arrive for 5 min).

### E.2 Which one are we using?
**Sliding** (`speed_layer.py:170`):
```python
F.window(F.col("event_time"), "30 seconds", "15 seconds")
```
30 s wide, slides every 15 s → each event falls into **two** overlapping
windows.

### E.3 Why sliding and not tumbling?
**Smoother output.** Tumbling windows of 30 s would emit one row every 30 s
per patient — features would "jump" at boundaries. Sliding gives an updated
view every 15 s with 30 s of context. Trade-off: 2× the state and output volume.

### E.4 What's `win.start` and `win.end`?
The `window` function adds a struct column with `start` and `end` timestamps:
```python
.select(F.col("win.start"), F.col("win.end"), F.col("eeg_chunk_count"))
```
We use `win.end` as the `alert_time` in Cassandra
(`speed_layer.py:229`).

### E.5 How do windows interact with watermark in append mode?
A windowed result emits only **after the watermark passes `window.end`**.
With 30 s window + 30 s watermark, the result for window `[T, T+30]` emits
at approximately `T+60` event-time. This is the **append-mode emission
delay** — sometimes called "watermark+window latency."

### E.6 What if the producer stops sending data?
**The watermark stops advancing.** Without new max event-times, Spark can't
know it's safe to close windows. Closed-but-unemitted windows stay in
state. Solution: send periodic "heartbeat" events, or use `processingTime`
trigger with a configurable `withWatermark(..., delayThreshold)` that uses
processing-time fallback (Spark 3.5+).

### E.7 Why grouping by `patient_id` and `window` together?
The window function gives you **time buckets**; `patient_id` partitions
them by entity. The result is one row per `(patient, window)` — the natural
analytical unit for "what's this patient's score in the last 30 s?"

---

## F. State management

### F.1 What state does our streaming query maintain?
Two kinds:
1. **Aggregation state**: per `(patient_id, window)`, the running count, avg
   sampling rate, etc.
2. **Watermark state**: max event-time seen across the stream.

Note: we **do not** have join state in the Kafka variant (we dropped the
stream-stream join).

### F.2 Where is state stored?
- **In-memory** during a batch.
- **Persisted to the checkpoint dir** at batch commit (HDFS-compatible FS).
- Spark 3.2+ supports **RocksDB state store** for larger-than-memory state
  (set `spark.sql.streaming.stateStore.providerClass`).

### F.3 What's `HDFSBackedStateStoreProvider` vs RocksDB?
- **HDFS-backed**: state in JVM heap; checkpoint writes a snapshot per batch.
  Default; fine for state up to a few GB.
- **RocksDB**: state on local disk via RocksDB; checkpoint writes only the
  delta. Better for **larger state** and **faster restart**. We use the
  default (small state).

### F.4 How is state bounded in our query?
By the **watermark + window combination**. With 30 s window and 30 s
watermark, state holds ~60 s × number-of-patients of data. For 1,097
patients, that's tiny (~MB).

### F.5 How does state survive a restart?
Spark reads the checkpoint dir on startup:
1. Reads the latest commit from the commit log.
2. Reads the state files for that commit.
3. Resumes the query with that state + the next Kafka offsets to read.

### F.6 What's `mapGroupsWithState` / `flatMapGroupsWithState`?
**Arbitrary stateful operators** — write your own state management with
timeouts. Powerful but tricky. We don't use them; built-in windowed agg is
enough.

### F.7 How big can state get before it's a problem?
Rule of thumb: keep state under **2-3 GB per executor** for HDFSBacked,
**tens of GB** for RocksDB. Beyond that, restart time and checkpoint write
time become painful.

---

## G. Checkpointing

### G.1 What's in the checkpoint directory?
Four sub-directories:
- **`offsets/`** — Kafka offsets consumed per batch.
- **`state/`** — windowed agg state per batch.
- **`commits/`** — log of completed batch IDs.
- **`sources/`** — source-specific progress.

### G.2 Where is our checkpoint stored?
On a PVC (`checkpoints-pvc`) mounted at `/data/checkpoints` in the speed-layer
pod. EBS gp3 backing.
```yaml
- {name: checkpoints, persistentVolumeClaim: {claimName: checkpoints-pvc}}
```

### G.3 Why is the PVC important?
The checkpoint must **survive pod restarts**. If it lived in `emptyDir`,
restarting the pod would lose Kafka offsets and state → re-process from the
beginning → duplicate alerts (or "data loss" if `startingOffsets=latest`).

### G.4 Why did we have to `rm -rf` the checkpoint when changing the query?
The line `rm -rf /data/checkpoints/kafka_speed_layer` in the manifest is there
for one specific reason: **schema/plan changes break checkpoint compatibility.**
When we changed the query (added/removed sources, changed aggregation), the
old offsets/state are no longer valid for the new plan. Spark errors with
`Cannot resume query from checkpoint`.

### G.5 What's the "2 sources vs 1" error?
Spark validates that the **number of streaming sources in the query** matches
what's in the checkpoint. We had a checkpoint from the earlier 2-source
(EEG+EHR) query and tried to resume with a 1-source (EEG-only) query. Spark
refused. Fix: delete the checkpoint dir on the next deploy.

### G.6 Can you change the operator graph and resume?
**Yes for small changes** (e.g., adding a select), **no for structural
changes** (adding sources, changing aggregations). Safe rule: if you change
the streaming query in any non-trivial way, **start fresh**.

### G.7 What's the performance impact of checkpointing?
- **Write per batch**: a few KB to MB for our query (state is small).
- **Latency added**: ~10–50 ms per batch (HDFSBacked, EBS).
- **Recovery time on restart**: read the latest commit + state ≈ few seconds.
For RocksDB state: smaller writes (deltas), faster recovery.

---

## H. Kafka source

### H.1 How does Spark consume from Kafka?
Not as a regular Kafka consumer group. It uses the **`KafkaConsumer` Java
API in offset-mode** — it tracks offsets in its checkpoint, not in Kafka's
consumer-group offset commits.

### H.2 What's `startingOffsets`?
```python
.option("startingOffsets", "latest")    # only events arriving after this query starts
.option("startingOffsets", "earliest")  # from the beginning of the topic
.option("startingOffsets", '{"eeg.raw":{"0":1234}}')   # specific per-partition
```
We use **`"latest"`** in the EKS variant (`speed_layer.py:144`). For
backfill demos, use `"earliest"`.

### H.3 What's `maxOffsetsPerTrigger` and why we set 5000?
**Caps the number of Kafka records read per micro-batch.** Set to 5000 in
`speed_layer.py:145`. Without it, the first batch after a restart with
`earliest` would try to read the whole topic at once → OOM.

### H.4 What if Kafka rebalances mid-batch?
Spark holds its own offset positions; **it ignores Kafka's consumer group
rebalance**. It manages partition assignment via the streaming engine.

### H.5 How does Spark handle Kafka partition expansion?
**Detected at trigger time.** On each batch, Spark calls `assignment()` on
the consumer and discovers new partitions; new partitions start at the
configured `startingOffsets` for that topic.

### H.6 Why parse with `from_json`?
Kafka returns `value` as `binary`; we cast to string and parse JSON with an
explicit schema:
```python
.select(F.from_json(F.col("value").cast("string"), eeg_schema).alias("e"))
.select("e.*")
```
The schema is **mandatory** — Spark can't infer schema from a streaming
source.

### H.7 What about the Kafka message key, headers, timestamp?
Available as columns alongside `value`:
```python
.load()
.select("key", "value", "topic", "partition", "offset", "timestamp", "headers")
```
We only use `value` (the JSON payload). In production you'd often partition
by `key` (e.g., `patient_id`) for downstream ordering guarantees.

### H.8 What if a message has bad JSON?
`from_json` with the **default `PERMISSIVE` mode** sets all fields to `null`.
The downstream agg includes the null row, which usually produces nonsense.
Fix: filter out null `patient_id` rows after parse, or use
`from_json(..., mode='FAILFAST')` to crash the batch.

### H.9 Can you write to Kafka from Structured Streaming?
Yes — there's a Kafka sink. We use it in the design-variant
(`build_streaming_pipeline`) via `publish_alerts` (which goes through
`alert_publisher.py`).

### H.10 What's the JAR dependency situation?
Kafka integration needs `spark-sql-kafka-0-10_2.12:3.5.0`. Pulled at
runtime via `--packages`:
```yaml
--packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0
```
First run downloads it to `/tmp/.ivy2/`; subsequent runs reuse the cache.

---

## I. UDFs

### I.1 What's our UDF and what does it compute?
`_score` in `speed_layer.py:178` — takes `(eeg_chunk_count, mean_sr, patient_id, win_start)`
and returns a 0–1 anomaly score:
```python
chunk_term   = min(chunk_count / 25.0, 1.0)
quality_term = 1.0 - signal_quality
base = 0.60 * chunk_term + 0.40 * quality_term
variance = (zlib.crc32(...) - 0.5) * 0.5    # deterministic per-window noise
return clamp(base + variance, 0, 1)
```

### I.2 Why is the score in the UDF different from `compute_anomaly_score`?
`compute_anomaly_score` uses 4 terms (chunk + quality + critical_lab +
meds_changes). The Kafka variant doesn't have EHR columns in the live stream,
so we use a 2-term reduced version: `0.60·chunk + 0.40·quality`.

### I.3 Why `zlib.crc32` instead of Python `hash()`?
**Determinism.** Python `hash()` is salted by `PYTHONHASHSEED`, which is
random per process. Same input → different hash on each executor → scores
are not reproducible across runs. `zlib.crc32` is a deterministic CRC.

### I.4 Why is this a Python UDF instead of a Pandas UDF?
For this scalar-per-row computation with a small DataFrame, the row-at-a-time
Python UDF overhead is acceptable. **Pandas UDFs are faster** for large
batches (vectorized) but add Arrow dependency overhead. Worth switching if
batches grow to 100k+ rows.

### I.5 What's the serialization cost of a Python UDF?
Each row goes: JVM → bytes → Python pickle → Python function → result → bytes
→ JVM. That's why **Pandas UDFs win at scale** — they batch this trip.

### I.6 How would you replace the UDF with built-in SQL functions for speed?
```python
.withColumn("chunk_term", F.least(F.col("eeg_chunk_count") / 25.0, F.lit(1.0)))
.withColumn("quality_term", F.lit(1.0) - F.col("signal_quality_score"))
.withColumn("score", 0.6 * F.col("chunk_term") + 0.4 * F.col("quality_term"))
```
Pure SQL — no Python trip. We kept the UDF because the CRC variance term is
ugly in SQL.

### I.7 Why register a UDF with `F.udf(...)` and not `@udf`?
Both work. `F.udf(func, returnType)` is the imperative form; the decorator
`@F.udf(returnType)` is the declarative form. Same result.

---

## J. foreachBatch sink

### J.1 What is `foreachBatch`?
A sink that hands you the micro-batch's DataFrame and lets you do **anything**
with it: write to a custom DB, send HTTP requests, log, etc. Each call gets
`(df, batch_id)`.

### J.2 How is our `_write_batch` implemented?
`speed_layer.py:205`:
```python
def _write_batch(df, batch_id):
    rows = df.collect()                       # to driver
    cluster = Cluster([host])
    try:
        session = cluster.connect("brainwatch")
        insert = session.prepare("INSERT INTO alerts ... VALUES (?, ?, ?, ?, ?)")
        for row in rows:
            severity = classify_v2(...).severity if quality_ok else "suppressed"
            session.execute(insert, (...))
    finally:
        cluster.shutdown()
```

### J.3 Why `try`/`finally` around `cluster.shutdown()`?
Without it, every micro-batch leaked a Cassandra cluster object (connection
pool, IO threads). After hours of running, the driver ran out of file
descriptors. Real bug; fixed by `finally`.

### J.4 Why `df.collect()` to the driver?
Our windowed agg output is small (one row per active patient × window).
Collecting to the driver is fine. For larger results, you'd use
`df.foreachPartition` instead to write from executors directly.

### J.5 Why `prepare(insert)` instead of inline `execute`?
Prepared statements are:
- **Faster** — Cassandra parses CQL once, reuses the plan.
- **Safer** — parameters are bound separately (no CQL injection).
- **Token-aware** — driver can route the request to the right node.

### J.6 Is `foreachBatch` exactly-once?
**Only if your sink is idempotent.** Spark can re-run the batch on failure;
duplicate writes are the application's responsibility to handle. We rely on
Cassandra's PK upsert (`(patient_id, alert_time)`) — a re-execute overwrites
the same row, no duplicate alert.

### J.7 What's `batch_id` and how do you use it?
A monotonically-increasing batch number. Use it to **deduplicate writes**:
```python
def _write_batch(df, batch_id):
    if already_written(batch_id):
        return                      # idempotency check
    write(df)
    mark_written(batch_id)
```
We don't do this because our sink is naturally idempotent.

### J.8 Can `foreachBatch` write to multiple sinks?
**Yes — that's the main reason to use it.** Inside `_write_batch` you can
write to Cassandra + Kafka + S3 in the same call. The original
`build_streaming_pipeline` does this via `publish_alerts` (Cassandra + Kafka
dual-sink).

---

## K. Joins

### K.1 What kinds of joins does Structured Streaming support?
- **Stream-static**: stream ⋈ batch DataFrame (always supported).
- **Stream-stream**: stream ⋈ stream (requires watermark + time predicate).

### K.2 Why is stream-stream join hard?
Spark can't know when a row from one stream might still join with a future
row from the other → would buffer forever. **Watermarks bound this**: drop
rows older than `max_event_time - allowed_lateness`.

### K.3 What did the design-variant do?
`build_streaming_pipeline` (l. 38):
```python
joined = eeg_df.join(ehr_df, on="patient_id", how="left_outer") \
               .filter(F.abs(...event_time - ehr.event_time) / 3600 <= 0.5)
```
±30 min predicate + watermarks on both sides.

### K.4 Why did we drop it in the EKS variant?
**Spark's stream-stream join in append mode + windowed aggregation has
latency we can't hide in a live demo.** Append-mode join requires watermark
delay before emission; then the downstream window agg adds its own delay.
Total emission latency was ~2 min, which kills the demo.

### K.5 What about stream-static join — could we use that?
**Yes.** Broadcast `patient_dim` once (small) and use it as a static
DataFrame inside `foreachBatch`:
```python
def _write_batch(df, batch_id):
    enriched = df.join(patient_dim_broadcast, "patient_id")
    write(enriched)
```
This is what production would do for slowly-changing dimensions like
patient demographics.

### K.6 What's the constraint matrix for join × output mode?

| Join type | append | update | complete |
|---|---|---|---|
| Stream-stream inner | ✅ | ❌ | ❌ |
| Stream-stream outer | ✅ (with watermark on both) | ❌ | ❌ |
| Stream-stream after agg | ❌ | ❌ | ❌ |
| Stream-static | ✅ | ✅ | ❌ |

### K.7 Where's the EHR enrichment now?
**In the batch/gold layer** (`gold_layer.build_patient_features`) — the
±30 min EHR join happens nightly when we recompute gold. The dashboard
shows the merged view; the speed layer only handles the EEG side live.

---

## L. Performance & tuning

### L.1 How many shuffle partitions?
**`spark.sql.shuffle.partitions=8`** in the EKS overlay (`real-pipeline.yaml:153`).
Default is 200 — way too many for our small streaming batches. 8 partitions
keeps task scheduling overhead low.

### L.2 What's our memory configuration?
```yaml
--driver-memory 4g
limits: {cpu: "2", memory: 5Gi}
```
4 GB driver, 1 GB headroom. The driver collects micro-batches to write to
Cassandra, so it needs enough memory for the largest batch.

### L.3 Why `local[4]` and not a real Spark cluster?
**Cost.** A real Spark-on-K8s cluster needs the Spark Operator, a driver pod
per query, executor pods, more EBS volumes. `local[4]` runs driver +
executors in one pod with 4 threads → cheap to operate, fine for our volume.

### L.4 How would we scale this to a real cluster?
Switch from `local[4]` to:
```bash
--master k8s://https://kubernetes.default.svc \
--conf spark.kubernetes.namespace=brainwatch \
--conf spark.executor.instances=3
```
Each executor becomes a pod. Spark Operator manages them.

### L.5 How do you tell if the stream is keeping up?
Watch **input rows/sec vs processed rows/sec** in Spark UI or via
`StreamingQueryProgress`:
```
inputRowsPerSecond:     1500
processedRowsPerSecond: 1500   ← keeping up
processedRowsPerSecond: 800    ← falling behind; lag will grow
```

### L.6 What's the backpressure mechanism?
`maxOffsetsPerTrigger=5000` is our explicit backpressure: never read more
than 5000 events per batch. If the stream is slow, Kafka lag grows but the
stream itself stays stable.

### L.7 What's the natural skew risk?
**Hot patients** (a stuck monitor flooding events for one `patient_id`).
That partition becomes hot, that executor lags. Mitigation: per-patient
rate limiting upstream (in the producer) — refuse new events for a patient
over N/sec.

### L.8 What's the cost of the `groupBy(patient_id, window)` shuffle?
Each micro-batch shuffles by `(patient_id, window_start)`. For our 8
shuffle partitions and 1,097 patients, that's ~137 rows per partition —
trivial. At 10× the cohort, we'd bump to `shuffle.partitions=32`.

### L.9 Could we skip the shuffle entirely?
Yes — if Kafka were already partitioned by `patient_id` (it is), Spark
could read each partition into an executor and aggregate locally with no
shuffle. Set `spark.sql.streaming.aggregation.stateFormatVersion=2`. Not
done in our overlay; possible optimization.

---

## M. Deployment on Kubernetes

### M.1 How does spark-submit run in our pod?
```bash
spark-submit --master local[4] --driver-memory 4g \
  --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 \
  --py-files /code/wheels/brainwatch_big_data-0.2.0-py3-none-any.whl \
  /code/scripts/run_speed_layer_kafka.py
```
One pod. The pod is the driver, and the 4 local threads are the executors.

### M.2 Why pip-install to `/code/site-packages`?
The Spark image's `/home/spark` is **read-only** under the `spark` user. We
install to a writable mount:
```bash
pip install --target=/code/site-packages /code/wheels/brainwatch_big_data...whl
export PYTHONPATH=/code/site-packages:$PYTHONPATH
```

### M.3 How does the Kafka JAR get into the pod?
`--packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0` triggers
Spark to download from Maven Central at startup (~30 s). Cached in
`/tmp/.ivy2/` for the lifetime of the pod.

### M.4 What about the cassandra-driver?
Pure-Python, pip-installed alongside the wheel:
```bash
pip install --target=/code/site-packages \
  /code/wheels/brainwatch_big_data...whl \
  cassandra-driver==3.29.1
```

### M.5 How does the pod restart-safely?
Three things have to be PVC-backed: **the checkpoint** (`/data/checkpoints`),
the source data if file-based (`bronze-pvc` for the Parquet variant), and
the sink data (Cassandra PVC). On restart, the new pod mounts the same PVCs
and the stream resumes from the checkpoint.

### M.6 How long does pod startup take?
- ~10 s for image pull (cached).
- ~30 s for `pip install` of dependencies.
- ~30 s for `spark-submit --packages` (Maven download / cache hit).
- ~10 s for SparkSession creation.
- ~5 s for Kafka subscription.
Total: ~1.5 min from `kubectl delete pod` to "first micro-batch."

---

## N. Observability & debug

### N.1 How do you know the stream is running?
```bash
kubectl -n brainwatch logs deploy/speed-layer -f | grep foreachBatch
# [foreachBatch batch_id=0] wrote 23 alerts to Cassandra
# [foreachBatch batch_id=1] wrote 18 alerts to Cassandra
```
The `print` in `_write_batch` (line 236) is your liveness signal.

### N.2 What's `StreamingQueryProgress`?
A JSON snapshot of the query state at the end of each batch:
```python
query.lastProgress     # last batch's stats
query.recentProgress   # last 100 batches
```
Key fields: `inputRowsPerSecond`, `processedRowsPerSecond`,
`numInputRows`, `triggerExecution`, `addBatch`, `queryPlanning`.

### N.3 Where's the Spark UI?
The driver runs an embedded UI on port 4040 by default. In our pod, you'd
`kubectl port-forward` to reach it. Shows: streaming tab with input rate
chart, processing time, batch duration, state info.

### N.4 What log lines should you grep for?
- `foreachBatch batch_id=N wrote M alerts` — our heartbeat.
- `Streaming query made progress` — Spark's per-batch log.
- `Cannot resume query from checkpoint` — schema mismatch.
- `OutOfMemoryError` — bad day.
- `KafkaConsumer rebalance` — partition reassignment.

### N.5 How do you detect lag?
Either:
- **Spark UI streaming tab** → input rate vs processing rate.
- **Kafka consumer lag** via the Kafka admin client (we don't have this
  wired up; in production you'd add `kafka-exporter` and a Prometheus
  alert).

### N.6 What if the stream is silently stuck?
The streaming query is alive but `lastProgress.timestamp` doesn't advance →
no new batches. Causes: Kafka broker down, all input is past the watermark,
checkpoint write failure. Fix: check the driver log, restart the pod.

---

## O. Errors we actually hit and fixed

### O.1 `Cannot resume query from checkpoint — 2 sources but checkpoint has 1`
**Cause**: changed the query from 1-source to 2-source (or vice versa)
without clearing checkpoint. **Fix**:
`rm -rf /data/checkpoints/kafka_speed_layer` (in the manifest's init script).

### O.2 `Stream-stream join with windowed aggregation is not supported in Update output mode`
**Cause**: we tried `update` mode with EHR-EEG join + windowed agg.
**Fix**: switched to `append`, dropped the stream-stream join, moved EHR
enrichment to batch.

### O.3 `CANNOT_READ_FILE_FOOTER` on bronze
**Cause**: tried to read JSONL with `parquet` source format.
**Fix**: `_read_bronze` in `silver_layer.py` walks the dir and sniffs format
before picking the reader.

### O.4 Driver OOM at 8 GiB batch
**Cause**: default 1 GB driver memory + 200 shuffle partitions on a heavy
gold join.
**Fix**: `--driver-memory 24g`, `spark.sql.shuffle.partitions=256`, AQE on.

### O.5 Connection reset to Kafka after pod restart
**Cause**: Spark consumer's stale connection.
**Fix**: nothing — Spark's `KafkaConsumer` reconnects automatically. The
error appears in logs once, then the stream catches up.

### O.6 Cassandra cluster leak (file-descriptor exhaustion after 24h)
**Cause**: `_write_batch` opened a new `Cluster([host])` per call without
shutdown.
**Fix**: `try`/`finally` around `cluster.shutdown()` (`speed_layer.py:212`).

### O.7 `hash()` produces different scores across executors
**Cause**: Python `hash()` is salted by `PYTHONHASHSEED`.
**Fix**: use `zlib.crc32` (`speed_layer.py:188`).

### O.8 Spark Python 3.8 too old for our wheel
**Cause**: `apache/spark:3.5.4` ships Python 3.8; our wheel requires 3.10+.
**Fix**: switched to `spark:3.5.5-scala2.12-java17-python3-ubuntu` (Python
3.10) and relaxed `requires-python = ">=3.10"` in `pyproject.toml`.

### O.9 `pip install --user` fails (read-only `/home/spark`)
**Cause**: image's home dir is RO under user `spark`.
**Fix**: `pip install --target=/code/site-packages` + `PYTHONPATH=...`.

### O.10 Watermark not advancing (`processedRowsPerSecond=0`)
**Cause**: producer stopped sending; max event_time stuck.
**Fix**: producer auto-restarts on failure; if persistent, watermarks
eventually advance via `processing-time fallback` (Spark 3.5+) — we haven't
enabled this.

---

## P. Comparisons

### P.1 Structured Streaming vs DStreams?
DStreams is the older RDD-based API, deprecated. Structured Streaming uses
DataFrames + Catalyst, supports event-time, watermarks, exactly-once via
checkpoint+idempotent-sink.

### P.2 Spark Structured Streaming vs Flink?
- **Spark**: micro-batch (5 s default), simpler ops, same API as batch.
- **Flink**: true streaming (record-at-a-time), sub-100 ms latency, more
  complex.
- **When to pick Flink**: latency budget < 1 s, complex event-time
  windowing, CEP (complex event processing).
- **When to pick Spark**: latency budget 5 s+, team already knows
  DataFrames, batch and streaming on the same engine.

### P.3 Spark Streaming vs Kafka Streams?
- **Kafka Streams**: a JAR; runs inside your service; no separate cluster;
  JVM-only.
- **Spark**: separate cluster, polyglot (Python/Scala/SQL/R), heavier ops.
- **When Kafka Streams wins**: small JVM services where you don't want to
  operate a Spark cluster.

### P.4 `foreachBatch` vs Kafka sink?
- **Kafka sink**: native, easy, no driver collect.
- **`foreachBatch`**: arbitrary external system. Slightly higher ops cost
  (you write the connection management).
- We use `foreachBatch` because the destination is Cassandra, not Kafka.

### P.5 In-memory state vs RocksDB?
- **In-memory** (default): faster batches, lower restart resilience,
  capped by heap.
- **RocksDB**: state on local disk, delta checkpoints, faster recovery,
  supports larger state.
- Switch via `spark.sql.streaming.stateStore.providerClass=org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider`.

---

*Cross-references: `STUDY-GUIDE.md` §6.8 (speed layer code walk),
`QA-BANK.md` §6 (speed-layer Q&A), `AUTO-TRIGGER-MECHANISMS.md` §2 (trigger
mechanics deep-dive), `REAL-VS-DEMO.md` §4.5 (what production speed layers
add).*
