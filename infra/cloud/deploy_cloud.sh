#!/usr/bin/env bash
# End-to-end cloud deploy driver for BrainWatch.
#
# Prereqs (any one cloud):
#   AWS:    aws CLI, eksctl, an IAM identity with EKS+ECR+EC2 permissions
#   GCP:    gcloud CLI, an authenticated project, GKE Autopilot enabled
#   Azure:  az CLI, an active subscription, AKS-ready RG
#
# Required env vars:
#   CLOUD            one of: eks | gke | aks
#   IMAGE            full image ref (e.g. 1234.dkr.ecr.us-east-1.amazonaws.com/brainwatch:0.3.0)
#   CLUSTER_NAME     name of the existing or about-to-be-created cluster
#   REGION           cloud region (e.g. us-east-1, asia-southeast1, southeastasia)
#
# Optional:
#   NAMESPACE=brainwatch
#   SKIP_BUILD=1     skip docker build/push (image already in registry)
#   SKIP_CREATE=1    skip cluster create (it already exists)
set -euo pipefail

CLOUD="${CLOUD:?CLOUD must be eks|gke|aks}"
IMAGE="${IMAGE:?IMAGE must be a full image ref}"
CLUSTER_NAME="${CLUSTER_NAME:?CLUSTER_NAME required}"
REGION="${REGION:?REGION required}"
NAMESPACE="${NAMESPACE:-brainwatch}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "==> BrainWatch cloud deploy"
echo "    cloud=$CLOUD  cluster=$CLUSTER_NAME  region=$REGION  image=$IMAGE"

# ---------- 1. Build + push image ----------
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "==> Building image"
  docker build -f infra/docker/Dockerfile -t "$IMAGE" .

  case "$CLOUD" in
    eks)
      REGISTRY="$(echo "$IMAGE" | cut -d/ -f1)"
      aws ecr get-login-password --region "$REGION" | \
        docker login --username AWS --password-stdin "$REGISTRY"
      ;;
    gke)
      gcloud auth configure-docker --quiet
      ;;
    aks)
      ACR_NAME="$(echo "$IMAGE" | cut -d. -f1)"
      az acr login --name "$ACR_NAME"
      ;;
  esac
  docker push "$IMAGE"
fi

# ---------- 2. Cluster create (if needed) ----------
if [ "${SKIP_CREATE:-0}" != "1" ]; then
  case "$CLOUD" in
    eks)
      eksctl create cluster --name "$CLUSTER_NAME" --region "$REGION" \
        --nodes 3 --node-type m5.large --managed
      aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"
      ;;
    gke)
      gcloud container clusters create-auto "$CLUSTER_NAME" --region "$REGION"
      gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION"
      ;;
    aks)
      RG="${RG:-${CLUSTER_NAME}-rg}"
      az group create --name "$RG" --location "$REGION" --output none
      az aks create --resource-group "$RG" --name "$CLUSTER_NAME" \
        --node-count 3 --node-vm-size Standard_D4s_v5 --generate-ssh-keys
      az aks get-credentials --resource-group "$RG" --name "$CLUSTER_NAME"
      ;;
  esac
fi

# ---------- 3. Render manifests with the cloud image ref ----------
RENDERED="$(mktemp -d)/k8s"
mkdir -p "$RENDERED"
cp -r infra/k8s/. "$RENDERED/"
sed -i "s|apache/spark:3.5.4|$IMAGE|g" \
  "$RENDERED"/spark-streaming-deployment.yaml "$RENDERED"/spark-batch-cronjob.yaml

# ---------- 4. Apply ----------
echo "==> Applying manifests"
NAMESPACE="$NAMESPACE" bash infra/k8s/deploy.sh

# ---------- 5. Load bronze data into the cluster ----------
if [ -d "data/lake/bronze" ] && [ -n "$(ls -A data/lake/bronze 2>/dev/null || true)" ]; then
  echo "==> Uploading local bronze zone into the bronze-pvc"
  POD="$(kubectl -n "$NAMESPACE" get pod -l app=spark-streaming -o name | head -1)"
  if [ -n "$POD" ]; then
    kubectl -n "$NAMESPACE" cp data/lake/bronze "$POD:/data/lake/bronze"
  else
    echo "    (no spark-streaming pod yet; skip data upload — retry once Ready)"
  fi
fi

# ---------- 6. Run the batch driver as a one-shot Job ----------
echo "==> Triggering an immediate batch run"
kubectl -n "$NAMESPACE" create job --from=cronjob/spark-batch batch-bootstrap-$(date +%s)

echo ""
echo "==> Done. Useful commands:"
echo "    kubectl -n $NAMESPACE get pods -w"
echo "    kubectl -n $NAMESPACE logs deploy/spark-streaming -f"
echo "    kubectl -n $NAMESPACE port-forward svc/spark-streaming-ui 4040:4040"
