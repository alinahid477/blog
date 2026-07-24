# Kagenti Deployment — kubernetes-mcp-server & k8s-namespace-monitor-agent

This directory contains the Kubernetes manifests and scripts for deploying
the **kubernetes-mcp-server** (MCP tool) and the **k8s-namespace-monitor-agent**
(A2A agent) on an OpenShift cluster running the
[Kagenti](https://github.com/kagenti/kagenti) platform with
[MCP Gateway](https://github.com/Kuadrant/mcp-gateway).

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            OpenShift Cluster                                     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │  agents namespace                                                           │ │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │ │
│  │  │ k8s-namespace-monitor-agent pod (3/3)                                │   │ │
│  │  │   agent ─── envoy-proxy (AuthBridge) ─── spiffe-helper               │   │ │
│  │  │                   │                                                  │   │ │
│  │  │                   │ outbound token exchange                          │   │ │
│  │  │                   │ (injects Keycloak JWT)                           │   │ │
│  │  └───────────────────┼──────────────────────────────────────────────────┘   │ │
│  └──────────────────────┼──────────────────────────────────────────────────────┘ │
│                         │ HTTP + Bearer JWT                                      │
│                         ▼ port 8081 (mcp-gateway-for-agents svc)                 │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │  gateway-system namespace                                                   │ │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │ │
│  │  │ mcp-gateway-istio (Envoy)  ←── Kuadrant AuthPolicy (JWT required)    │   │ │
│  │  │   │                                                                  │   │ │
│  │  │   │ ext_proc: broker/router identifies tool prefix                   │   │ │
│  │  └───┼──────────────────────────────────────────────────────────────────┘   │ │
│  └──────┼──────────────────────────────────────────────────────────────────────┘ │
│         │                                                                        │
│         ▼                                                                        │
│  ┌─────────────────────────────────────────────────────────────────────────────┐ │
│  │  mcp-servers namespace                                                      │ │
│  │  ┌──────────────────────────────────────────────────────────────────────┐   │ │
│  │  │ kubernetes-mcp-server pod (3/3)                                      │   │ │
│  │  │   kubernetes-mcp-server ─── authbridge-proxy ─── spiffe-helper       │   │ │
│  │  │        │                         │                                   │   │ │
│  │  │   port 9091 (direct)       port 9090 (JWT-protected)                 │   │ │
│  │  │   broker uses this         external clients use this                 │   │ │
│  │  └──────────────────────────────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
│  mcp-system: mcp-gateway-controller, mcp-gateway (broker)                        │
│  keycloak:   Keycloak (kagenti realm, OIDC/JWT issuer)                           │
│  spire:      SPIRE server + agents (SPIFFE workload identity)                    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Why Two Ports (9090 vs 9091)?

The kubernetes-mcp-server pod runs with an **AuthBridge sidecar** that
intercepts all traffic on port 9090 via iptables. AuthBridge validates
incoming JWTs before forwarding to the app on port 9091.

The MCP Gateway broker forwards tool calls to the upstream MCP server, but
its ext_proc pipeline **does not attach the `credentialRef` token** to
outgoing requests. This means:

- **Port 9090** (AuthBridge): Requires a Bearer JWT. Works for external
clients and direct agent calls. Fails for broker-forwarded tool calls
because the broker sends no `Authorization` header.
- **Port 9091** (direct to app): Bypasses AuthBridge. No JWT required.
The broker connects here successfully.

Port 9091 is exposed via the `kubernetes-mcp-server-internal` Service and
protected by the Istio ambient mesh (mTLS via ztunnel).

## Why MCP Gateway Authentication?

With the broker connecting to the upstream on port 9091 (no JWT), the
upstream is accessible without application-layer auth from the broker's
perspective. But this leaves the MCP Gateway's own public endpoint
unprotected — any caller (external or in-cluster) could invoke tools
through the gateway without credentials.

To close this gap, we deploy a **Kuadrant AuthPolicy** on the gateway's
public `mcp` listener that requires a valid Keycloak JWT on all requests.
The agent's AuthBridge sidecar handles token acquisition automatically via
outbound token exchange.

## Deployment

### kubernetes-mcp-server (MCP tool)

Deployed in two phases:

```bash
# Phase 1: Core tool deployment (namespace, RBAC, deployment, services)
bash kagenti/k8s-mcp-server/deploy-k8s-mcp-server.sh --phase toolonly

# Phase 2: MCP Gateway integration (HTTPRoute, MCPServerRegistration, AuthPolicy)
bash kagenti/k8s-mcp-server/deploy-k8s-mcp-server.sh --phase gatewayintegration
```

### k8s-namespace-monitor-agent (A2A agent)

```bash
bash kagenti/k8s-namespace-monitor-agent/deploy-agent.sh
```

### Teardown

```bash
# Agent
bash kagenti/k8s-namespace-monitor-agent/delete-agent.sh

# MCP server (reverse order)
bash kagenti/k8s-mcp-server/delete-k8s-mcp-server.sh --phase gatewayintegration
bash kagenti/k8s-mcp-server/delete-k8s-mcp-server.sh --phase toolonly
```

## File Reference

### `kagenti/k8s-mcp-server/` — MCP Tool Manifests


| File                                               | Type                          | Deployed       | Description                                                                                                                                                                                                                    |
| -------------------------------------------------- | ----------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `01-namespace.yaml`                                | Namespace                     | Yes — toolonly | Creates `mcp-servers` namespace with Istio ambient mesh labels.                                                                                                                                                                |
| `02-openshift-rbac.yaml`                           | SA, ClusterRole, RoleBindings | Yes — toolonly | ServiceAccount with cluster-level namespace read and admin access to `dbapps` and `simple-webserver` namespaces.                                                                                                               |
| `02a-mcp-servers-authbridge-scc.yaml`              | RoleBinding                   | Yes — toolonly | Binds the `kagenti-authbridge` SCC to the AuthBridge service account in `mcp-servers`.                                                                                                                                         |
| `02b-system-openshift-scc-kagenti-authbridge.yaml` | SCC                           | Yes — toolonly | OpenShift SecurityContextConstraints required by the AuthBridge init container (NET_ADMIN for iptables).                                                                                                                       |
| `03a-deployment.yaml`                              | Deployment, Services          | Yes — toolonly | kubernetes-mcp-server Deployment (with AuthBridge + SPIFFE sidecars) and two Services: port 9090 (AuthBridge) and port 8000→9091 (kagenti convention).                                                                         |
| `03b-k8s-mcp-server-service-internal.yaml`         | Service                       | Yes — toolonly | `kubernetes-mcp-server-internal` Service exposing port 9091 directly, bypassing AuthBridge. Used by the MCP Gateway broker.                                                                                                    |
| `04-agent-runtime.yaml`                            | AgentRuntime                  | Yes — toolonly | Enrolls the Deployment as a Kagenti tool (`type: tool`). The operator adds labels, config-hash, and manages lifecycle.                                                                                                         |
| `05-mcp-servers-reference-grant.yaml`              | ReferenceGrant                | Yes — gateway  | Allows HTTPRoutes in `mcp-system` to reference Services in `mcp-servers` (cross-namespace Gateway API requirement).                                                                                                            |
| `06b-httproute-internal.yaml`                      | HTTPRoute                     | Yes — gateway  | Routes to port 9091 via `kubernetes-mcp-server-internal`. Attached to the `mcps` listener. Used by the MCPServerRegistration.                                                                                                  |
| `07a-create-or-patch-mcp-broker-token.sh`          | Script                        | No             | *Creates/patches the `kubernetes-mcp-access-token` secret with a Keycloak JWT. Not needed when using port 9091 because the MCPServerRegistration has no `credentialRef`.*                                                      |
| `07b-IMP-patch-to-istio-mode-none.sh`              | Script                        | No             | *Patches the mcp-gateway deployment with `istio.io/dataplane-mode: none`. Not needed — the gateway pod already has this label by default.*                                                                                     |
| `07d-mcpserverregistration-internal.yaml`          | MCPServerRegistration         | Yes — gateway  | Registration without `credentialRef` (port 9091 path). Broker connects directly to the app, no JWT needed.                                                                                                                     |
| `08a-gateway-authentication-enable.sh`             | Script                        | No             | *Patches MCPGatewayExtension with `oauthProtectedResource`. Not used — the installed CRD version does not support this field; AuthPolicy (`08b`) is used instead.*                                                             |
| `08b-gateway-auth-policy.yaml`                     | AuthPolicy                    | Yes — gateway  | Kuadrant AuthPolicy requiring JWT validation on the gateway's `mcp` listener. Unauthenticated requests return 401.                                                                                                             |
| `08c-mcp-gateway-for-agents.yaml`                  | Service                       | Yes — gateway  | `mcp-gateway-for-agents` Service mapping port 8081→8080. Needed because port 8080 is excluded from AuthBridge iptables interception; traffic on 8081 passes through the sidecar so outbound token exchange can inject the JWT. |
| `00-keycloak-client-for-MCPServer.sh`              | Script                        | Manual         | One-time Keycloak client creation for the MCP server. Run manually before first deployment.                                                                                                                                    |
| `deploy-k8s-mcp-server.sh`                         | Script                        | —              | Orchestrates deployment in two phases: `toolonly` and `gatewayintegration`.                                                                                                                                                    |
| `delete-k8s-mcp-server.sh`                         | Script                        | —              | Tears down resources in reverse order per phase.                                                                                                                                                                               |
| `test-mcp-gateway.sh`                              | Script                        | —              | End-to-end test: initialize → tools/list → tools/call. Supports `--option secured` (with JWT) and `--option insecured` (without).                                                                                              |
| `test-k8s-mcp-server.sh`                           | Script                        | —              | Tests the MCP server directly (bypasses gateway). Fetches a Keycloak token and calls the server via `kubectl run`.                                                                                                             |
| `show-mcp-log.sh`                                  | Script                        | —              | Streams logs from a named container in the kubernetes-mcp-server pod.                                                                                                                                                          |
| `find-spiffe-trust-domain.sh`                      | Script                        | —              | Discovers the Istio mesh trust domain from ztunnel.                                                                                                                                                                            |
| `find-relevant-urls.sh`                            | Script                        | —              | Prints key cluster URLs (Keycloak, gateway, MCP server).                                                                                                                                                                       |


### `kagenti/k8s-namespace-monitor-agent/` — Agent Manifests


| File                                               | Type                | Description                                                                                                                                               |
| -------------------------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `01-namespace.yaml`                                | Namespace           | Creates `agents` namespace with Istio ambient mesh labels and waypoint proxy.                                                                             |
| `02-rbac.yaml`                                     | SA, Secrets         | ServiceAccount and Secrets (OpenAI API key, Telegram bot token) for the agent.                                                                            |
| `02a-agent-authbridge-scc.yaml`                    | RoleBinding         | Binds the `kagenti-authbridge` SCC to the AuthBridge service account in `agents`.                                                                         |
| `02b-system-openshift-scc-kagenti-authbridge.yaml` | SCC                 | OpenShift SecurityContextConstraints for the AuthBridge init container.                                                                                   |
| `03-deployment.yaml`                               | Deployment, Service | Agent Deployment (with AuthBridge + SPIFFE sidecars) and ClusterIP Service on port 8000. `MCP_SERVER_URL` points to `mcp-gateway-for-agents:8081`.        |
| `04-agentruntime.yaml`                             | AgentRuntime        | Enrolls the Deployment as a Kagenti agent (`type: agent`). Triggers operator-managed Keycloak client registration and sidecar configuration.              |
| `05-authproxy-routes.yaml`                         | ConfigMap           | `authproxy-routes` — tells the AuthBridge sidecar to perform outbound token exchange for requests to the MCP Gateway (`mcp-gateway-for-agents:8081`).     |
| `deploy-agent.sh`                                  | Script              | Orchestrates agent deployment: namespace → RBAC → SCC → deployment (wait 3/3) → AgentRuntime → authproxy-routes.                                         |
| `delete-agent.sh`                                  | Script              | Tears down agent resources in reverse order.                                                                                                              |
| `debug-agent-to-mcp.sh`                            | Script              | Comprehensive debug tool: deploys a netshoot pod and runs curl tests against the MCP server (no auth → with auth → bypass AuthBridge), then inspects ConfigMaps and sidecar logs. |
| `test-k8s-monitor-agent.sh`                        | Script              | Sends an A2A `message/send` request to the agent to trigger a monitoring run.                                                                             |
| `show-agent-log.sh`                                | Script              | Streams agent container logs.                                                                                                                             |


### `kagenti/` — Top-Level Documentation


| File                                   | Description                                                                                                                                               |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enable-mcp-gateway-authentication.md` | Detailed guide for the MCP Gateway authentication setup: AuthPolicy, proxy Service on port 8081, AuthBridge outbound token exchange, and debugging steps. |
| `restart-my-setup.sh`                  | Convenience script to restart all deployed components.                                                                                                    |


