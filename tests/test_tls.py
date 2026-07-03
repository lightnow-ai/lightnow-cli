"""Tests for TLS trust setup."""

from lightnow_cli import tls


def test_configure_tls_trust_store_uses_os_store(monkeypatch) -> None:
    """The CLI configures Python HTTPS clients to use the OS trust store."""
    calls: list[bool] = []

    monkeypatch.setattr(tls.truststore, "inject_into_ssl", lambda: calls.append(True))

    tls.configure_tls_trust_store()

    assert calls == [True]
