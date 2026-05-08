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

echo "Dat: implement deploy steps in this script."
exit 1
