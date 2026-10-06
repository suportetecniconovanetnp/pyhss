# Copyright 2026 Carlos Henrique <carloshenriquedeoliveiragomes@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that hssService runs its periodic housekeeping outside the diameter request loop.

handleQueue() processes every inbound diameter message serially in one thread. The table scans done by
clearExpiredEmergencySubscribers() and generatePromStats() used to run inline at the end of a batch, which
froze that loop for several seconds once a minute: requests (and DWR/DWA watchdogs) that arrived meanwhile
were answered late or lost.
"""

import importlib
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from baseModels import InboundData

top_dir = Path(Path(__file__) / "../..").resolve()
sys.path.append(str(top_dir / "services"))
hssService = importlib.import_module("hssService")


class _StopLoop(BaseException):
    """handleQueue() only catches Exception, so this breaks out of its endless loop."""


def _service():
    """An HssService without the Redis, database and Diameter setup done by __init__()."""
    service = object.__new__(hssService.HssService)
    service.logTool = Mock()
    service.redisMessaging = Mock()
    service.diameterLibrary = Mock()
    service.benchmarking = False
    service.hostname = "hss01"
    service.emergencySubscriberCleanupInterval = 60
    service.lastEmergencySubscriberCleanup = 0.0
    service.promStatsInterval = 60
    service.lastPromStatsGeneration = 0.0
    service.housekeepingStop = threading.Event()
    service.housekeepingPollInterval = 0.01
    service.getPeerHostname = Mock(return_value=None)
    return service


def _wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def test_handle_queue_does_not_run_housekeeping():
    service = _service()
    service.clearExpiredEmergencySubscribers = Mock()
    service.generatePromStats = Mock()

    inbound = InboundData(SenderIp="127.0.0.1", SenderPort="1234", InitialReceiveTimestamp=1, InboundHex="aa")
    service.redisMessaging.awaitBulkMessage.side_effect = [(b"key", [inbound.model_dump_json().encode()]), _StopLoop()]
    service.diameterLibrary.split_diameter_message.return_value = ["aa"]
    service.diameterLibrary.generateDiameterResponse.return_value = "bb"
    service.diameterLibrary.getDiameterMessageType.return_value = {"inbound": "DWR", "outbound": "DWA"}

    with pytest.raises(_StopLoop):
        service.handleQueue()

    # The request was answered, and no table scan ran in between.
    assert service.redisMessaging.sendMessage.call_count == 1
    service.clearExpiredEmergencySubscribers.assert_not_called()
    service.generatePromStats.assert_not_called()
    service.diameterLibrary.clear_expired_emergency_subscribers.assert_not_called()
    service.diameterLibrary.Generate_Prom_Stats.assert_not_called()


def test_housekeeping_thread_runs_both_tasks():
    service = _service()
    thread = service.startHousekeeping()
    try:
        assert _wait_for(lambda: service.diameterLibrary.clear_expired_emergency_subscribers.called)
        assert _wait_for(lambda: service.diameterLibrary.Generate_Prom_Stats.called)
        assert thread.daemon
    finally:
        service.housekeepingStop.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_housekeeping_tasks_are_throttled_by_their_interval():
    service = _service()
    thread = service.startHousekeeping()
    try:
        assert _wait_for(lambda: service.diameterLibrary.Generate_Prom_Stats.called)
        time.sleep(0.1)  # many polls at 10ms, but the 60s intervals have not elapsed
    finally:
        service.housekeepingStop.set()
        thread.join(timeout=2)
    assert service.diameterLibrary.clear_expired_emergency_subscribers.call_count == 1
    assert service.diameterLibrary.Generate_Prom_Stats.call_count == 1


def test_housekeeping_survives_a_failing_task():
    service = _service()
    service.promStatsInterval = 0
    service.diameterLibrary.Generate_Prom_Stats.side_effect = RuntimeError("database unavailable")
    thread = service.startHousekeeping()
    try:
        assert _wait_for(lambda: service.diameterLibrary.Generate_Prom_Stats.call_count >= 3)
        assert thread.is_alive()
    finally:
        service.housekeepingStop.set()
        thread.join(timeout=2)


def test_slow_housekeeping_does_not_block_the_request_loop():
    service = _service()
    release = threading.Event()
    service.diameterLibrary.Generate_Prom_Stats.side_effect = lambda: release.wait(timeout=5)
    thread = service.startHousekeeping()
    try:
        assert _wait_for(lambda: service.diameterLibrary.Generate_Prom_Stats.called)

        inbound = InboundData(SenderIp="127.0.0.1", SenderPort="1234", InitialReceiveTimestamp=1, InboundHex="aa")
        service.redisMessaging.awaitBulkMessage.side_effect = [(b"key", [inbound.model_dump_json().encode()]), _StopLoop()]
        service.diameterLibrary.split_diameter_message.return_value = ["aa"]
        service.diameterLibrary.generateDiameterResponse.return_value = "bb"
        service.diameterLibrary.getDiameterMessageType.return_value = {"inbound": "DWR", "outbound": "DWA"}

        started = time.monotonic()
        with pytest.raises(_StopLoop):
            service.handleQueue()
        elapsed = time.monotonic() - started

        # Housekeeping is still blocked, and the request was answered anyway.
        assert not release.is_set()
        assert service.redisMessaging.sendMessage.call_count == 1
        assert elapsed < 1.0
    finally:
        release.set()
        service.housekeepingStop.set()
        thread.join(timeout=2)
