#!/usr/bin/env bash
# BrainWatch — teardown script.
#
# Owner: Dat.
# Last updated: 2026-05-21 by Nguyễn Đình Đạt
#
# Reverse order of deploy.sh. ALWAYS prompt before deleting PVCs — losing
# the bronze zone means re-running the whole pipeline.
#
# Usage:
#   bash infra/k8s/teardown.sh                # tears down workloads, KEEPS PVCs
#   bash infra/k8s/teardown.sh --delete-pvcs  # also drops persistent data (asks twice)

set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
DELETE_PVCS=false
if [ "${1:-}" = "--delete-pvcs" ]; then
  DELETE_PVCS=true
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

delete() {
  local manifest="$DIR/$1"
  echo ">>> Deleting $1 ..."
  kubectl delete -f "$manifest" --ignore-not-found
}

echo "========================================"
echo " BrainWatch teardown — namespace: $NAMESPACE"
echo "========================================"

# Ngược chiều deploy: xóa Spark trước, Cassandra sau
delete spark-batch-cronjob.yaml
delete spark-streaming-deployment.yaml
delete cassandra-statefulset.yaml
delete configmap.yaml

if [ "$DELETE_PVCS" = "true" ]; then
  echo ""
  echo "!!! CẢNH BÁO: Sắp xóa toàn bộ persistent data (bronze/silver/gold) !!!"
  read -rp "Xác nhận lần 1 — gõ 'yes' để tiếp tục: " confirm1
  if [ "$confirm1" != "yes" ]; then
    echo "Hủy. PVCs được giữ lại."
    exit 0
  fi
  read -rp "Xác nhận lần 2 — gõ 'DELETE' để xóa vĩnh viễn: " confirm2
  if [ "$confirm2" != "DELETE" ]; then
    echo "Hủy. PVCs được giữ lại."
    exit 0
  fi
  delete persistent-volumes.yaml
  echo "PVCs đã xóa."
else
  echo ""
  echo "(PVCs được giữ lại. Dùng --delete-pvcs để xóa data.)"
fi

# Namespace xóa cuối cùng
delete namespace.yaml

echo ""
echo "Teardown hoàn tất."

