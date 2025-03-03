#!/usr/bin/env bash

set -x

ResourceGroup="$1"
ImageFQN="$2"

ImageTitle="$(echo $ImageFQN | sed 's/.*azurecr\.io\///' | sed 's/:.*//')"
ACRPrefix="$(echo $ImageFQN | sed 's/\.azurecr\.io.*//')"

DeploymentName="$(echo "${ResourceGroup}-${ImageTitle}" | sed 's/_/-/g')"

## Build primary container
docker login \
  -u 00000000-0000-0000-0000-000000000000 \
  -p $(az acr login --name ${ACRPrefix} --expose-token --output tsv --query accessToken) \
  ${ACRPrefix}.azurecr.io
docker push $ImageFQN

# Deploy primary container and sidecar using ./arm-template.json
az deployment group create \
  --resource-group $ResourceGroup \
  --template-file arm-template.json \
  --parameters name=${DeploymentName} \
  --parameters image="${ImageFQN}" \
  --parameters ssh="$(cat ~/.ssh/id_rsa.pub)" \
  --parameters acr-repo="${ACRPrefix}.azurecr.io" \
  --parameters acr-token="$(az acr login --name $ACRPrefix --expose-token --output tsv --query accessToken)"

echo Hosted container on: $(az container show -g $ResourceGroup -n ${DeploymentName} --query ipAddress.ip -o tsv)
