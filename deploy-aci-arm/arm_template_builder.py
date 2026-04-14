import json
from dataclasses import dataclass, field


NETWORK_API_VERSION = "2023-09-01"
ACI_API_VERSION = "2022-10-01-preview"
PUBLIC_IP_TYPE = "Microsoft.Network/publicIPAddresses"
NAT_GATEWAY_TYPE = "Microsoft.Network/natGateways"
VIRTUAL_NETWORK_TYPE = "Microsoft.Network/virtualNetworks"
VIRTUAL_NETWORK_SUBNET_TYPE = "Microsoft.Network/virtualNetworks/subnets"
LOAD_BALANCER_TYPE = "Microsoft.Network/loadBalancers"
NETWORK_SECURITY_GROUP_TYPE = "Microsoft.Network/networkSecurityGroups"
NETWORK_INTERFACE_TYPE = "Microsoft.Network/networkInterfaces"
CONTAINER_GROUP_TYPE = "Microsoft.ContainerInstance/containerGroups"
ACI_SKU_CONFIDENTIAL = "Confidential"
ACI_SKU_STANDARD = "Standard"
CCE_POLICY = "cGFja2FnZSBwb2xpY3kKCmFwaV9zdm4gOj0gIjAuMTAuMCIKZnJhbWV3b3JrX3N2biA6PSAiMC4xLjAiCgptb3VudF9kZXZpY2UgOj0geyJhbGxvd2VkIjogdHJ1ZX0KbW91bnRfb3ZlcmxheSA6PSB7ImFsbG93ZWQiOiB0cnVlfQpjcmVhdGVfY29udGFpbmVyIDo9IHsiYWxsb3dlZCI6IHRydWUsICJhbGxvd19zdGRpb19hY2Nlc3MiOiB0cnVlfQp1bm1vdW50X2RldmljZSA6PSB7ImFsbG93ZWQiOiB0cnVlfQp1bm1vdW50X292ZXJsYXkgOj0geyJhbGxvd2VkIjogdHJ1ZX0KZXhlY19pbl9jb250YWluZXIgOj0geyJhbGxvd2VkIjogdHJ1ZX0KZXhlY19leHRlcm5hbCA6PSB7ImFsbG93ZWQiOiB0cnVlLCAiYWxsb3dfc3RkaW9fYWNjZXNzIjogdHJ1ZX0Kc2h1dGRvd25fY29udGFpbmVyIDo9IHsiYWxsb3dlZCI6IHRydWV9CnNpZ25hbF9jb250YWluZXJfcHJvY2VzcyA6PSB7ImFsbG93ZWQiOiB0cnVlfQpwbGFuOV9tb3VudCA6PSB7ImFsbG93ZWQiOiB0cnVlfQpwbGFuOV91bm1vdW50IDo9IHsiYWxsb3dlZCI6IHRydWV9CmdldF9wcm9wZXJ0aWVzIDo9IHsiYWxsb3dlZCI6IHRydWV9CmR1bXBfc3RhY2tzIDo9IHsiYWxsb3dlZCI6IHRydWV9CnJ1bnRpbWVfbG9nZ2luZyA6PSB7ImFsbG93ZWQiOiB0cnVlfQpsb2FkX2ZyYWdtZW50IDo9IHsiYWxsb3dlZCI6IHRydWV9CnNjcmF0Y2hfbW91bnQgOj0geyJhbGxvd2VkIjogdHJ1ZX0Kc2NyYXRjaF91bm1vdW50IDo9IHsiYWxsb3dlZCI6IHRydWV9Cg=="


def resource_id(resource_type: str, *names: str) -> str:
    quoted_names = ", ".join(f"'{name}'" for name in names)
    return f"[resourceId('{resource_type}', {quoted_names})]"


def subnet_resource_id(vnet_name: str, subnet_name: str) -> str:
    return resource_id(VIRTUAL_NETWORK_SUBNET_TYPE, vnet_name, subnet_name)


def aci_sku_name(sku: str) -> str:
    normalized = sku.lower()
    if normalized == "standard":
        return ACI_SKU_STANDARD
    if normalized == "confidential":
        return ACI_SKU_CONFIDENTIAL
    raise ValueError(f"Unsupported ACI SKU: {sku}")


def confidential_compute_properties_for_sku(sku: str) -> dict:
    if aci_sku_name(sku) != ACI_SKU_CONFIDENTIAL:
        return {}
    return {"confidentialComputeProperties": {"ccePolicy": CCE_POLICY}}


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
            "type": NETWORK_SECURITY_GROUP_TYPE,
            "apiVersion": NETWORK_API_VERSION,
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
        return resource_id(NETWORK_SECURITY_GROUP_TYPE, self.name)


