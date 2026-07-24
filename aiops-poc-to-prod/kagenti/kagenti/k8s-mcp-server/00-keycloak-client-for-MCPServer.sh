#!/bin/bash

echo "Creating Keycloak client for MCPServerRegistration..."

echo "Getting Token..."

TOKEN=$(curl -s -X POST "https://keycloak-keycloak.apps.<your-cluster-domain>/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=temp-admin" \
  -d "password=your-password-here" \
  -d "grant_type=password" | jq -r '.access_token')

echo $TOKEN

echo "Creating Client..."

curl -X POST "https://keycloak-keycloak.apps.<your-cluster-domain>/admin/realms/kagenti/clients" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "mcp-gateway-custom",
    "name": "mcp-gateway-custom",
    "publicClient": false,
    "standardFlowEnabled": true,
    "directAccessGrantsEnabled": true,
    "serviceAccountsEnabled": true,
    "attributes": {
      "standard.token.exchange.enabled": "true"
    }
  }'

  echo "Client created successfully"