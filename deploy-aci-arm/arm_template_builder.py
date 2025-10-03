import json
from dataclasses import dataclass


class CACI:
    def __init__(self, name, image, cpu, ram):
        self.name = name
        self.image = image
        self.cpu = cpu
        self.ram = ram

    def to_dict(self, ssh_key=None):
        if ssh_key is None:
            cmd = ["tail", "-f", "/dev/null"]
            env = []
        else:
            cmd = [
                "/bin/sh",
                "-c",
                "echo Fabric_NodeIPOrFQDN=$Fabric_NodeIPOrFQDN >> /aci_env && echo UVM_SECURITY_CONTEXT_DIR=$UVM_SECURITY_CONTEXT_DIR >> /aci_env && mkdir -p /root/.ssh/ && gpg --import /etc/pki/rpm-gpg/MICROSOFT-RPM-GPG-KEY && tdnf update -y && tdnf install -y openssh-server ca-certificates && echo $SSH_ADMIN_KEY >> /root/.ssh/authorized_keys && ssh-keygen -A && sed -i 's/PermitRootLogin no/PermitRootLogin yes/' /etc/ssh/sshd_config && sed -i 's/# PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config && /usr/sbin/sshd -D",
            ]
            env = [{"name": "SSH_ADMIN_KEY", "value": ssh_key}]

        return {
            "name": self.name,
            "properties": {
                "image": self.image,
                "command": cmd,
                "ports": [{"protocol": "TCP", "port": "22"}] if ssh_key else [],
                "environmentVariables": env,
                "volumeMounts": [],
                "resources": {
                    "requests": {
                        "cpu": self.cpu,
                        "memoryInGB": self.ram,
                    }
                },
            },
        }


@dataclass
class NSGRule:
    name: str
    priority: int
    direction: str  # "Inbound" or "Outbound"
    access: str  # "Allow" or "Deny"
    protocol: str  # "Tcp", "Udp", "*"
    source_port_range: str  # "*", "80", "100-200"
    destination_port_range: str  # "*", "80", "100-200"
    source_address_prefix: str  # "*", "Internet", "VirtualNetwork"
    destination_address_prefix: str  # "*", "Internet", "VirtualNetwork"

    def to_dict(self):
        return {
            "name": self.name,
            "properties": {
                "priority": self.priority,
                "direction": self.direction,
                "access": self.access,
                "protocol": self.protocol,
                "sourcePortRange": self.source_port_range,
                "destinationPortRange": self.destination_port_range,
                "sourceAddressPrefix": self.source_address_prefix,
                "destinationAddressPrefix": self.destination_address_prefix,
            },
        }


@dataclass
class ResourceNSG:
    name: str
    region: str
    security_rules: list[NSGRule] | None = None

    def to_dict(self):
        d = {
            "type": "Microsoft.Network/networkSecurityGroups",
            "apiVersion": "2023-09-01",
            "name": self.name,
            "location": self.region,
            "properties": {},
        }
        if self.security_rules:
            d["properties"]["securityRules"] = [
                rule.to_dict() for rule in self.security_rules
            ]
        return d

    def get_name(self):
        return f"[resourceId('Microsoft.Network/networkSecurityGroups', '{self.name}')]"


@dataclass
class ResourcePublicIP:
    name: str
    region: str
    allocation_method: str = "Static"
    sku: str = "Standard"

    def to_dict(self):
        return {
            "type": "Microsoft.Network/publicIPAddresses",
            "apiVersion": "2023-09-01",
            "name": self.name,
            "location": self.region,
            "sku": {"name": self.sku},
            "properties": {
                "publicIPAddressVersion": "IPv4",
                "publicIPAllocationMethod": self.allocation_method,
                "idleTimeoutInMinutes": 4,
            },
        }

    def get_name(self):
        return f"[resourceId('Microsoft.Network/publicIPAddresses', '{self.name}')]"