@dataclass
class ResourcePublicIP:
    name: str
    region: str
    allocation_method: str = "Static"
    sku: str = "Standard"

    def to_dict(self):
        return {
            "type": PUBLIC_IP_TYPE,
            "apiVersion": NETWORK_API_VERSION,
            "name": self.name,
            "location": self.region,
            "sku": {"name": self.sku},
            "properties": {
                "publicIPAddressVersion": "IPv4",
                "publicIPAllocationMethod": self.allocation_method,
                "idleTimeoutInMinutes": 4,
                "ipTags": [{"ipTagType": "FirstPartyUsage", "tag": "/NonProd"}],
            },
        }

    def get_name(self):
        return resource_id(PUBLIC_IP_TYPE, self.name)


@dataclass
class ResourceLoadBalancer:
    name: str
    region: str
    public_ip: ResourcePublicIP
    vnet_name: str
    subnet_name: str
    depends_on_vnet: bool = True

    def to_dict(self):
        frontend_name = "LoadBalancerFrontEnd"
        backend_pool_name = "BackendPool"
        probe_name = "ssh-health-probe"
        rule_name = "ssh-rule"
        return {
            "type": LOAD_BALANCER_TYPE,
            "apiVersion": NETWORK_API_VERSION,
            "name": self.name,
            "location": self.region,
            "sku": {"name": "Standard"},
            "dependsOn": [self.public_ip.get_name()]
            + (
                [resource_id(VIRTUAL_NETWORK_TYPE, self.vnet_name)]
                if self.depends_on_vnet
                else []
            ),
            "properties": {
                "frontendIPConfigurations": [
                    {
                        "name": frontend_name,
                        "properties": {
                            "publicIPAddress": {"id": self.public_ip.get_name()}
                        },
                    }
                ],
                "backendAddressPools": [
                    {
                        "name": backend_pool_name,
                        "properties": {},
                    }
                ],
                "probes": [
                    {
                        "name": probe_name,
                        "properties": {
                            "port": 22,
                            "protocol": "Tcp",
                            "intervalInSeconds": 5,
                            "numberOfProbes": 2,
                        },
                    }
                ],
                "loadBalancingRules": [
                    {
                        "name": rule_name,
                        "properties": {
                            "frontendIPConfiguration": {
                                "id": f"[concat(resourceId('{LOAD_BALANCER_TYPE}', '{self.name}'), '/frontendIPConfigurations/{frontend_name}')]"
                            },
                            "backendAddressPool": {
                                "id": f"[concat(resourceId('{LOAD_BALANCER_TYPE}', '{self.name}'), '/backendAddressPools/{backend_pool_name}')]"
                            },
                            "probe": {
                                "id": f"[concat(resourceId('{LOAD_BALANCER_TYPE}', '{self.name}'), '/probes/{probe_name}')]"
                            },
                            "protocol": "Tcp",
                            "frontendPort": 22,
                            "backendPort": 22,
                            "enableFloatingIP": False,
                            "idleTimeoutInMinutes": 4,
                        },
                    }
                ],
            },
        }


@dataclass
class ResourceNAT:
    name: str
    region: str
    address_space: str
    public_ip: ResourcePublicIP

    def to_dict(self):
        return {
            "type": NAT_GATEWAY_TYPE,
            "apiVersion": NETWORK_API_VERSION,
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
        return resource_id(NAT_GATEWAY_TYPE, self.name)


@dataclass
class VNetSubnet:
    name: str
    address_prefix: str
    delegations: list[tuple[str, str]] | None = None
    nat_gateway: ResourceNAT | None = None
    allow_outbound: bool = False

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
                "defaultoutboundaccess": self.allow_outbound,
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
    existing: bool = False
    emit_dependency: bool = True

    def to_dict(self):
        subnets = self.subnets or []
        depends_on = [
            s.nat_gateway.get_name() for s in subnets if s.nat_gateway is not None
        ]
        return {
            "type": VIRTUAL_NETWORK_TYPE,
            "apiVersion": NETWORK_API_VERSION,
            "name": self.name,
            "location": self.region,
            "properties": {
                "addressSpace": {"addressPrefixes": [self.address_space]},
                "subnets": [s.to_dict() for s in subnets],
            },
        } | ({"dependsOn": depends_on} if len(depends_on) > 0 else {})


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
                    "id": subnet_resource_id(self.subnet.name, self.subnet.name)
                },
                "privateIPAllocationMethod": self.private_ip_allocation_method,
            },
        }
        if self.private_ip_allocation_method == "Static" and self.private_ip_address:
            ip_config["properties"]["privateIPAddress"] = self.private_ip_address

        return {
            "type": NETWORK_INTERFACE_TYPE,
            "apiVersion": NETWORK_API_VERSION,
            "name": self.name,
            "location": self.region,
            "properties": {
                "ipConfigurations": [ip_config],
            }
            | nsg_,
        }


