# Copyright 2026 Carlos Henrique <carloshenriquedeoliveiragomes@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for how hssService.handleQueue() treats requests that waited too long, and a failing Redis.

The inbound Redis queue only expires when nothing is pushed to it for diameter_request_timeout, so during
a burst its backlog never expires. handleQueue() drops requests older than max_inbound_message_age instead,
so the HSS doesn't spend its time answering requests that the peers have already retried.
"""

import importlib
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from baseModels import InboundData

top_dir = Path(Path(__file__) / "../..").resolve()
sys.path.append(str(top_dir / "services"))
hssService = importlib.import_module("hssService")


class _StopLoop(BaseException):
    """handleQueue() only catches Exception, so this breaks out of its endless loop."""


def _diameter_header(command_code):
    """A bare 20 byte Diameter header, as hex."""
    return (bytes([1]) + (20).to_bytes(3, "big") + bytes([0x80]) + command_code.to_bytes(3, "big") + bytes(12)).hex()


def _service(max_age=10.0):
    service = object.__new__(hssService.HssService)
    service.logTool = Mock()
    service.redisMessaging = Mock()
    service.diameterLibrary = Mock()
    service.benchmarking = False
    service.hostname = "hss01"
    service.maxInboundMessageAge = max_age
    service.getPeerHostname = Mock(return_value=None)
    service.clearExpiredEmergencySubscribers = Mock()
    service.generatePromStats = Mock()
    service.diameterLibrary.generateDiameterResponse.return_value = "bb"
    service.diameterLibrary.getDiameterMessageType.return_value = {"inbound": "REQ", "outbound": "ANS"}
    return service


def _run_one_batch(service, command_code, age_seconds):
    """Feed handleQueue() one queued message, received age_seconds ago, then stop the loop."""
    message_hex = _diameter_header(command_code)
    received = time.time_ns() - int(age_seconds * 1e9)
    inbound = InboundData(SenderIp="127.0.0.1", SenderPort="1234", InitialReceiveTimestamp=received, InboundHex=message_hex)
    service.diameterLibrary.split_diameter_message.return_value = [message_hex]
    service.redisMessaging.awaitBulkMessage.side_effect = [(b"key", [inbound.model_dump_json().encode()]), _StopLoop()]
    with pytest.raises(_StopLoop):
        service.handleQueue()


def _answered(service):
    return service.redisMessaging.sendMessage.call_count == 1


def test_fresh_request_is_answered():
    service = _service()
    _run_one_batch(service, command_code=272, age_seconds=1)
    assert _answered(service)
    service.redisMessaging.sendMetric.assert_not_called()


def test_stale_request_is_dropped_and_reported():
    service = _service()
    _run_one_batch(service, command_code=272, age_seconds=30)

    assert not _answered(service)
    service.diameterLibrary.generateDiameterResponse.assert_not_called()

    warnings = [c.kwargs["message"] for c in service.logTool.log.call_args_list if c.kwargs.get("level") == "warning"]
    assert any("Dropped 1 diameter message" in w for w in warnings)
    metric_names = [c.kwargs["metricName"] for c in service.redisMessaging.sendMetric.call_args_list]
    assert metric_names == ["prom_diam_stale_request_dropped"]


@pytest.mark.parametrize("command_code", [257, 280, 282])
def test_stale_capabilities_watchdog_and_disconnect_are_still_answered(command_code):
    service = _service()
    _run_one_batch(service, command_code=command_code, age_seconds=30)
    assert _answered(service)


def test_age_check_can_be_disabled():
    service = _service(max_age=0)
    _run_one_batch(service, command_code=272, age_seconds=3600)
    assert _answered(service)


def test_message_from_the_future_is_not_stale():
    """Clock skew between the diameter and hss hosts must not make every request look stale."""
    service = _service()
    _run_one_batch(service, command_code=272, age_seconds=-60)
    assert _answered(service)


def test_unparseable_message_is_not_treated_as_stale():
    service = _service()
    inbound = InboundData(SenderIp="127.0.0.1", SenderPort="1234", InitialReceiveTimestamp=1, InboundHex="aa")
    assert not service.isStaleRequest(inbound, "zz")


def test_handle_queue_backs_off_when_redis_is_unreachable():
    service = _service()
    # awaitBulkMessage() returns '' straight away, instead of blocking, while Redis can't be reached.
    service.redisMessaging.awaitBulkMessage.side_effect = ["", "", _StopLoop()]

    with patch.object(hssService.time, "sleep") as sleep:
        with pytest.raises(_StopLoop):
            service.handleQueue()

    assert [c.args for c in sleep.call_args_list] == [(1,), (1,)]
    service.logTool.log.assert_not_called()


def test_handle_queue_does_not_sleep_when_a_message_list_is_returned():
    service = _service()
    with patch.object(hssService.time, "sleep") as sleep:
        _run_one_batch(service, command_code=272, age_seconds=1)
    sleep.assert_not_called()
