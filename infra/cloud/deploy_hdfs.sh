#!/usr/bin/env bash
# Bring up the hybrid storage path on an existing EKS cluster.
#
# Prereqs (already done by deploy_cloud.sh / resume_from_snapshots.sh):
#   - kubectl context points at the brainwatch-capstone EKS cluster
#   - brainwatch namespace exists with Kafka, Cassandra, and the bronze-pvc
#
# What this does:
#   1. Apply hdfs.yaml          → NameNode + 2 DataNodes + lake dir bootstrap
#   2. Wait for the StatefulSets to be Ready
#   3. Apply batch-on-hdfs.yaml → loader Job + spark-batch Job
#   4. Print the URLs you need to demo
#
# Expected wall-clock: 3–5 min for HDFS + 1–2 min for the bronze loader +
# 3–5 min for the batch run.
set -euo pipefail

NAMESPACE=${NAMESPACE:-brainwatch}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

KUBECTL="${KUBECTL:-$HOME/bin/kubectl}"
if ! command -v "$KUBECTL" >/dev/null 2>&1; then
  echo "kubectl not found at $KUBECTL"; exit 1
fi

echo "==> 1. Applying HDFS overlay"
$KUBECTL apply -f infra/cloud/k8s-overlays/hdfs.yaml

echo "==> 2. Waiting for NameNode to be Ready"
$KUBECTL -n "$NAMESPACE" rollout status statefulset/hdfs-namenode --timeout=300s

echo "==> 3. Waiting for DataNodes to be Ready"
$KUBECTL -n "$NAMESPACE" rollout status statefulset/hdfs-datanode --timeout=300s

echo "==> 4. Waiting for bootstrap Job to finish (creates /lake, /checkpoints dirs)"
$KUBECTL -n "$NAMESPACE" wait --for=condition=complete --timeout=180s job/hdfs-bootstrap-dirs

echo "==> 5. Sanity check — HDFS lake tree"
$KUBECTL -n "$NAMESPACE" exec statefulset/hdfs-namenode -- \
  /opt/hadoop-3.2.1/bin/hdfs dfs -ls /

echo "==> 6. Applying batch-on-hdfs overlay (loader + batch Jobs)"
$KUBECTL apply -f infra/cloud/k8s-overlays/batch-on-hdfs.yaml

echo "==> 7. Waiting for the bronze loader to finish"
$KUBECTL -n "$NAMESPACE" wait --for=condition=complete --timeout=600s job/hdfs-bronze-loader || \
  echo "    (loader still running or no bronze-pvc data; check logs)"

echo "==> 8. Triggering the Spark batch on HDFS"
$KUBECTL -n "$NAMESPACE" wait --for=condition=complete --timeout=900s job/spark-batch-hdfs || \
  echo "    (batch still running; tail logs with kubectl logs job/spark-batch-hdfs)"

echo ""
echo "==> Hybrid storage is live."
echo ""
echo "Demo URLs:"
echo "  HDFS NameNode UI: kubectl -n $NAMESPACE port-forward svc/hdfs-namenode 9870:9870"
echo "                    → open http://localhost:9870 (Datanodes tab, Browse the file system)"
echo "  Dashboard (S3):   http://brainwatch-dashboard-923884399064.s3-website-us-east-1.amazonaws.com"
echo ""
echo "Useful commands:"
echo "  kubectl -n $NAMESPACE exec sts/hdfs-namenode -- hdfs dfs -ls -R /lake | head -50"
echo "  kubectl -n $NAMESPACE exec sts/hdfs-namenode -- hdfs dfs -du -h /lake"
echo "  kubectl -n $NAMESPACE logs job/spark-batch-hdfs --tail 200"
echo "  kubectl -n $NAMESPACE get pods | grep -E 'hdfs|spark|speed'"
echo ""
echo "Tear down hybrid (keep the rest of the cluster):"
echo "  kubectl -n $NAMESPACE delete -f infra/cloud/k8s-overlays/batch-on-hdfs.yaml"
echo "  kubectl -n $NAMESPACE delete -f infra/cloud/k8s-overlays/hdfs.yaml"
