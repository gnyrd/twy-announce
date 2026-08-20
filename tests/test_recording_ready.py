"""The recording_ready gate resolver: is the month's Habit recording claimable?

The Class Recording campaign email carries the recording_ready gate. It may go
out only when a registrant can actually claim a recording, which means the
month's free product exists and still carries its media at the provider. These
tests pin that the resolver reads the provisioning state file and live-verifies
the product, and that every failure mode fails closed (holds the email).
"""
import json

import provision_recording_product as prp


class _FakeClient:
    """Stands in for the Marvelous client. Maps product_id to a product dict,
    None (gone), or an Exception to raise."""

    def __init__(self, products):
        self._products = products
        self.calls = []

    def get_product(self, pid):
        self.calls.append(pid)
        val = self._products.get(pid)
        if isinstance(val, Exception):
            raise val
        return val


def _write_state(tmp_path, payload):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_no_state_file_is_not_ready(tmp_path):
    missing = tmp_path / "nope.json"
    assert prp.recording_ready(
        2026, 9, path=missing, client_factory=lambda: _FakeClient({})
    ) is False


def test_state_with_product_carrying_media_is_ready(tmp_path):
    p = _write_state(tmp_path, {"product_id": 555, "media_id": 42})
    client = _FakeClient({555: {"content_count": 1}})
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: client
    ) is True
    assert client.calls == [555]


def test_state_with_empty_product_is_not_ready(tmp_path):
    p = _write_state(tmp_path, {"product_id": 555})
    client = _FakeClient({555: {"content_count": 0}})
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: client
    ) is False


def test_missing_product_at_provider_is_not_ready(tmp_path):
    p = _write_state(tmp_path, {"product_id": 555})
    client = _FakeClient({555: None})
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: client
    ) is False


def test_provider_error_is_not_ready(tmp_path):
    p = _write_state(tmp_path, {"product_id": 555})
    client = _FakeClient({555: RuntimeError("boom")})
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: client
    ) is False


def test_malformed_state_is_not_ready(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: _FakeClient({})
    ) is False


def test_state_without_product_id_is_not_ready(tmp_path):
    p = _write_state(tmp_path, {"media_id": 42})
    assert prp.recording_ready(
        2026, 9, path=p, client_factory=lambda: _FakeClient({})
    ) is False
