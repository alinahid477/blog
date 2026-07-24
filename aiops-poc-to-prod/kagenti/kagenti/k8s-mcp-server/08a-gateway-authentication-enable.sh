#! /bin/bash

# This tells the broker to respond with OAuth discovery information:


kubectl patch mcpgatewayextension mcp-gateway -n mcp-system --type merge -p '
spec:
  oauthProtectedResource:
    resourceName: "MCP Server"
    resource: "https://mcp-gateway-gateway-system.apps.<your-cluster-domain>/mcp"
    authorizationServers:
      - "https://keycloak-keycloak.apps.<your-cluster-domain>/realms/kagenti"
    bearerMethodsSupported:
      - "header"
    scopesSupported:
      - "openid"
      - "profile"
      - "email"
'

# reverse patch to remove the authentication:

# kubectl patch mcpgatewayextension mcp-gateway -n mcp-system --type json \
#   -p='[{"op":"remove","path":"/spec/oauthProtectedResource"}]'

# verify the patch:

kubectl get mcpgatewayextension mcp-gateway -n mcp-system -o yaml | grep -A10 oauthProtectedResource


# NOTE: IMPORTANT:
# Your CRD version doesn't have oauthProtectedResource. 
#That feature is in a newer version of mcp-gateway than what's installed.

# Skip Step 2 entirely — 
# it's only for OAuth discovery metadata (/.well-known/oauth-protected-resource), 
# which is optional. The core protection (Step 3 — AuthPolicy with JWT validation) 
# works independently. Just apply the AuthPolicy directly and it will enforce JWT 
# validation on the gateway's mcp listener without needing the OAuth metadata 
#endpoint.