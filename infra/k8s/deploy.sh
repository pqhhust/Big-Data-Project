#!/usr/bin/env bash
# BrainWatch production deploy.
#
#   bash infra/k8s/deploy.sh             # apply everything in order
#   bash infra/k8s/deploy.sh --dry-run   # kubectl apply --dry-run=client
#   NAMESPACE=foo bash infra/k8s/deploy.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-brainwatch}"
DRY_RUN="${DRY_RUN:-false}"
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN="true"
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apply() {
  local manifest="$DIR/$1"
  echo ">>> Applying $1"
  if [ "$DRY_RUN" = "true" ]; then
    kubectl apply -f "$manifest" --dry-run=client
  else
    kubectl apply -f "$manifest"
  fi
}

wait_rollout() {
  local kind="$1" name="$2"
  if [ "$DRY_RUN" = "true" ]; then
    echo "    [dry-run] skipping rollout wait for $kind/$name"
    return
  fi
  echo "    Waiting for $kind/$name"
  kubectl -n "$NAMESPACE" rollout status "$kind/$name" --timeout=300s
}

echo "========================================"
echo " BrainWatch deploy — namespace: $NAMESPACE"
echo "========================================"

apply namespace.yaml
apply configmap.yaml
apply persistent-volumes.yaml

apply zookeeper-deployment.yaml
wait_rollout deployment zookeeper

apply kafka-statefulset.yaml
wait_rollout statefulset kafka

apply cassandra-statefulset.yaml
wait_rollout statefulset cassandra

apply spark-streaming-deployment.yaml
wait_rollout deployment spark-streaming

apply spark-batch-cronjob.yaml

echo ""
echo "========================================"
echo " Deploy complete. Cluster state:"
echo "========================================"
kubectl -n "$NAMESPACE" get pods,svc,pvc,statefulset,deployment,cronjob
