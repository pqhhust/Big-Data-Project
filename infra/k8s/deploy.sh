#!/usr/bin/env bash
# BrainWatch — production deploy script.
#
# Owner: Dat (script), Quang-Hung (cluster cutover).
#
# Usage:
#   bash infra/k8s/deploy.sh                # deploy everything in order
#   bash infra/k8s/deploy.sh --dry-run      # kubectl apply --dry-run=client
#
# Dat: implement.  Order matters — apply each layer and wait for it to be
# ready before moving on. `set -euo pipefail` is mandatory.

set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
DRY_RUN="${DRY_RUN:-false}"
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN="true"
fi

# Dat: implement the deploy steps. Suggested order:
#
#   1. namespace.yaml
#   2. configmap.yaml
#   3. persistent-volumes.yaml          (PVCs must exist before pods that mount them)
#   4. zookeeper-deployment.yaml        (skip if Kafka is in KRaft mode)
#   5. kafka-statefulset.yaml           (wait for cluster ready: kubectl rollout status statefulset/kafka)
#   6. cassandra-statefulset.yaml       (wait for cassandra-0 readiness probe)
#   7. spark-streaming-deployment.yaml  (the speed layer)
#   8. spark-batch-cronjob.yaml         (the daily batch)
#
# After each apply, wait until the workload is Ready:
#
#   kubectl -n "$NAMESPACE" rollout status statefulset/<name> --timeout=300s
#
# Pattern:
#   apply() {
#     local manifest="$1"
#     if [ "$DRY_RUN" = "true" ]; then
#       kubectl apply -f "$manifest" --dry-run=client
#     else
#       kubectl apply -f "$manifest"
#     fi
#   }
#
# At the end, print:
#   kubectl -n "$NAMESPACE" get pods,svc,statefulset,deployment,cronjob
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apply() {
  local manifest="$DIR/$1"
  echo ">>> Applying $1 ..."
  if [ "$DRY_RUN" = "true" ]; then
    kubectl apply -f "$manifest" --dry-run=client
  else
    kubectl apply -f "$manifest"
  fi
}

wait_rollout() {
  local kind="$1"
  local name="$2"
  if [ "$DRY_RUN" = "true" ]; then
    echo "    [dry-run] skipping rollout wait for $kind/$name"
    return
  fi
  echo "    Waiting for $kind/$name ..."
  kubectl -n "$NAMESPACE" rollout status "$kind/$name" --timeout=300s
}

echo "========================================"
echo " BrainWatch deploy — namespace: $NAMESPACE"
echo "========================================"

# 1. Namespace
apply namespace.yaml

# 2. ConfigMap
apply configmap.yaml

# 3. PVCs — phải có trước khi pod nào được tạo
apply persistent-volumes.yaml

# 4. Cassandra — Spark cần Cassandra chạy trước
apply cassandra-statefulset.yaml
wait_rollout statefulset cassandra

# 5. Spark Streaming Deployment
apply spark-streaming-deployment.yaml
wait_rollout deployment spark-streaming

# 6. Spark Batch CronJob — không có pod chạy ngay, chỉ đăng ký lịch
apply spark-batch-cronjob.yaml

echo ""
echo "========================================"
echo " Deploy xong. Trạng thái cluster:"
echo "========================================"
kubectl -n "$NAMESPACE" get pods,svc,pvc,statefulset,deployment,cronjob
