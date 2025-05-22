# ACI Container Deployer

This `deploy-aci` script automates the deployment of a Docker container to Azure Container Instances (ACI) using the Azure CLI. It supports both public and private container images, including those from GitHub Container Registry.

- Deploys containers to Azure using ARM templates
- Supports public and private container images
- Automatically handles ACR authentication for private images
- Injects an SSH key for remote access (if needed)
- Outputs the public IP of the container after deployment

---

## Prerequisites

The following needs to be installed:

- Python
- Docker
- Azure CLI

### One-time Setup

1. **Login to Azure:**
   ```bash
   az login
   ```

2. **Create a Resource Group (if not already created):**
   ```bash
   az group create --name my-resource-group --location eastus
   ```

3. **Generate an SSH key (if not already present):**
   ```bash
   ssh-keygen -t rsa
   ```

---

## Usage

```bash
./deploy-aci \
  --resource-group <resource-group-name> \
  --image <image-name> \
  [--name <container-name>] 
```

Sample public test image: `ghcr.io/cjen1-msft/scitt-snp:prebaked-latest`

This will:

- Determine if the image is public or private
- Deploy it using the appropriate ARM template
- Output the container’s public IP address

---
