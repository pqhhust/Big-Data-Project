#!/usr/bin/env bash
# Resume the BrainWatch EKS cluster from the snapshots taken during pause.
#
# Prereqs:
#   - AWS creds in env (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION=us-east-1)
#   - eksctl, kubectl, aws CLI on PATH
#   - artifacts/eks/snapshots/index.txt (committed at pause time)
#
# What this does:
#   1. Recreates the EKS cluster (brainwatch-capstone, 2x t3.xlarge)
#   2. Installs EBS CSI driver + attaches the policy to the node role
#   3. For each snapshot in the inventory, creates a fresh EBS volume from it
#      (in the same AZ as the cluster nodes) and a matching pre-bound PV
#   4. Creates PVCs that bind to those PVs
#   5. Applies all the namespaced manifests (Cassandra, Kafka KRaft, Grafana,
#      real-pipeline, aws-credentials secret, dashboards ConfigMap, schema init)
#
# Expected wall-clock: 15-20 min for the cluster + 5 min for the workloads.
set -euo pipefail

CLUSTER=${CLUSTER:-brainwatch-capstone}
REGION=${REGION:-us-east-1}
NAMESPACE=${NAMESPACE:-brainwatch}
INVENTORY=${INVENTORY:-artifacts/eks/snapshots/index.txt}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

: "${AWS_ACCESS_KEY_ID:?required}"
: "${AWS_SECRET_ACCESS_KEY:?required}"

echo "==> 1. Provisioning EKS cluster $CLUSTER"
if ! aws eks describe-cluster --name "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  eksctl create cluster --name "$CLUSTER" --region "$REGION" --version 1.30 \
    --nodegroup-name workers --node-type t3.xlarge --nodes 2 --managed \
    --asg-access --full-ecr-access --node-volume-size 100 --node-volume-type gp3
fi
aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION"

echo "==> 2. EBS CSI driver"
ROLE=$(aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name workers \
       --region "$REGION" --query 'nodegroup.nodeRole' --output text | awk -F/ '{print $NF}')
aws iam attach-role-policy --policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
                          --role-name "$ROLE" 2>/dev/null || true
aws eks create-addon --cluster-name "$CLUSTER" --addon-name aws-ebs-csi-driver --region "$REGION" 2>/dev/null || true

echo "==> 3. Namespace + storage class + gp3 default"
kubectl apply -f infra/k8s/namespace.yaml
kubectl apply -f infra/k8s/configmap.yaml
kubectl apply -f infra/cloud/k8s-overlays/storage-class.yaml
kubectl patch storageclass gp2 -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' || true

echo "==> 4. Pick an AZ from the worker nodes (PV must match)"
AZ=$(kubectl get node -o jsonpath='{.items[0].metadata.labels.topology\.kubernetes\.io/zone}')
echo "    using AZ=$AZ"

echo "==> 5. Restore EBS volumes from snapshots + create PVs/PVCs"
mkdir -p /tmp/resume
while read pvc ebs_old snap; do
  [ -z "$pvc" ] && continue
  # Capacity from snapshot
  size=$(aws ec2 describe-snapshots --snapshot-ids "$snap" --query 'Snapshots[0].VolumeSize' --output text)
  vol_new=$(aws ec2 create-volume --snapshot-id "$snap" --availability-zone "$AZ" \
            --volume-type gp3 --query 'VolumeId' --output text)
  aws ec2 create-tags --resources "$vol_new" --tags "Key=brainwatch-pvc,Value=$pvc" "Key=restored-from,Value=$snap"
  aws ec2 wait volume-available --volume-ids "$vol_new"
  echo "    $pvc  ($size GiB)  $snap → $vol_new"

  pv="pv-$pvc"
  cat > "/tmp/resume/$pv.yaml" <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: $pv
  labels:
    brainwatch-pvc: $pvc
spec:
  capacity:
    storage: ${size}Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: gp3
  csi:
    driver: ebs.csi.aws.com
    volumeHandle: $vol_new
    fsType: ext4
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: topology.kubernetes.io/zone
              operator: In
              values: ["$AZ"]
  claimRef:
    namespace: $NAMESPACE
    name: $pvc
EOF
  kubectl apply -f "/tmp/resume/$pv.yaml"

  cat > "/tmp/resume/pvc-$pvc.yaml" <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $pvc
  namespace: $NAMESPACE
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: gp3
  volumeName: $pv
  resources:
    requests:
      storage: ${size}Gi
EOF
  kubectl apply -f "/tmp/resume/pvc-$pvc.yaml"
done < "$INVENTORY"

echo "==> 6. AWS credentials secret (in-cluster for S3 sidecars)"
kubectl -n "$NAMESPACE" create secret generic aws-credentials \
  --from-literal=access-key-id="$AWS_ACCESS_KEY_ID" \
  --from-literal=secret-access-key="$AWS_SECRET_ACCESS_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> 7. Apply infra workloads (Cassandra, Kafka, real pipeline, Grafana)"
kubectl apply -f infra/k8s/cassandra-statefulset.yaml
kubectl apply -f infra/cloud/k8s-overlays/kafka-kraft.yaml
kubectl apply -f infra/cloud/k8s-overlays/grafana.yaml
kubectl -n "$NAMESPACE" create configmap grafana-dashboard-brainwatch \
  --from-file=brainwatch.json=infra/cloud/grafana-dashboard.json \
  --from-file=brainwatch-pipeline.json=infra/cloud/grafana-pipeline-dashboard.json \
  --from-file=brainwatch-about.json=infra/cloud/grafana-about-dashboard.json \
  --from-file=brainwatch-insights.json=infra/cloud/grafana-insights-dashboard.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f infra/cloud/k8s-overlays/real-pipeline.yaml

echo "==> 8. Open Grafana NodePort"
SG=$(aws ec2 describe-instances --filters "Name=tag:eks:cluster-name,Values=$CLUSTER" \
      --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 30300 \
  --cidr 0.0.0.0/0 --tag-specifications 'ResourceType=security-group-rule,Tags=[{Key=brainwatch,Value=grafana}]' \
  2>/dev/null || true

NODE_IP=$(kubectl get node -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
echo ""
echo "==> Done. Grafana at http://$NODE_IP:30300 (admin / brainwatch)"
echo "    Pods:"
kubectl -n "$NAMESPACE" get pods,pvc
