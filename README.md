# Runes

A collection of scripts and tools for working with Azure, focused on
deploying containers to Azure Container Instances and providing tooling
for SNP-based confidential computing.

## deploy-aci-arm

Deploys Docker containers to Azure Container Instances using ARM templates.
Handles VNet/NAT/load-balancer creation, SSH access, and optional Azure Files
mounts.

### Quick start

```bash
./deploy-aci-arm/deploy-aci \
  --resource-group-prefix my-rg \
  --name mycluster \
  --image ghcr.io/myrepo/myimage:latest \
  --ssh-key ~/.ssh/id_rsa.pub \
  --sku standard \
  --num-containers 3
```

This creates a resource group `my-rg-mycluster`, deploys 3 container groups
each with a VNet, NAT gateway, and load balancer, then prints the SSH
commands and public/private IP mappings.

### Parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--resource-group <name>` | — | Deploy into this exact resource group. Mutually exclusive with `--resource-group-prefix`. |
| `--resource-group-prefix <prefix>` | — | Derive the resource group as `<prefix>-<name>`. Mutually exclusive with `--resource-group`. |
| `--image <image>` | — | Container image. Required unless `--delete`. |
| `--ssh-key <path>` | — | SSH public key file. Required unless `--delete`. |
| `--name <name>` | random | Deployment name. Used in resource naming. |
| `--region <region>` | `northeurope` | Azure region. |
| `--sku <sku>` | `confidential` | `confidential` or `standard`. |
| `--num-containers <n>` | `1` | Number of container groups to deploy. |
| `--cpus <n>` | `4` | CPUs per container. |
| `--ram <n>` | `16` | RAM (GB) per container. |
| `--tcp-ports <ports>` | `22` | Comma-separated TCP ports to open. |
| `--udp-ports <ports>` | — | Comma-separated UDP ports to open. |
| `--dry-run` | — | Print planned commands without executing. |
| `--verbose` | — | Verbose output. |
| `--delete` | — | Delete the managed resource group for this deployment. |
| `--use-existing-resource-group` | — | Treat the resource group as pre-existing; don't create or delete it. Requires `--resource-group`. |
| `--azure-auth` | — | Use `az` CLI for image registry authentication. |

### Azure Files mounts

Use `--azure-file-mount` to attach an Azure Files share to each container.
The flag accepts comma-separated `key=value` pairs:

- `share=<name>` — required; the file share name (or prefix with `--azure-file-share-prefix`)
- `path=<absolute-path>` — required; the mount path inside the container

The tool automatically creates a storage account and the specified shares in
the deployment resource group.

With `--azure-file-share-prefix`, the `share` value is treated as a prefix
and per-node shares are derived as `<prefix>-1`, `<prefix>-2`, etc.

Use `--azure-file-account-sku` (default `Standard_LRS`) to control the
storage account SKU (e.g. `Premium_LRS` for premium file shares).

```bash
./deploy-aci-arm/deploy-aci \
  --resource-group-prefix my-rg \
  --name mycluster \
  --image ghcr.io/myrepo/myimage:latest \
  --ssh-key ~/.ssh/id_rsa.pub \
  --sku standard \
  --num-containers 2 \
  --azure-file-share-prefix \
  --azure-file-mount share=workspace,path=/mnt/workspace
```

This creates shares `workspace-1` and `workspace-2` mounted at
`/mnt/workspace` in each container.

## docker-attestation-tools

A Docker image for working with SNP-based systems, optimised specifically
for CCF. Built on Azure Linux 3.0.

### Image contents

- **SNP report tools** (`bin/`): `get-snp-report`, `get-fake-snp-report`,
  `hex2report`, `verbose-report`
- **Scripts** (`scripts/`): AMD collateral fetching, attestation stashing,
  log processing, dev VM setup

### Dev VM setup

`scripts/setup-devvm.sh` bootstraps a container for CCF development — clones
the CCF repo, configures remotes, and runs the CCF CI setup script.

```bash
/scripts/setup-devvm.sh -r github.com/microsoft/ccf -b main
```
