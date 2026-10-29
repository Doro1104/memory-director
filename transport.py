"""Self-signed TLS with an explicitly scanned certificate fingerprint."""
import datetime
import hashlib
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from runtime import DATA_DIR,LAN_ENABLED,LAN_PORT,WEB_LAN_ENABLED,WEB_PORT


def certificate():
    folder=DATA_DIR/'transport';folder.mkdir(parents=True,exist_ok=True)
    cert=folder/'server.crt';key=folder/'server.key'
    if cert.exists()!=key.exists():raise ValueError('连接证书不完整；请恢复备份，不自动替换已有证书')
    if not cert.exists():
        private=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Memory Director local host')])
        now=datetime.datetime.now(datetime.timezone.utc)
        value=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
               .not_valid_after(now+datetime.timedelta(days=3650))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
               .sign(private,hashes.SHA256()))
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert.write_bytes(value.public_bytes(serialization.Encoding.PEM))
        try:key.chmod(0o600)
        except OSError:pass
    value=x509.load_pem_x509_certificate(cert.read_bytes())
    fingerprint=hashlib.sha256(value.public_bytes(serialization.Encoding.DER)).hexdigest()
    return cert,key,fingerprint


def connection_info(addresses):
    web_urls=[f'http://{address}:{WEB_PORT}' for address in addresses] if WEB_LAN_ENABLED else []
    if not LAN_ENABLED:return {'urls':[],'pairing':[],'web_urls':web_urls}
    _,_,fingerprint=certificate()
    pairs=[{'version':1,'url':f'https://{address}:{LAN_PORT}','certificate_sha256':fingerprint} for address in addresses]
    return {'urls':[p['url'] for p in pairs],'pairing':pairs,'web_urls':web_urls}
