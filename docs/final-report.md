# BrainWatch — Final Report

> **Note:** Sections below are assigned per owner. Merge via PR before Wed 21:00.
> - Section 1–3: Quang-Hùng (Architecture + Editor)
> - Section 4–5: Kim-Quân (Batch Layer + Performance)
> - Section 6–7: Kim-Hùng (Speed Layer + Serving)
> - **Section 8: Đạt (Deployment + Infrastructure)** ← this file
> - Section 9: Trang (Testing, Results, Demo)

---

## 8. Deployment & Infrastructure

### 8.1 Kubernetes Topology

The BrainWatch platform runs entirely inside a dedicated Kubernetes namespace `brainwatch`. All workloads are defined as first-class Kubernetes objects so that the cluster scheduler handles restarts, scheduling, and resource isolation automatically.

```
┌──────────────────────────────────────────────────────────────────┐
│  Kubernetes namespace: brainwatch                                │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Persistent Storage (PVCs)                              │    │
│  │   bronze-pvc │ silver-pvc │ gold-pvc │ checkpoints-pvc  │    │
│  │                          cassandra-data (StatefulSet)   │    │
│  └───────────────────────────────┬─────────────────────────┘    │
│                                  │ volumeMount                   │
│  ┌───────────────┐   ┌───────────▼──────────┐                   │
│  │  CronJob      │   │  StatefulSet         │                   │
│  │  spark-batch  │   │  cassandra (1 pod)   │                   │
│  │  (daily 03:00)│   │  cassandra-0         │                   │
│  │  bitnami/     │   │  :9042 (CQL)         │                   │
│  │  spark:3.5    │   │  :7000 (intra-node)  │                   │
│  └───────┬───────┘   └──────────┬───────────┘                   │
│          │ spark-submit          │ headless Service              │
│          │ run_batch.py          │ cassandra-svc                 │
│  ┌───────▼───────────────────────▼───────────┐                  │
│  │  Deployment: spark-streaming (1 replica)  │                  │
│  │  bitnami/spark:3.5                        │                  │
│  │  spark-submit -m brainwatch.processing    │                  │
│  │               .speed_layer               │                  │
│  │  Service: spark-streaming-ui :4040       │                  │
│  └───────────────────────────────────────────┘                  │
│                                                                  │
│  ConfigMap: brainwatch-config  (env for all pods)               │
└──────────────────────────────────────────────────────────────────┘
```

**Data flow through the cluster:**

1. Kafka brokers (external to namespace) push EEG/EHR events.
2. `spark-streaming` Deployment reads from Kafka, writes results to Cassandra and the bronze PVC.
3. The `spark-batch` CronJob runs nightly at 03:00 UTC, reads bronze → produces silver → gold zones.
4. Cassandra serves the alert query layer for the speed path.

---

### 8.2 Deployment Runbook

All manifests live under `infra/k8s/`. Use `deploy.sh` to apply them in the correct dependency order.

#### Prerequisites

```bash
# 1. kubectl configured to point at the brainwatch cluster
kubectl config current-context   # should show: brainwatch

# 2. Clone the repo and move into the k8s folder
git clone <repo-url>
cd Big-Data-Project
```

#### Full deploy

```bash
# Dry-run first (safe — no changes applied)
bash infra/k8s/deploy.sh --dry-run

# Apply for real
bash infra/k8s/deploy.sh
```

The script applies manifests in this order and waits for each workload to become Ready before proceeding:

| Step | Manifest | Wait condition |
|------|----------|---------------|
| 1 | `namespace.yaml` | — |
| 2 | `configmap.yaml` | — |
| 3 | `persistent-volumes.yaml` | PVCs must exist before pods |
| 4 | `cassandra-statefulset.yaml` | `rollout status statefulset/cassandra` (timeout 300 s) |
| 5 | `spark-streaming-deployment.yaml` | `rollout status deployment/spark-streaming` (timeout 300 s) |
| 6 | `spark-batch-cronjob.yaml` | No wait — CronJob registers a schedule, no pod runs immediately |

After deploy completes, the script prints:

```bash
kubectl -n brainwatch get pods,svc,pvc,statefulset,deployment,cronjob
```

#### Verify the speed layer

```bash
# Check Spark Streaming is running
kubectl -n brainwatch get pods -l app=spark-streaming

# Open Spark UI (port-forward to localhost:4040)
kubectl -n brainwatch port-forward svc/spark-streaming-ui 4040:4040
```

#### Teardown

```bash
# Remove all workloads but KEEP persistent data (safe default)
bash infra/k8s/teardown.sh

# Remove everything including bronze/silver/gold data (irreversible)
bash infra/k8s/teardown.sh --delete-pvcs
# → prompts for confirmation twice before deleting
```

---

### 8.3 Resource Budget

| Workload | Kind | Replicas | CPU request | CPU limit | Memory request | Memory limit | Storage |
|----------|------|----------|-------------|-----------|----------------|--------------|---------|
| `cassandra` | StatefulSet | 1 | 500 m | 2 | 1 Gi | 4 Gi | 20 Gi (PVC) |
| `spark-streaming` | Deployment | 1 | 1 | 2 | 2 Gi | 4 Gi | — |
| `spark-batch` | CronJob | 1 (on schedule) | 1 | 2 | 2 Gi | 4 Gi | — |
| **Total (peak)** | | **3 pods** | **2.5** | **6** | **5 Gi** | **12 Gi** | **20 Gi** |

**PVC allocation:**

| PVC | Purpose | Access mode |
|-----|---------|-------------|
| `bronze-pvc` | Raw ingested EEG/EHR data | ReadWriteOnce |
| `silver-pvc` | Cleaned, deduplicated data | ReadWriteOnce |
| `gold-pvc` | Business-ready feature tables | ReadWriteOnce |
| `checkpoints-pvc` | Spark Structured Streaming checkpoints | ReadWriteOnce |
| `cassandra-data` | Cassandra alert store | ReadWriteOnce (StatefulSet) |

> All pods share environment variables via `ConfigMap brainwatch-config`. No secrets are stored in manifests.
