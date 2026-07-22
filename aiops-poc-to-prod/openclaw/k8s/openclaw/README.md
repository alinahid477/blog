# openclaw — Kubernetes / OpenShift Deployment

Kustomize-based manifests for deploying openclaw on OpenShift (or any Kubernetes 1.25+ cluster).

## Prerequisites

- `oc` / `kubectl` connected to your cluster
- `kustomize` (or `kubectl apply -k`)
- Image pushed and accessible: `quay.io/<your-quay-username>/myopenclaw:20260617-1`
- StorageClass `lvms-vg-ssd-1` available (used for the 10Gi PVC)

---

## ⚠️ Before You Deploy

### 1. Replace the gateway token (critical)

The current `OPENCLAW_GATEWAY_TOKEN` in `secret.yaml` is weak. Generate a strong one:

```bash
openssl rand -hex 32
```

Replace the value of `OPENCLAW_GATEWAY_TOKEN` in `secret.yaml`.

### 2. Grant `anyuid` SCC (OpenShift only)

The openclaw image runs as UID 1000 (hardcoded in the `node:24-bookworm` base image).
OpenShift's default `restricted-v2` SCC disallows fixed UIDs. Grant `anyuid` to the
`openclaw` ServiceAccount **after the namespace is created**:

```bash
oc adm policy add-scc-to-user anyuid -z openclaw -n openclaw
```

Run this before (or immediately after) the first `kubectl apply -k`.

---

## Deploy

```bash
# Grant anyuid SCC (OpenShift)
oc adm policy add-scc-to-user anyuid -z openclaw -n openclaw

# From the repo root:
kubectl apply -k k8s/openclaw/

# Watch the pod come up
kubectl get pods -n openclaw -w
```

The init container seeds config/workspace files from ConfigMaps into the PVC on first
boot, then exits. The main container starts the openclaw gateway bound to `0.0.0.0:18789`.

---

## Access

### Via OpenShift Route (HTTPS — primary)

```
https://openclaw.apps.<your-cluster-domain>
```

Log in with the gateway token from `secret.yaml`.

### Via port-forward (debug / local)

```bash
kubectl port-forward svc/openclaw 18789:18789 -n openclaw
open http://localhost:18789
```

---

## Architecture

```
Namespace: openclaw
├── ServiceAccount/openclaw          No SA token automount
├── Secret/openclaw-secrets          Gateway token, Telegram, OC creds, AAP creds
├── ConfigMap/openclaw-config        openclaw.json (k8s-adapted)
├── ConfigMap/openclaw-workspace     AGENTS.md, SOUL.md, USER.md, IDENTITY.md,
│                                    TOOLS.md, HEARTBEAT.md
├── ConfigMap/openclaw-cron          cron/jobs.json (every-2h k8s-namespace-monitor)
├── PersistentVolumeClaim/openclaw-data   10Gi lvms-vg-ssd-1 (agent state)
├── Deployment/openclaw
│   ├── initContainer: init-config   Seeds PVC from ConfigMaps on first boot
│   └── container: openclaw          node dist/index.js gateway --bind lan --port 18789
├── Service/openclaw                 ClusterIP :18789 (gateway), :18790 (bridge)
└── Route/openclaw                   edge TLS → https://openclaw.apps.<your-cluster-domain>
```

### Volume layout inside the pod

```
/home/node/.openclaw/          ← PVC (persistent, read-write)
  openclaw.json                  seeded from ConfigMap/openclaw-config
  workspace/
    AGENTS.md                    seeded from ConfigMap/openclaw-workspace
    SOUL.md
    USER.md
    IDENTITY.md
    TOOLS.md
    HEARTBEAT.md
    memory/                      written by agent at runtime
  cron/
    jobs.json                    seeded from ConfigMap/openclaw-cron
/tmp/                          ← emptyDir (ephemeral, reset on restart)
  aap/                           vms-monitor writes compliance/remediation reports here
  k8s-monitoring-agent/logs/     k8s-namespace-monitor writes log files here
/app/skills/                   ← baked into the image (read-only)
  k8s-namespace-monitor/
  vms-monitor/
```

---

## Updating config or workspace files

**Gateway config** (`openclaw.json`):

```bash
# Edit configmap-config.yaml, then:
kubectl apply -k k8s/
# The init container only seeds on first boot (cp -n). To force a re-seed:
kubectl exec -n openclaw deploy/openclaw -- rm /home/node/.openclaw/openclaw.json
kubectl rollout restart deployment/openclaw -n openclaw
```

**Workspace markdown files** (AGENTS.md, SOUL.md, etc.):

```bash
# Edit configmap-workspace.yaml, then apply + force re-seed as above.
# Or edit the file directly in the PVC via exec:
kubectl exec -n openclaw deploy/openclaw -- vi /home/node/.openclaw/workspace/AGENTS.md
```

**Rotate gateway token**:

```bash
NEW_TOKEN=$(openssl rand -hex 32)
kubectl patch secret openclaw-secrets -n openclaw \
  -p "{\"stringData\":{\"OPENCLAW_GATEWAY_TOKEN\":\"$NEW_TOKEN\"}}"
kubectl rollout restart deployment/openclaw -n openclaw
echo "New token: $NEW_TOKEN"
```

---

## Teardown

```bash
# Deletes everything including the PVC (agent state will be lost)
kubectl delete namespace openclaw
```

---

## Known limitations


| Limitation                         | Detail                                                                                                                                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No sandbox isolation**           | `agents.defaults.sandbox.mode: off` — the docker-based sandbox doesn't work without a docker socket. Skills run directly in the pod.                                                                     |
| **Single replica only**            | PVC is `ReadWriteOnce`. Horizontal scaling is not supported.                                                                                                                                             |
|                                    |                                                                                                                                                                                                          |
|                                    |                                                                                                                                                                                                          |
| **Skill credentials in ConfigMap** | `OC_PASS` and `AAP_TOKEN` appear in `openclaw.json` (in the ConfigMap) because openclaw passes them to skill processes via `skills.entries.*.env`. They are also in the Secret for direct env injection. |