@dataclass
class ResourceNAT:
    name: str
    region: str
    address_space: str
    public_ip: ResourcePublicIP

    def to_dict(self):
        return {
            "type": "Microsoft.Network/natGateways",
            "apiVersion": "2023-09-01",
            "name": self.name,
            "location": self.region,
            "sku": {"name": "Standard"},
            "properties": {
                "publicIpAddresses": [{"id": self.public_ip.get_name()}],
                "idleTimeoutInMinutes": 4,
            },
            "dependsOn": [self.public_ip.get_name()],
        }

    def get_name(self):
        return f"[resourceId('Microsoft.Network/natGateways', '{self.name}')]"


@dataclass
class VNetSubnet:
    name: str
    address_prefix: str
    delegations: list[tuple[str, str]] | None = None
    nat_gateway: ResourceNAT | None = None

    def to_dict(self):
        if self.nat_gateway:
            nat_gateway_ = {
                "natGateway": {"id": self.nat_gateway.get_name()}
                # "privateEndpointNetworkPolicies": "Enabled",
                # "privateLinkServiceNetworkPolicies": "Enabled",
            }
        else:
            nat_gateway_ = {}
        d = {
            "name": self.name,
            "properties": {
                "addressPrefix": self.address_prefix,
            }
            | nat_gateway_,
        }
        if self.delegations:
            d["properties"]["delegations"] = self.delegations
        return d


@dataclass
class ResourceVNet:
    name: str
    region: str
    address_space: str
    subnets: list[VNetSubnet] | None = None

    def to_dict(self):
        depends_on = [
            s.nat_gateway.get_name() for s in self.subnets if s.nat_gateway is not None
        ]
        return (
            {
                "type": "Microsoft.Network/virtualNetworks",
                "apiVersion": "2023-09-01",
                "name": self.name,
                "location": self.region,
                "properties": {
                    "addressSpace": {"addressPrefixes": [self.address_space]},
                    "subnets": [s.to_dict() for s in self.subnets],
                },
            }
            | {"dependsOn": depends_on}
            if len(depends_on) > 0
            else {}
        )


@dataclass
class ResourceNetworkInterface:
    name: str
    region: str
    subnet: VNetSubnet
    nsg: ResourceNSG | None = None
    private_ip_allocation_method: str = "Dynamic"  # or "Static"
    private_ip_address: str | None = None  # required if Static

    def to_dict(self):
        if self.nsg:
            nsg_ = {"networkSecurityGroup": {"id": self.nsg.get_name()}}
        else:
            nsg_ = {}

        ip_config = {
            "name": f"{self.name}-ipconfig",
            "properties": {
                "subnet": {
                    "id": f"[resourceId('Microsoft.Network/virtualNetworks/subnets', '{self.subnet.name}', '{self.subnet.name}')]"
                },
                "privateIPAllocationMethod": self.private_ip_allocation_method,
            },
        }
        if self.private_ip_allocation_method == "Static" and self.private_ip_address:
            ip_config["properties"]["privateIPAddress"] = self.private_ip_address

        return {
            "type": "Microsoft.Network/networkInterfaces",
            "apiVersion": "2023-09-01",
            "name": self.name,
            "location": self.region,
            "properties": {
                "ipConfigurations": [ip_config],
            }
            | nsg_,
        }