@dataclass
class CACI:
    name: str
    image: str
    cpu: int = 8
    ram: int = 16
    privileged: bool = True
    # Use default_factory to avoid sharing a mutable list between instances
    ports: list[dict] = field(
        default_factory=list
    )  # list of {"protocol": "TCP", "port": 22} dicts

    def to_dict(self, ssh_key=None, volume_mounts=None):
        cmd_prefix = "echo Fabric_NodeIPOrFQDN=$Fabric_NodeIPOrFQDN >> /aci_env && echo UVM_SECURITY_CONTEXT_DIR=$UVM_SECURITY_CONTEXT_DIR >> /aci_env && mkdir -p /root/.ssh/ && gpg --import /etc/pki/rpm-gpg/MICROSOFT-RPM-GPG-KEY && tdnf update -y && tdnf install -y openssh-server ca-certificates"
        if ssh_key is None:
            cmd = ["/bin/sh", "-c", f"{cmd_prefix} && tail -f /dev/null"]
            env = []
        else:
            cmd = [
                "/bin/sh",
                "-c",
                f"{cmd_prefix} && echo $SSH_ADMIN_KEY >> /root/.ssh/authorized_keys && ssh-keygen -A && sed -i 's/PermitRootLogin no/PermitRootLogin yes/' /etc/ssh/sshd_config && sed -i 's/# PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config && /usr/sbin/sshd -D",
            ]
            env = [{"name": "SSH_ADMIN_KEY", "value": ssh_key}]

        ports = list(self.ports)  # make a copy
        ports_contains_22 = any(p.get("port") == 22 for p in self.ports)
        if ssh_key and not ports_contains_22:
            ports += [{"protocol": "TCP", "port": "22"}]
        return {
            "name": self.name,
            "properties": {
                "image": self.image,
                "command": cmd,
                "ports": ports,
                "environmentVariables": env,
                "volumeMounts": volume_mounts or [],
                "resources": {
                    "requests": {
                        "cpu": self.cpu,
                        "memoryInGB": self.ram,
                    }
                },
                "securityContext": {
                    "privileged": self.privileged,
                }
            },
        }


@dataclass(frozen=True)
class AzureFileMount:
    storage_account_name: str
    share_name: str
    volume_name: str
    mount_path: str
    storage_account_key: str
    read_only: bool = False

    def volume_dict(self):
        return {
            "name": self.volume_name,
            "azureFile": {
                "shareName": self.share_name,
                "storageAccountName": self.storage_account_name,
                "storageAccountKey": self.storage_account_key,
            },
        }

    def volume_mount_dict(self):
        return {
            "name": self.volume_name,
            "mountPath": self.mount_path,
            "readOnly": self.read_only,
        }


@dataclass
class ResourceACIGroup:
    name: str
    region: str
    sshkey: str | None = None
    containers: list[CACI] = field(default_factory=list)
    acr_creds: dict | None = None
    ports: list[dict] = field(default_factory=list)
    sku: str = ACI_SKU_CONFIDENTIAL
    vnet: ResourceVNet | None = None
    private_ip_address: str | None = None
    azure_file_mount: AzureFileMount | None = None

    def to_dict(self):
        depends_on = []
        ip_address = {
            "ports": self.ports,
            "type": "Public" if not self.vnet else "Private",
        }
        if self.private_ip_address:
            ip_address["ip"] = self.private_ip_address
        properties = {
            "sku": aci_sku_name(self.sku),
            "restartPolicy": "Never",
            "osType": "Linux",
            "ipAddress": ip_address,
            "volumes": (
                [self.azure_file_mount.volume_dict()]
                if self.azure_file_mount is not None
                else []
            ),
            "containers": [
                container.to_dict(
                    self.sshkey,
                    volume_mounts=(
                        [self.azure_file_mount.volume_mount_dict()]
                        if self.azure_file_mount is not None
                        else []
                    ),
                )
                for container in self.containers
            ],
        }
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
        if not any(port["port"] == 22 for port in self.ports):
            ports += [{"protocol": "TCP", "port": "22"}]

        if self.vnet:
            vnet_subnets = self.vnet.subnets or []
            subnet = {
                "subnetIds": [
                    {
                        "id": subnet_resource_id(self.vnet.name, vnet_subnets[0].name)
                    }
                ]
            }
            if self.vnet.emit_dependency:
                depends_on.append(resource_id(VIRTUAL_NETWORK_TYPE, self.vnet.name))
        else:
            subnet = {}

        properties |= confidential_compute_properties_for_sku(self.sku)

        return {
            "type": CONTAINER_GROUP_TYPE,
            "apiVersion": ACI_API_VERSION,
            "name": self.name,
            "location": self.region,
            "identity": {"type": "SystemAssigned"},
            "properties": properties
            | image_crds
            | subnet,
        } | ({"dependsOn": depends_on} if len(depends_on) > 0 else {})


class ARMTemplate:
    def __init__(self, resources, parameters=None):
        self.resources = resources
        self.parameters = parameters or {}

    def to_dict(self):
        return {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "parameters": self.parameters,
            "variables": {},
            "resources": [resource.to_dict() for resource in self.resources],
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=4)
