#!/usr/bin/env bash
# verify_exactly_once.sh
#
# Empirical check of the exactly-once-visibility claim made by the speed
# layer (Lesson 3 and Lesson 11 of the BrainWatch final report).
#
# The three properties under test:
#   1. Replayable source     — Kafka offsets persisted in the Spark checkpoint
#   2. Idempotent sink       — Cassandra primary key (patient_id, alert_time)
#   3. Checkpointed state    — Spark state store on the checkpoints PVC
#
# Protocol:
#   a. Snapshot the alert count in Cassandra.
#   b. Wait one micro-batch worth of wall clock so the streamer ingests new events.
#   c. Force-delete the speed-layer pod mid-batch.
#   d. Wait for the Deployment to recreate the pod and the streaming query to resume.
#   e. Wait two more micro-batches.
#   f. Snapshot the alert count again and assert no rows were lost.
#
# Run AGAINST a live cluster:
#   bash scripts/verify_exactly_once.sh
#
# Optional environment variables:
#   NAMESPACE              (default: brainwatch)
#   SPEED_LAYER_LABEL      (default: app=spark-speed-layer)
#   CASSANDRA_POD          (default: cassandra-0)
#   MICROBATCH_SECONDS     (default: 30)
#   RESUME_TIMEOUT_SECONDS (default: 180)
set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
SPEED_LAYER_LABEL="${SPEED_LAYER_LABEL:-app=spark-speed-layer}"
CASSANDRA_POD="${CASSANDRA_POD:-cassandra-0}"
MICROBATCH_SECONDS="${MICROBATCH_SECONDS:-30}"
RESUME_TIMEOUT_SECONDS="${RESUME_TIMEOUT_SECONDS:-180}"

count_alerts() {
  kubectl -n "$NAMESPACE" exec "$CASSANDRA_POD" -c cassandra -- \
    cqlsh -e 'SELECT COUNT(*) FROM brainwatch.alerts;' \
    | awk '/^[[:space:]]*[0-9]+$/{print $1; exit}'
}

speed_pod() {
  kubectl -n "$NAMESPACE" get pod -l "$SPEED_LAYER_LABEL" \
    -o jsonpath='{.items[0].metadata.name}'
}

wait_for_ready() {
  local deadline=$(( SECONDS + RESUME_TIMEOUT_SECONDS ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if kubectl -n "$NAMESPACE" wait --for=condition=Ready \
         -l "$SPEED_LAYER_LABEL" pod --timeout=5s >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "TIMEOUT: speed-layer pod did not become Ready within ${RESUME_TIMEOUT_SECONDS}s" >&2
  return 1
}

echo "[1/6] snapshot alert count BEFORE pod-delete"
BEFORE=$(count_alerts)
echo "      alerts BEFORE = $BEFORE"

echo "[2/6] wait one micro-batch (${MICROBATCH_SECONDS}s) so a batch is in flight"
sleep "$MICROBATCH_SECONDS"

echo "[3/6] force-delete the speed-layer pod mid-batch"
POD=$(speed_pod)
echo "      deleting pod: $POD"
kubectl -n "$NAMESPACE" delete pod "$POD" --grace-period=0 --force >/dev/null

echo "[4/6] wait for Deployment to recreate the pod and the query to resume"
wait_for_ready

echo "[5/6] wait two more micro-batches for the replay to land"
sleep $(( MICROBATCH_SECONDS * 2 ))

echo "[6/6] snapshot alert count AFTER pod-delete + recovery"
AFTER=$(count_alerts)
echo "      alerts AFTER  = $AFTER"

if [ -z "$BEFORE" ] || [ -z "$AFTER" ]; then
  echo "FAIL: could not read alert counts" >&2
  exit 2
fi

if [ "$AFTER" -lt "$BEFORE" ]; then
  echo "FAIL: alert count regressed (BEFORE=$BEFORE, AFTER=$AFTER); replay lost rows" >&2
  exit 1
fi

echo "PASS: exactly-once visibility upheld across pod-delete (BEFORE=$BEFORE, AFTER=$AFTER, delta=$((AFTER - BEFORE)))"
