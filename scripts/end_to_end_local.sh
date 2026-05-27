#!/usr/bin/env bash
# end_to_end_local.sh
#
# Spin up the local container stack (Kafka + Spark + Cassandra), seed
# patient_state with enrichment for two test patients, push synthetic
# EEG + EHR events to Kafka, start the speed layer with --mode=both
# (Cassandra lookup + Kafka stream-stream join concurrently), wait
# for alerts, assert both `source` tags appear in brainwatch.alerts,
# then tear the stack down unconditionally.
#
# The trap on EXIT means "turn off deploy local" happens automatically
# whether the test passes or fails -- the dev host doesn't end up with
# Kafka + Cassandra containers eating ports between sessions.
#
# Prerequisites: docker + docker compose; python3 with the
# `kafka-python` package on the host (only for pushing the synthetic
# input batch).
#
# Usage:
#   bash scripts/end_to_end_local.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE="$REPO/infra/docker/docker-compose.yml"

teardown() {
  echo
  echo "==== tearing down local stack (turn off deploy local) ===="
  docker compose -f "$COMPOSE" down -v 2>&1 | tail -5 || true
}
trap teardown EXIT

echo "==== bringing up Kafka + Spark + Cassandra ===="
docker compose -f "$COMPOSE" up -d

echo "==== waiting for Cassandra schema init ===="
for _ in $(seq 1 30); do
  if docker compose -f "$COMPOSE" logs cassandra-init 2>/dev/null \
       | grep -q "Cassandra schema applied"; then
    break
  fi
  sleep 5
done

echo "==== seeding patient_state for two test patients ===="
docker compose -f "$COMPOSE" exec -T cassandra cqlsh -e "
  INSERT INTO brainwatch.patient_state
    (patient_id, has_critical_lab, n_medication_changes_24h, enrichment_updated_at)
    VALUES ('P001', true, 3, dateof(now()));
  INSERT INTO brainwatch.patient_state
    (patient_id, has_critical_lab, n_medication_changes_24h, enrichment_updated_at)
    VALUES ('P002', false, 0, dateof(now()));
"

echo "==== pushing 50 synthetic EEG + 10 EHR events to Kafka ===="
python3 - <<'PY'
import json
from datetime import datetime, timezone
from kafka import KafkaProducer

prod = KafkaProducer(
    bootstrap_servers="localhost:9094",
    acks="all",
    linger_ms=20,
    compression_type="gzip",
    value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
)
now = datetime.now(timezone.utc).isoformat()
for i in range(50):
    pid = "P001" if i < 25 else "P002"
    prod.send("eeg.raw", {
        "patient_id": pid, "session_id": f"S{i // 10}",
        "event_time": now, "site_id": "S0001",
        "channel_count": 19, "sampling_rate_hz": 200.0,
        "window_seconds": 4.0, "source_uri": "synthetic://test",
    })
for i in range(10):
    pid = "P001" if i < 5 else "P002"
    et = "critical_lab" if i % 5 == 0 else "medication"
    prod.send("ehr.updates", {
        "patient_id": pid, "encounter_id": f"E{i}",
        "event_time": now, "event_type": et,
        "source_system": "synthetic", "version": 1,
    })
prod.flush()
print(f"Pushed 50 eeg + 10 ehr events at {now}")
PY

echo "==== running speed_layer --mode=both for 90 s ===="
docker compose -f "$COMPOSE" run --rm \
  -v "$REPO":/app -w /app \
  spark-master \
  /opt/spark/bin/spark-submit \
    --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 \
    --conf spark.jars.ivy=/tmp/.ivy2 \
    src/brainwatch/processing/speed_layer.py \
      --kafka kafka:9092 \
      --cassandra cassandra \
      --checkpoint /tmp/checkpoints \
      --mode both &
SPARK_PID=$!
sleep 90
kill "$SPARK_PID" 2>/dev/null || true

echo "==== querying Cassandra for emitted alerts ===="
docker compose -f "$COMPOSE" exec -T cassandra cqlsh -e "
  SELECT source, COUNT(*) FROM brainwatch.alerts GROUP BY source;
" || true
N_LOOKUP=$(docker compose -f "$COMPOSE" exec -T cassandra cqlsh -e \
  "SELECT COUNT(*) FROM brainwatch.alerts WHERE source='speed_lookup' ALLOW FILTERING;" \
  | awk '/^[[:space:]]*[0-9]+/{print $1; exit}')
N_JOIN=$(docker compose -f "$COMPOSE" exec -T cassandra cqlsh -e \
  "SELECT COUNT(*) FROM brainwatch.alerts WHERE source='speed_join' ALLOW FILTERING;" \
  | awk '/^[[:space:]]*[0-9]+/{print $1; exit}')

echo
echo "==== assertion ===="
echo "  speed_lookup alerts = ${N_LOOKUP:-0}"
echo "  speed_join   alerts = ${N_JOIN:-0}"
if [ "${N_LOOKUP:-0}" -ge 1 ] && [ "${N_JOIN:-0}" -ge 1 ]; then
  echo "PASS: both speed_lookup and speed_join produced alerts"
  exit 0
fi
echo "FAIL: at least one of the two paths produced zero alerts"
exit 1
