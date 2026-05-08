#!/usr/bin/env bash
# BrainWatch — teardown script.
#
# Owner: Dat.
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

# Dat: implement.  Suggested order:
#
#   1. spark-batch-cronjob.yaml
#   2. spark-streaming-deployment.yaml
#   3. cassandra-statefulset.yaml
#   4. kafka-statefulset.yaml
#   5. zookeeper-deployment.yaml (if applicable)
#   6. configmap.yaml
#   7. (only if --delete-pvcs) persistent-volumes.yaml
#      → confirm twice with `read -p` before deleting.
#   8. namespace.yaml (this also nukes everything inside, but explicit is better)
#
# Use `kubectl delete -f <file> --ignore-not-found` so reruns are safe.

echo "Dat: implement teardown steps in this script."
exit 1
