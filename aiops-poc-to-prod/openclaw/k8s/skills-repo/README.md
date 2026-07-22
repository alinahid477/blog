# skills-repo — Kubernetes Deployment

Deploys the `skills-repo` container into the `openclaw` namespace.
The pod clones a git repository, packages the `skills/` subdirectory as a
tarball, and serves it over HTTP on port 8080 via a ClusterIP Service.

The openclaw pod's `init-skills` init container fetches the tarball from
`http://skills-repo:8080/skills.tar.gz` and extracts it into the agent's
workspace before openclaw starts.

---

## Prerequisites

- `openclaw` namespace already exists (created by `kubectl apply -k k8s/openclaw/`)
- Image built and pushed: `quay.io/<your-quay-username>/skills-repo:20260617-1`
  (see `skills-repo/README.md` for build + push instructions)

---

## Configure before deploying

### 1. Set the git repo URL and branch — `configmap.yaml`

Edit `configmap.yaml` and set:

```yaml
data:
  GIT_REPO_URL: "https://github.com/your-org/your-skills-repo.git"
  GIT_BRANCH: "main"
```

### 2. Supply git credentials via `.env.secrets` (gitignored var file)

Credentials are **not** stored in any committed YAML. Kustomize reads them
from a local `.env.secrets` file at apply time.

```bash
# One-time setup: copy the example and fill in real values
cp k8s/skills-repo/.env.secrets.example k8s/skills-repo/.env.secrets
```

Edit `.env.secrets`:

```dotenv
GIT_USERNAME=your-git-username
GIT_PASSWORD=your-personal-access-token
```

For GitHub, the PAT needs only the `repo` (read) scope.
For GitLab, use a Project Access Token with `read_repository` scope.

`.env.secrets` is listed in `.gitignore` — it will never be committed.

---

## Deploy

```bash
kubectl apply -k k8s/skills-repo/

oc adm policy add-scc-to-user anyuid -z skills-repo -n openclaw

```

Watch the pod come up and clone the repo:

```bash
kubectl get pods -n openclaw -l app.kubernetes.io/name=skills-repo -w
```

Tail the clone + HTTP server logs:

```bash
kubectl logs -n openclaw -l app.kubernetes.io/name=skills-repo -f
```

Expected output once healthy:

```
[skills-repo] Cloning...
[skills-repo] Clone complete. Contents of /skills: ...
[skills-repo] Skills directory contents (/skills/skills): ...
[skills-repo] Creating tarball at /var/www/skills.tar.gz ...
[skills-repo] Tarball ready (12K).
[skills-repo] Serving /var/www on port 8080
[skills-repo] Skills tarball: http://<service>:8080/skills.tar.gz
```

---

## Verify the tarball is being served

From any pod in the `openclaw` namespace (e.g. exec into openclaw):

```bash
kubectl exec -n openclaw deploy/openclaw -- \
  curl -s -o /tmp/skills.tar.gz http://skills-repo:8080/skills.tar.gz && \
  echo "Download OK"
```

Or from outside the cluster using port-forward:

```bash
kubectl port-forward -n openclaw svc/skills-repo 8080:8080 &
curl -o /tmp/skills.tar.gz http://localhost:8080/skills.tar.gz
tar -tzf /tmp/skills.tar.gz | head -20
```

---

## Update skills (after pushing new commits to git)

```bash
# 1. Restart skills-repo so it re-clones and regenerates the tarball
kubectl rollout restart deployment/skills-repo -n openclaw

# Wait for it to be ready
kubectl rollout status deployment/skills-repo -n openclaw

# 2. Restart openclaw so init-skills re-fetches the new tarball
kubectl rollout restart deployment/openclaw -n openclaw
kubectl rollout status deployment/openclaw -n openclaw
```

---

## Troubleshoot

| Symptom | Check |
|---|---|
| Pod stuck in `Init:0/2` | `kubectl logs -n openclaw <pod> -c init-skills` |
| Clone fails (auth error) | Verify `secret.yaml` credentials; check PAT expiry |
| `skills/` subdir not found | Ensure the repo has a `skills/` directory at root (see `skills-repo/README.md`) |
| `init-skills` times out | skills-repo pod not Ready — check its own logs first |
| skills-repo pod `CrashLoopBackOff` | `kubectl logs -n openclaw deploy/skills-repo --previous` |

---

## File structure

```
k8s/skills-repo/
├── configmap.yaml          GIT_REPO_URL, GIT_BRANCH, CLONE_DIR (non-sensitive)
├── .env.secrets.example    Template — copy to .env.secrets and fill in values
├── .env.secrets            GIT_USERNAME, GIT_PASSWORD — gitignored, never committed
├── .gitignore              Ignores .env.secrets
├── deployment.yaml         Deployment — image, envFrom, resources, securityContext
├── service.yaml            ClusterIP Service on port 8080 (DNS: skills-repo:8080)
└── kustomization.yaml      secretGenerator reads .env.secrets at apply time
```

---

## See also

- `skills-repo/` — Dockerfile and `clone-skills.sh` entrypoint script
- `k8s/openclaw/deployment.yaml` — `init-skills` init container (consumer)
