#!/usr/bin/env bash
# BrainWatch teardown — reverse of deploy.sh.
#
#   bash infra/k8s/teardown.sh                # remove workloads, KEEP PVCs
#   bash infra/k8s/teardown.sh --delete-pvcs  # also drop persistent data (double-prompts)
set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
DELETE_PVCS=false
if [ "${1:-}" = "--delete-pvcs" ]; then
  DELETE_PVCS=true
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

delete() {
  local manifest="$DIR/$1"
  echo ">>> Deleting $1"
  kubectl delete -f "$manifest" --ignore-not-found
}

echo "========================================"
echo " BrainWatch teardown — namespace: $NAMESPACE"
echo "========================================"

delete spark-batch-cronjob.yaml
delete spark-streaming-deployment.yaml
delete cassandra-statefulset.yaml
delete kafka-statefulset.yaml
delete zookeeper-deployment.yaml
delete configmap.yaml

if [ "$DELETE_PVCS" = "true" ]; then
  echo ""
  echo "!!! WARNING: about to delete all persistent data (bronze/silver/gold) !!!"
  read -rp "Confirm 1/2 — type 'yes' to continue: " confirm1
  if [ "$confirm1" != "yes" ]; then
    echo "Cancelled. PVCs preserved."
    exit 0
  fi
  read -rp "Confirm 2/2 — type 'DELETE' to permanently remove: " confirm2
  if [ "$confirm2" != "DELETE" ]; then
    echo "Cancelled. PVCs preserved."
    exit 0
  fi
  delete persistent-volumes.yaml
  echo "PVCs deleted."
else
  echo ""
  echo "(PVCs preserved. Use --delete-pvcs to remove data.)"
fi

delete namespace.yaml

echo ""
echo "Teardown complete."
