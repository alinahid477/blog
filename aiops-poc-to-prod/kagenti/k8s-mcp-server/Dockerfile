# syntax=docker/dockerfile:1
#
# Builds the upstream kubernetes-mcp-server from source.
# Source: https://github.com/containers/kubernetes-mcp-server
#
# This is the pure-Kubernetes variant. It covers all the tools used by
# k8s-namespace-monitor-agent (namespaces_list, deployment_logs, resources_*)
# but does NOT include OpenShift-specific toolsets (Routes, Projects, Builds,
# Tekton, …).  Use mcp-servers/openshift/ if you need those.
#
# Build:
#   docker build -f mcp-servers/k8s/Dockerfile -t kubernetes-mcp-server-local .
#
# Run:
#   docker run --rm \
#     -v ~/.kube:/root/.kube:ro \
#     -p 9090:9090 \
#     kubernetes-mcp-server-local
#
# Then set in .env:  MCP_SERVER_URL=http://localhost:9090/sse

# ── Build stage ────────────────────────────────────────────────────────────────
FROM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /src

# Clone the upstream kubernetes-mcp-server repository.
RUN git clone --depth=1 https://github.com/containers/kubernetes-mcp-server.git .

# Static binary – no CGO required.
RUN CGO_ENABLED=0 GOOS=linux \
    go build \
      -trimpath \
      -ldflags="-s -w" \
      -o kubernetes-mcp-server \
      ./cmd/kubernetes-mcp-server

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM alpine:3.21

RUN apk add --no-cache ca-certificates

WORKDIR /app

COPY --from=builder /src/kubernetes-mcp-server /app/kubernetes-mcp-server
COPY mcp_config.toml           /app/mcp_config.toml

# Kubeconfig must be supplied at runtime – never bake credentials into the image.
#
# Option A – mount entire .kube directory (simplest):
#   docker run -v ~/.kube:/root/.kube:ro ...
#
# Option B – single file via KUBECONFIG env var:
#   docker run -e KUBECONFIG=/kubeconfig -v ~/.kube/config:/kubeconfig:ro ...
#
# Option C – in-cluster (OpenShift/Kubernetes deployment):
#   No volume needed. The pod's ServiceAccount token is mounted automatically.

# OpenShift runs pods with arbitrary UIDs from the root group (GID 0).
# Granting group ownership and equal group permissions allows the binary to
# execute under any UID assigned by the platform without root privileges.
RUN chown -R 0:0 /app && chmod -R g=u /app

USER 1001

EXPOSE 9090

# Use shell form so ${PORT:-9090} is expanded at container start.
# The PORT env var overrides the port in mcp_config.toml at runtime:
#   - Local dev (no PORT set):            listens on 9090 (default)
#   - OpenShift/Kagenti (PORT=9091 injected by operator): listens on 9091
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["/app/kubernetes-mcp-server --config /app/mcp_config.toml --port ${PORT:-9090}"]
