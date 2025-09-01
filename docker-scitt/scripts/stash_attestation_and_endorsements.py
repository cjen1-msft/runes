import sys
import argparse
import base64
import json
import subprocess
import os

if __name__ == "__main__":
    args = argparse.ArgumentParser(
        description="Stash attestation and endorsements from a C-ACI container."
    )
    args.add_argument(
        "--bins",
        type=str,
        default="./bin",
        help="Path to the directory containing the sidecar-tools binaries.",
    )

    args = args.parse_args()

    sys.stderr.write("Finding security context folder...\n")
    security_context = None
    for folder in os.listdir("/"):
        if folder.startswith("security-context"):
            security_context = folder
            break

    sys.stderr.write("Reading in certificate chain...\n")
    with open(f"/{security_context}/host-amd-cert-base64", "r") as f:
        caci_certs = f.read()
    certs = json.loads(base64.b64decode(caci_certs).decode("utf-8"))

    sys.stderr.write("Certificate chain:\n")

    vcek_cert = certs["vcekCert"]
    vcek_cert = vcek_cert.replace("\\n", "\n")
    sys.stdout.write(vcek_cert)
    cert_chain = certs["certificateChain"]
    cert_chain = cert_chain.replace("\\n", "\n")
    sys.stdout.write(cert_chain)

    raw_attestation = subprocess.run(
        [f"{args.bins}/get-snp-report"], capture_output=True, text=True, check=True
    ).stdout

    sys.stderr.write("\nRaw attestation: \n")
    sys.stdout.write(raw_attestation)