class ResourceACIGroup:
    def __init__(
        self,
        name,
        region,
        sshkey=None,
        containers=None,
        acr_creds=None,
        ports=None,
        sku="Confidential",
        vnet=None,
    ):
        self.name = name
        self.region = region
        self.sshkey = sshkey
        self.containers = containers
        self.acr_creds = acr_creds
        if ports is not None:
            self.ports = ports
        else:
            self.ports = []
        self.sku = sku
        self.vnet = vnet

    def to_dict(self):
        depends_on = []
        if self.acr_creds:
            image_crds = {
                "imageRegistryCredentials": [
                    {
                        "server": self.acr_creds["server"],
                        "username": self.acr_creds["username"],
                        "password": self.acr_creds["password"],
                    }
                ]
            }
        else:
            image_crds = {}

        ports = self.ports
        if self.sshkey:
            ports += [{"protocol": "TCP", "port": "22"}]

        if self.vnet:
            subnet = {
                "subnetIds": [
                    {
                        "id": f"[resourceId('Microsoft.Network/virtualNetworks/subnets', '{self.vnet.name}', '{self.vnet.subnets[0].name}')]"
                    }
                ]
            }
            depends_on.append(
                f"[resourceId('Microsoft.Network/virtualNetworks', '{self.vnet.name}')]"
            )
        else:
            subnet = {}

        return (
            {
                "type": "Microsoft.ContainerInstance/containerGroups",
                "apiVersion": "2022-10-01-preview",
                "name": self.name,
                "location": self.region,
                "identity": {"type": "SystemAssigned"},
                "properties": {
                    "sku": self.sku,
                    "restartPolicy": "Never",
                    "osType": "Linux",
                    "ipAddress": {
                        "ports": self.ports,
                        "type": "Public" if not self.vnet else "Private",
                    },
                    "volumes": [],
                    "confidentialComputeProperties": {
                        "ccePolicy": "cGFja2FnZSBwb2xpY3kKCmFwaV9zdm4gOj0gIjAuMTAuMCIKZnJhbWV3b3JrX3N2biA6PSAiMC4xLjAiCgptb3VudF9kZXZpY2UgOj0geyJhbGxvd2VkIjogdHJ1ZX0KbW91bnRfb3ZlcmxheSA6PSB7ImFsbG93ZWQiOiB0cnVlfQpjcmVhdGVfY29udGFpbmVyIDo9IHsiYWxsb3dlZCI6IHRydWUsICJhbGxvd19zdGRpb19hY2Nlc3MiOiB0cnVlfQp1bm1vdW50X2RldmljZSA6PSB7ImFsbG93ZWQiOiB0cnVlfQp1bm1vdW50X292ZXJsYXkgOj0geyJhbGxvd2VkIjogdHJ1ZX0KZXhlY19pbl9jb250YWluZXIgOj0geyJhbGxvd2VkIjogdHJ1ZX0KZXhlY19leHRlcm5hbCA6PSB7ImFsbG93ZWQiOiB0cnVlLCAiYWxsb3dfc3RkaW9fYWNjZXNzIjogdHJ1ZX0Kc2h1dGRvd25fY29udGFpbmVyIDo9IHsiYWxsb3dlZCI6IHRydWV9CnNpZ25hbF9jb250YWluZXJfcHJvY2VzcyA6PSB7ImFsbG93ZWQiOiB0cnVlfQpwbGFuOV9tb3VudCA6PSB7ImFsbG93ZWQiOiB0cnVlfQpwbGFuOV91bm1vdW50IDo9IHsiYWxsb3dlZCI6IHRydWV9CmdldF9wcm9wZXJ0aWVzIDo9IHsiYWxsb3dlZCI6IHRydWV9CmR1bXBfc3RhY2tzIDo9IHsiYWxsb3dlZCI6IHRydWV9CnJ1bnRpbWVfbG9nZ2luZyA6PSB7ImFsbG93ZWQiOiB0cnVlfQpsb2FkX2ZyYWdtZW50IDo9IHsiYWxsb3dlZCI6IHRydWV9CnNjcmF0Y2hfbW91bnQgOj0geyJhbGxvd2VkIjogdHJ1ZX0Kc2NyYXRjaF91bm1vdW50IDo9IHsiYWxsb3dlZCI6IHRydWV9Cg=="
                    },
                    "containers": [
                        container.to_dict(self.sshkey) for container in self.containers
                    ],
                }
                | image_crds
                | subnet,
            }
            | {"dependsOn": depends_on}
            if len(depends_on) > 0
            else {}
        )


class ARMTemplate:
    def __init__(self, resources):
        self.resources = resources

    def to_dict(self):
        return {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {},
            "variables": {},
            "resources": [resource.to_dict() for resource in self.resources],
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=4)
