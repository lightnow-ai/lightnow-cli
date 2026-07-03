"""TLS trust configuration."""

import truststore


def configure_tls_trust_store() -> None:
    """Use the operating system certificate store for HTTPS verification."""
    truststore.inject_into_ssl()
