"""Make sure secure (HTTPS) connections can find the system's certificates.

The bundled OpenSSL looks for certificates where the system the program was
built on keeps them. A build made on Ubuntu looks in /usr/lib/ssl, which Arch
and CachyOS don't have, so every HTTPS request failed there (no covers, no
logo). This points OpenSSL at the certificates wherever this system keeps them,
and falls back to the copy bundled with the program (certifi).
"""
import logging
import os
import ssl

log = logging.getLogger(__name__)

# Where Linux distributions keep their certificate bundle.
SYSTEM_BUNDLES = [
    "/etc/ssl/certs/ca-certificates.crt",                # Debian, Ubuntu, Arch, CachyOS
    "/etc/pki/tls/certs/ca-bundle.crt",                  # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                            # openSUSE
    "/etc/ssl/cert.pem",                                 # Arch, Alpine, macOS
]


def _usable(path: str | None, directory: bool = False) -> bool:
    if not path:
        return False
    if directory:
        try:
            return os.path.isdir(path) and any(os.scandir(path))
        except OSError:
            return False
    return os.path.isfile(path)


def ensure_ca_certs() -> str | None:
    """Point OpenSSL at a certificate bundle if its own default isn't there.
    Returns the bundle chosen, or None when nothing had to change."""
    if os.environ.get("SSL_CERT_FILE") or os.name == "nt":
        return None  # set by the user, or Windows (which uses its own store)
    paths = ssl.get_default_verify_paths()
    if _usable(paths.cafile) or _usable(paths.capath, directory=True):
        return None
    for bundle in SYSTEM_BUNDLES:
        if _usable(bundle):
            os.environ["SSL_CERT_FILE"] = bundle
            return bundle
    try:
        import certifi
        bundle = certifi.where()
        if _usable(bundle):
            os.environ["SSL_CERT_FILE"] = bundle
            return bundle
    except ImportError:
        pass
    log.warning("no certificate bundle found: covers and logos can't be looked up "
                "(set SSL_CERT_FILE to your system's CA bundle)")
    return None
