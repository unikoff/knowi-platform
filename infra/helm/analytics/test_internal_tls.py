"""Synthetic container test; requires Docker and cryptography. No cluster access."""
from datetime import datetime, timedelta, timezone
import http.client
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import textwrap
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from test_render import PRODUCTION, DIGEST, UUID, render


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr}")
    return result.stdout.strip()


def certificate(common_name, key, signer, signer_key, *, ca=False, usage=None):
    now = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (x509.CertificateBuilder().subject_name(subject)
            .issuer_name(signer.subject if signer else subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if usage:
        cert = cert.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    if not ca:
        cert = cert.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    return cert.sign(signer_key or key, hashes.SHA256())


def write_pair(path, name, cert, key):
    (path / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (path / f"{name}.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def main():
    role = os.environ.get("ANALYTICS_TLS_TEST_ROLE", "collector")
    assert role in {"collector", "query", "core-source"}
    values = dict(PRODUCTION)
    if role == "core-source":
        values.pop("coreDeletion.podLabels.app")
        values.update({"privateCoreSource.enabled": "true", "privateCoreSource.imageRepository": "example/core",
                       "privateCoreSource.imageDigest": DIGEST, "privateCoreSource.databaseSecretName": "source-db",
                       "privateCoreSource.hmacSecretName": "source-hmac", "privateCoreSource.databaseCASecretName": "source-ca",
                       "privateCoreSource.serverTLSSecretName": "source-tls", "privateCoreSource.expectedHost": "db.local",
                       "privateCoreSource.expectedDatabase": "core", "privateCoreSource.expectedUser": "readonly",
                       "privateCoreSource.instanceId": UUID, "privateCoreSource.databaseCIDR": "10.42.5.9/32",
                       "coreDeletion.namespace": "knowi-analytics", "coreDeletion.port": "8443",
                       "coreDeletion.podLabels.app\\.kubernetes\\.io/name": "analytics-core-source",
                       "coreDeletion.podLabels.app\\.kubernetes\\.io/instance": "analytics"})
    result = render(values)
    assert result.returncode == 0, result.stderr
    block = result.stdout.split(f"  {role}.conf: |\n", 1)[1].split("\n---", 1)[0]
    for next_role in ("query", "core-source"):
        block = block.split(f"  {next_role}.conf: |\n", 1)[0]
    config = textwrap.dedent(block)
    name = "analytics-tls-test-" + uuid.uuid4().hex[:12]
    upstream = name + "-upstream"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        ca_key, server_key, client_key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3))
        ca = certificate("test-ca", ca_key, None, None, ca=True)
        server = certificate("localhost", server_key, ca, ca_key, usage=ExtendedKeyUsageOID.SERVER_AUTH)
        client = certificate("test-client", client_key, ca, ca_key, usage=ExtendedKeyUsageOID.CLIENT_AUTH)
        other_ca_key, other_key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2))
        other_ca = certificate("other-ca", other_ca_key, None, None, ca=True)
        other_client = certificate("other-client", other_key, other_ca, other_ca_key, usage=ExtendedKeyUsageOID.CLIENT_AUTH)
        write_pair(path, "tls", server, server_key)
        write_pair(path, "client", client, client_key)
        write_pair(path, "other", other_client, other_key)
        (path / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (path / "nginx.conf").write_text(config, encoding="utf-8")
        try:
            docker("run", "-d", "--name", name, "--network", "bridge", "--read-only", "--user", "10001:10001", "--tmpfs", "/tmp:rw,size=16m", "-p", "127.0.0.1::8443", "-v", f"{path}:/var/run/server-tls:ro", "--entrypoint", "nginx", "pymentor-web-nginx-check:local", "-c", "/var/run/server-tls/nginx.conf", "-g", "daemon off;")
            try:
                docker("exec", name, "nginx", "-t", "-c", "/var/run/server-tls/nginx.conf")
            except RuntimeError:
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                raise RuntimeError(logs.stderr) from None
            code = "from http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b'upstream')\nHTTPServer(('127.0.0.1',8000),H).serve_forever()"
            docker("run", "-d", "--name", upstream, "--network", f"container:{name}", "--entrypoint", "python", "python:3.12.16-slim-trixie", "-c", code)
            port = int(docker("port", name, "8443/tcp").rsplit(":", 1)[1])
            time.sleep(1)
            context = ssl.create_default_context(cafile=str(path / "ca.crt"))
            context.load_cert_chain(str(path / "client.crt"), str(path / "client.key"))
            def request(method, target, ctx):
                conn = http.client.HTTPSConnection("localhost", port, context=ctx, timeout=5)
                conn.request(method, target)
                response = conn.getresponse(); data = response.read(); conn.close()
                return response.status, data
            target = {"collector": "/internal/core-transfer/v1/receipts/abc", "query": "/api/analytics/reports/activity", "core-source": "/api/internal/analytics/deletions"}[role]
            assert request("GET", target, context) == (200, b"upstream")
            assert request("POST", target, context)[0] == 405
            assert request("GET", "/api/analytics/events/batch", context)[0] == 404
            assert request("GET", target, ssl.create_default_context(cafile=str(path / "ca.crt")))[0] in (400, 403)
            other_context = ssl.create_default_context(cafile=str(path / "ca.crt"))
            other_context.load_cert_chain(str(path / "other.crt"), str(path / "other.key"))
            assert request("GET", target, other_context)[0] in (400, 403)
            print(f"{role} mTLS proxy synthetic test passed")
        finally:
            subprocess.run(["docker", "rm", "-f", upstream, name], capture_output=True, check=False)


if __name__ == "__main__":
    main()
