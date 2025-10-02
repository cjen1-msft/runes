#!/usr/bin/env python3
"""Deploy an Azure Virtual Network via an ARM template.

Executable usage:
	python deploy_vnet.py \
		--resource-group my-rg \
		--location northeurope \
		--vnet-name myVnet \
		--subnet-name aci-subnet \
		--address-prefix 10.42.0.0/16 \
		--subnet-prefix 10.42.1.0/24

Library usage:
	from deploy_vnet import deploy_vnet
	vnet_info = deploy_vnet(
		resource_group="my-rg",
		location="northeurope",
		vnet_name="myVnet",
		subnet_name="aci-subnet",
		address_prefix="10.42.0.0/16",
		subnet_prefix="10.42.1.0/24",
	)
	print(vnet_info["subnet_id"])  # Use for ACI subnetIds

Returns a dict: { 'vnet_name', 'subnet_name', 'subscription_id', 'subnet_id' }.

Assumptions:
  - Azure CLI (az) is installed and logged-in.
  - Caller has rights to deploy at resource group scope.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict

from arm_template_builder import VNetDeployment, SubnetSpec


def _run(cmd: list[str]) -> str:
    """Run a CLI command and return stdout, raising on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
        )
    return proc.stdout.strip()


def build_vnet_template(
    vnet_name: str,
    location: str,
    address_prefix: str,
    subnet_name: str,
    subnet_prefix: str,
):
    vnet = VNetDeployment(
        name=vnet_name,
        location=location,
        address_space=[address_prefix],
        subnets=[SubnetSpec(subnet_name, subnet_prefix)],
    )
    return vnet.to_dict()


def deploy_vnet(
    resource_group: str,
    location: str,
    vnet_name: str,
    subnet_name: str,
    address_prefix: str = "10.10.0.0/16",
    subnet_prefix: str = "10.10.1.0/24",
    quiet: bool = False,
) -> Dict[str, str]:
    """Deploy the VNet and return identifiers.

    Uses an ephemeral ARM template file and az deployment group create.
    """
    template = build_vnet_template(
        vnet_name=vnet_name,
        location=location,
        address_prefix=address_prefix,
        subnet_name=subnet_name,
        subnet_prefix=subnet_prefix,
    )
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td) / "vnet-template.json"
        tmp_path.write_text(json.dumps(template, indent=2))
        if not quiet:
            print(
                f"Deploying VNet '{vnet_name}' to resource group '{resource_group}'..."
            )
        cmd = [
            "az",
            "deployment",
            "group",
            "create",
            "--resource-group",
            resource_group,
            "--template-file",
            str(tmp_path),
            "--parameters",
            f"location={location}",  # location is embedded but harmless to pass
            "-o",
            "none",
        ]
        # We ignore output by specifying -o none for cleaner display.
        _run(cmd)

    subscription_id = _run(["az", "account", "show", "--query", "id", "-o", "tsv"])
    subnet_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
        f"providers/Microsoft.Network/virtualNetworks/{vnet_name}/subnets/{subnet_name}"
    )
    result = {
        "vnet_name": vnet_name,
        "subnet_name": subnet_name,
        "subscription_id": subscription_id,
        "subnet_id": subnet_id,
    }
    if not quiet:
        print(json.dumps(result, indent=2))
    return result


def parse_args(argv):
    ap = argparse.ArgumentParser(description="Deploy an Azure VNet (ARM template)")
    ap.add_argument("--resource-group", "-g", required=True)
    ap.add_argument("--location", "-l", required=True, help="Azure region")
    ap.add_argument("--vnet-name", required=True)
    ap.add_argument("--subnet-name", required=True)
    ap.add_argument(
        "--address-prefix",
        default="10.10.0.0/16",
        help="CIDR for VNet address space (default: 10.10.0.0/16)",
    )
    ap.add_argument(
        "--subnet-prefix",
        default="10.10.1.0/24",
        help="CIDR for subnet (default: 10.10.1.0/24)",
    )
    ap.add_argument(
        "--quiet", action="store_true", help="Suppress progress / pretty output"
    )
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    deploy_vnet(
        resource_group=args.resource_group,
        location=args.location,
        vnet_name=args.vnet_name,
        subnet_name=args.subnet_name,
        address_prefix=args.address_prefix,
        subnet_prefix=args.subnet_prefix,
        quiet=args.quiet,
    )


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 - show a concise error
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
