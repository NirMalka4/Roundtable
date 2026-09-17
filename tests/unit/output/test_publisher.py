from pathlib import Path

import pytest

from roundtable.delivery import (
    PublishOutcome,
    PublishRequest,
    RetractRequest,
    get_publisher,
    register_publisher,
)


def test_test_publisher_registers_without_delivery_changes(tmp_path: Path) -> None:
    class Fake:
        name = "test_publisher_contract"

        def publish(self, request):
            assert request.session_dir == tmp_path
            return PublishOutcome(True, 0)

        def retract(self, request):
            assert request.session_dir == tmp_path
            return PublishOutcome(True, 0)

    register_publisher(Fake.name, Fake())
    publisher = get_publisher(Fake.name)
    assert publisher is not None
    assert publisher.publish(PublishRequest(tmp_path)).ok
    assert publisher.retract(RetractRequest(tmp_path)).ok
    with pytest.raises(ValueError, match="already registered"):
        register_publisher(Fake.name, Fake())
