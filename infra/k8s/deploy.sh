#!/bin/bash
# BrainWatch Kubernetes Deployment Script
# Usage: bash infra/k8s/deploy.sh

set -euo pipefail

NAMESPACE="brainwatch"
K8S_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo "  BrainWatch — Kubernetes Deployment"
echo "============================================"

# 1. Create namespace
echo "[1/6] Creating namespace..."
kubectl apply -f "$K8S_DIR/namespace.yaml"

# 2. Apply ConfigMaps
echo "[2/6] Applying ConfigMaps..."
kubectl apply -f "$K8S_DIR/configmap.yaml"

# 3. Deploy Kafka
echo "[3/6] Deploying Kafka..."
kubectl apply -f "$K8S_DIR/kafka-deployment.yaml"

# 4. Deploy Cassandra
echo "[4/6] Deploying Cassandra..."
kubectl apply -f "$K8S_DIR/cassandra-statefulset.yaml"

# 5. Wait for dependencies
echo "[5/6] Waiting for pods to be ready..."
kubectl wait --for=condition=Ready pod -l app=kafka -n "$NAMESPACE" --timeout=120s 2>/dev/null || \
    echo "  ⏳ Kafka pods not yet ready (may take a few minutes)"
kubectl wait --for=condition=Ready pod -l app=cassandra -n "$NAMESPACE" --timeout=120s 2>/dev/null || \
    echo "  ⏳ Cassandra pods not yet ready (may take a few minutes)"

# 6. Deploy Spark jobs
echo "[6/6] Deploying Spark jobs..."
kubectl apply -f "$K8S_DIR/spark-batch-job.yaml"
kubectl apply -f "$K8S_DIR/spark-streaming-deployment.yaml"
kubectl apply -f "$K8S_DIR/ingress.yaml" 2>/dev/null || true

echo ""
echo "============================================"
echo "  Deployment complete!"
echo "============================================"
echo ""
echo "View status:"
echo "  kubectl get all -n $NAMESPACE"
echo ""
echo "View logs:"
echo "  kubectl logs -f deployment/spark-streaming -n $NAMESPACE"
echo ""
