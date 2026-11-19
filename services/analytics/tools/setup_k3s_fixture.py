"""Generate public synthetic K3s fixture manifests only; no side effects.

Inputs are trusted local Docker network addresses obtained after identity
checks. This phase-one helper does not provision a DB, generate secrets, or
execute kubectl; the independent persistent DB and credentials require GO.
"""
import argparse
import ipaddress
import json
import re


def private_ipv4(value):
    ip = ipaddress.ip_address(value)
    if (ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or ip.is_reserved):
        raise ValueError("only a private fixture-network IPv4 is allowed")
    return str(ip)


def manifests(database_ip, authority_ip, image_tag):
    database_ip, authority_ip = private_ipv4(database_ip), private_ipv4(authority_ip)
    if database_ip == authority_ip:
        raise ValueError("database and independent authority must have distinct addresses")
    if not isinstance(image_tag, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", image_tag):
        raise ValueError("a frozen image tag is required")
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "privacy-authority", "namespace": "knowi-analytics"},
               "spec": {"ports": [{"name": "http", "port": 57420, "protocol": "TCP", "targetPort": 57420}]}}
    endpoints = {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
                 "metadata": {"name": "privacy-authority-fixture", "namespace": "knowi-analytics",
                              "labels": {"kubernetes.io/service-name": "privacy-authority",
                                         "endpointslice.kubernetes.io/managed-by": "knowi-analytics-fixture"}},
                 "addressType": "IPv4", "ports": [{"name": "http", "protocol": "TCP", "port": 57420}],
                 "endpoints": [{"addresses": [authority_ip]}]}
    values = {"image": {"repository": "knowi-analytics", "tag": image_tag, "pullPolicy": "Never"},
              "trustManifest": {"configMapName": "analytics-trust-manifest"},
              "fixtureCatalog": {"configMapName": "analytics-fixture-catalog"},
              "privacyAuthority": {"secretName": "analytics-privacy-authority"},
              "roles": {"privacy": {"enabled": True}},
              "network": {"databaseCIDR": database_ip + "/32", "databasePort": 5432,
                          "authorityCIDR": authority_ip + "/32", "authorityPort": 57420},
              "ingress": {"enabled": False}, "migration": {"enabled": False, "revision": "a006"}}
    return {"service": service, "endpointSlice": endpoints, "helmValues": values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-ip", required=True)
    parser.add_argument("--authority-ip", required=True)
    parser.add_argument("--image-tag", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(manifests(args.database_ip, args.authority_ip, args.image_tag), sort_keys=True))
    except ValueError:
        parser.exit(2, "Invalid fixture parameters; no resources changed.\n")


if __name__ == "__main__":
    main()
