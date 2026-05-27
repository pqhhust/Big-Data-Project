#!/usr/bin/env bash
# BrainWatch — production deploy script.
#
# Owner: Dat (script), Quang-Hung (cluster cutover).
# Last updated: 2026-05-26 by Nguyễn Đình Đạt
# Changes: triển khai K8s infra (PVC, Cassandra, Spark Streaming/Batch) + viết deploy/teardown scripts
# Note: script deploy theo thứ tự namespace → PV → Cassandra → Spark Streaming → Spark Batch
#
# Usage:
#   bash infra/k8s/deploy.sh                # deploy everything in order
#   bash infra/k8s/deploy.sh --dry-run      # kubectl apply --dry-run=client
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
DRY_RUN="${DRY_RUN:-false}"
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN="true"
fi

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
