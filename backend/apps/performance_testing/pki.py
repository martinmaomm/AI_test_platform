"""Short-lived per-run PKI. A certificate from another run is never trusted."""
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def private_write(path, content):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(content)


def create_run_certificates(directory, server_name, run_id, node_ids):
    node_ids = [str(uuid.UUID(str(node_id))) for node_id in node_ids]
    if not 1 <= len(node_ids) <= 5 or len(set(node_ids)) != len(node_ids):
        raise ValueError('运行证书必须对应 1–5 个不同节点')
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'Performance run {run_id}')])

    def builder(key, subject):
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=30))
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False))

    ca = (builder(ca_key, issuer).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(ca_key, hashes.SHA256()))
    ca_pem = ca.public_bytes(serialization.Encoding.PEM).decode()
    private_write(directory / 'ca.pem', ca_pem)
    certs = {}
    roles = [('server', server_name, ExtendedKeyUsageOID.SERVER_AUTH)]
    roles.extend((f'client-{node_id}', node_id, ExtendedKeyUsageOID.CLIENT_AUTH)
                 for node_id in sorted(node_ids))
    for role, name, usage in roles:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        cert = (builder(key, subject).add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
                .add_extension(x509.ExtendedKeyUsage([usage]), False))
        if role == 'server':
            try:
                identity = x509.IPAddress(ipaddress.ip_address(server_name))
            except ValueError:
                identity = x509.DNSName(server_name)
            cert = cert.add_extension(x509.SubjectAlternativeName([identity]), False)
        certificate = cert.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
        private_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
        private_write(directory / f'{role}.pem', certificate)
        private_write(directory / f'{role}.key', private_key)
        certs[role] = {'cert_pem': certificate, 'key_pem': private_key}
    return {node_id: {'ca_pem': ca_pem, **certs[f'client-{node_id}']}
            for node_id in node_ids}
