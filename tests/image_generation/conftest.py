"""Portable, offline image MVP suite. No credentials or system fonts required."""
from hashlib import sha256
import json
from pathlib import Path
import socket

import pytest


@pytest.fixture(scope="session", autouse=True)
def check_bundled_font():
    directory = Path(__file__).parent / "fixtures/fonts"
    manifest = json.loads((directory / "provenance.json").read_text())
    for name, info in manifest["files"].items():
        assert sha256((directory / name).read_bytes()).hexdigest() == info["sha256"]


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Image MVP tests must use MockTransport, not a real network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    yield
    assert not attempts, "A test attempted to open a network connection"
