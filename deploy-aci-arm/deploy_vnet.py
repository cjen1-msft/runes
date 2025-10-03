#!/usr/bin/env python3
"""Deploy an Azure Virtual Network (VNet) via an ARM template.

Executable usage:
	python deploy_vnet.py --resource-group <rg> [--name myvnet] \
		[--region northeurope] [--address-space 10.1.0.0/16] \
		[--subnet default:10.1.0.0/24 --subnet workload:10.1.1.0/24]

Library usage:
	from deploy_vnet import deploy_vnet
	vnet_name = deploy_vnet(resource_group="my-rg")

The deploy_vnet function returns the VNet name so callers can chain operations.
"""

from __future__ import annotations

import argparse
import os
import random
import string
import tempfile
from subprocess import run, PIPE
import shlex
from typing import List, Dict

import arm_template_builder as tb


def _random_suffix(n: int = 5) -> str:
	return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def default_vnet_name() -> str:
	return f"vnet-{_random_suffix()}"


def parse_subnet_args(values: List[str]) -> List[Dict[str, str]]:
	"""Parse --subnet arguments of the form name:CIDR.

	Example: ["default:10.0.0.0/24", "workload:10.0.1.0/24"]
	Returns list of {"name": name, "addressPrefix": cidr}
	"""
	subnets = []
	for v in values:
		if ':' not in v:
			raise ValueError(f"Invalid subnet specification '{v}'. Use name:CIDR")
		name, cidr = v.split(':', 1)
		subnets.append({"name": name, "addressPrefix": cidr})
	return subnets


def _default_subnet_from_space(address_space: str) -> List[Dict[str, str]]:
	# Naive derivation: if /16 -> replace with /24 for first subnet. Otherwise reuse.
	if address_space.endswith('/16'):
		base = address_space.rsplit('.', 2)[0]  # crude but acceptable: '10.0'
		subnet_cidr = address_space.split('/')[0].rsplit('.', 1)[0] + '.0.0/24'
		# Actually above might mis-handle; simpler: just replace trailing '/16' with '/24'
		subnet_cidr = address_space.replace('/16', '/24')
	else:
		subnet_cidr = address_space
	return [{"name": "default", "addressPrefix": subnet_cidr}]


def deploy_vnet(
	resource_group: str,
	name: str | None = None,
	region: str = "northeurope",
	address_space: str = "10.0.0.0/16",
	subnets: List[Dict[str, str]] | None = None,
	azure_auth: bool = False,
) -> str:
	"""Deploy a VNet and return its name.

	Parameters mirror CLI flags. If subnets is None a single default subnet is created.
	"""
	vnet_name = name or default_vnet_name()
	if subnets is None or len(subnets) == 0:
		subnets = _default_subnet_from_space(address_space)

	# Optional auth step (simple placeholder - avoids adding magic if already logged in)
	if azure_auth:
		run(["az", "account", "show"], check=False, stdout=PIPE, stderr=PIPE)

	template = tb.ARMTemplate([
		tb.ResourceVNet(
			name=vnet_name,
			region=region,
			address_space=address_space,
			subnets=subnets,
		)
	])

	with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
		tf.write(template.to_json())
		tf.flush()
		template_file = tf.name

    az_cmd = [
      "az",
      "deployment",
      "group",
      "create",
      "--resource-group",
      resource_group,
      "--template-file",
      tf.name,
    ]
	print("Running:")
	print(shlex.join(az_cmd))
	run(az_cmd, check=True)

	# No direct output required; return the name for programmatic usage.
	return vnet_name


def main():
	parser = argparse.ArgumentParser(description="Deploy an Azure VNet via ARM template.")
	parser.add_argument("--resource-group", required=True, help="Resource group for the deployment")
	parser.add_argument("--name", help="Name of the VNet (auto-generated if omitted)")
	parser.add_argument("--region", default="northeurope", help="Azure region")
	parser.add_argument("--address-space", default="10.0.0.0/16", help="VNet address space CIDR")
	parser.add_argument(
		"--subnet",
		action="append",
		dest="subnets",
		help="Subnet specification name:CIDR (can be repeated)",
	)
	parser.add_argument(
		"--azure-auth",
		action="store_true",
		help="Attempt an az account show to ensure CLI context (no-op if already logged in)",
	)

	args = parser.parse_args()

	if args.subnets:
		subnets = parse_subnet_args(args.subnets)
	else:
		subnets = None

	vnet_name = deploy_vnet(
		resource_group=args.resource_group,
		name=args.name,
		region=args.region,
		address_space=args.address_space,
		subnets=subnets,
		azure_auth=args.azure_auth,
	)
	print(f"VNET_NAME={vnet_name}")


if __name__ == "__main__":
	main()
