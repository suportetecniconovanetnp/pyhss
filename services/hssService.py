#!/usr/bin/env python3
# Copyright 2023-2025 David Kneipp <david@davidkneipp.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
import os, sys, json, time, traceback, socket, threading
from datetime import datetime

sys.path.append(os.path.realpath(os.path.dirname(__file__) + "/../lib"))

from messaging import RedisMessaging
from diameter import Diameter
from banners import Banners
from logtool import LogTool
from baseModels import Peer, InboundData, OutboundData
import pydantic_core
from pyhss_config import config


class HssService:
    
    def __init__(self):
        self.redisUseUnixSocket = config.get('redis', {}).get('useUnixSocket', False)
        self.redisUnixSocketPath = config.get('redis', {}).get('unixSocketPath', '/var/run/redis/redis-server.sock')
        self.redisHost = config.get('redis', {}).get('host', 'localhost')
        self.redisPort = config.get('redis', {}).get('port', 6379)
        self.redisMessaging = RedisMessaging(host=self.redisHost, port=self.redisPort, useUnixSocket=self.redisUseUnixSocket, unixSocketPath=self.redisUnixSocketPath)
        self.logTool = LogTool(config=config)
        self.banners = Banners()
        self.mnc = config.get('hss', {}).get('MNC', '999')
        self.mcc = config.get('hss', {}).get('MCC', '999')
        self.originRealm = config.get('hss', {}).get('OriginRealm', f'mnc{self.mnc}.mcc{self.mcc}.3gppnetwork.org')
        self.originHost = config.get('hss', {}).get('OriginHost', f'hss01')
        self.productName = config.get('hss', {}).get('ProductName', f'PyHSS')
        self.logTool.log(service='HSS', level='info', message=f"{self.banners.hssService()}", redisClient=self.redisMessaging)
        self.diameterLibrary = Diameter(
            logTool=self.logTool,
            originHost=self.originHost,
            originRealm=self.originRealm,
            productName=self.productName,
            mcc=self.mcc,
            mnc=self.mnc,
            main_service=True,
        )
        self.benchmarking = config.get('hss').get('enable_benchmarking', False)
        self.hostname = self.originHost
        self.diameterPeerKey = config.get('hss', {}).get('diameter_peer_key', 'diameterPeers')
        # Housekeeping and peer lookups are kept out of the per-message hot path.
        self.emergencySubscriberCleanupInterval = int(config.get('hss', {}).get('emergency_subscriber_cleanup_interval', 60))
        self.lastEmergencySubscriberCleanup = 0.0
        self.peerCacheInterval = int(config.get('hss', {}).get('peer_cache_interval', 5))
        self.peerCache = {}
        self.lastPeerCacheRefresh = 0.0
        # Diagnostic watchdog: the redis-py client used by awaitBulkMessage has
        # no socket timeout, so a half-dead connection to Redis blocks handleQueue
        # forever with no exception and no log line. This reports how long it has
        # been since the last successful read via plain stdout prints, bypassing
        # LogTool (which itself writes to Redis and could block too) so the
        # watchdog keeps reporting even while handleQueue is stuck.
        self.redisWatchdogInterval = int(config.get('hss', {}).get('redis_watchdog_interval', 15))
        self.lastQueueActivity = time.monotonic()
        threading.Thread(target=self.redisWatchdog, daemon=True).start()

    def getPeerHostname(self, senderIp: str, senderPort: str):
        """
        Returns the hostname of the diameter peer matching the given ip and port,
        or None if no peer matches.

        The peer list changes rarely, so it is cached for peerCacheInterval seconds.
        Reading it from Redis on every inbound message put a round trip (plus a full
        hash decode) in front of every single diameter request.
        """
        now = time.monotonic()
        if now - self.lastPeerCacheRefresh >= self.peerCacheInterval:
            try:
                peerCache = {}
                diameterPeers = self.redisMessaging.getAllHashData(self.diameterPeerKey, usePrefix=True, prefixHostname=self.hostname, prefixServiceName='diameter')
                if diameterPeers:
                    for diameterPeerKey, diameterPeerValue in diameterPeers.items():
                        diameterPeer = Peer.model_validate(pydantic_core.from_json(json.dumps(diameterPeerValue)))
                        peerCache[(diameterPeer.IpAddress, diameterPeer.Port)] = diameterPeer.Hostname
                self.peerCache = peerCache
                self.lastPeerCacheRefresh = now
            except Exception as e:
                self.logTool.log(service='HSS', level='error', message=f"[HSS] [getPeerHostname] Error refreshing peer cache: {traceback.format_exc()}", redisClient=self.redisMessaging)

        return self.peerCache.get((senderIp, senderPort), None)

    def clearExpiredEmergencySubscribers(self) -> None:
        """
        Runs the emergency subscriber expiry sweep at most once every
        emergencySubscriberCleanupInterval seconds.

        This is periodic housekeeping which scans the whole EMERGENCY_SUBSCRIBER
        table, so running it once per processed diameter message serialised a full
        table scan in front of every request.
        """
        now = time.monotonic()
        if now - self.lastEmergencySubscriberCleanup < self.emergencySubscriberCleanupInterval:
            return
        self.lastEmergencySubscriberCleanup = now
        try:
            self.diameterLibrary.clear_expired_emergency_subscribers()
        except Exception as e:
            self.logTool.log(service='HSS', level='error', message=f"[HSS] [clearExpiredEmergencySubscribers] Exception: {traceback.format_exc()}", redisClient=self.redisMessaging)

    def redisWatchdog(self):
        """
        Prints, on a fixed interval, how long it has been since handleQueue last
        got data back from Redis. A healthy service prints a small, steady age
        each tick. If the age keeps climbing past redisWatchdogInterval, the
        process is stuck inside the blocking Redis read (see comment on
        lastQueueActivity above), which otherwise fails completely silently.
        """
        while True:
            time.sleep(self.redisWatchdogInterval)
            age = time.monotonic() - self.lastQueueActivity
            timestamp = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            status = "OK" if age < self.redisWatchdogInterval * 2 else "STALLED?"
            print(f"[{timestamp}] [WATCHDOG] [HSS] Last successful Redis read was {age:.1f}s ago ({status})", flush=True)

    def handleQueue(self):
        """
        Gets and parses inbound diameter requests, processes them and queues the response.
        """
        while True:
            try:
                if self.benchmarking:
                    startTime = time.perf_counter()

                inboundMessageList = self.redisMessaging.awaitBulkMessage(key='diameter-inbound', usePrefix=True, prefixHostname=self.hostname, prefixServiceName='diameter')
                self.lastQueueActivity = time.monotonic()

                if inboundMessageList == None:
                    continue
                for inboundMessage in inboundMessageList[1]:
                    self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] Message: {inboundMessage}", redisClient=self.redisMessaging)
                    inboundMessage = inboundMessage.decode('ascii')
                    inboundData = InboundData.model_validate(pydantic_core.from_json(inboundMessage))
                    inboundBinary = bytes.fromhex(inboundData.InboundHex)

                    if inboundBinary == None:
                        continue

                    buffered_diameter_messages = self.diameterLibrary.split_diameter_message(inboundBinary)
                    self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] Buffered diameter messages: {buffered_diameter_messages}", redisClient=self.redisMessaging)
                    messageNumber = 1

                    for buffered_diameter_message in buffered_diameter_messages:
                        self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] Processing message ({messageNumber} of {len(buffered_diameter_messages)}): {buffered_diameter_message}", redisClient=self.redisMessaging)

                        try:
                            # If this is a message from a stored peer, increment prom_diam_request_count_host by 1.
                            peerHostname = self.getPeerHostname(inboundData.SenderIp, inboundData.SenderPort)
                            if peerHostname:
                                self.redisMessaging.sendMetric(serviceName='diameter', metricName='prom_diam_request_count_host',
                                            metricType='gauge', metricAction='inc',
                                            metricLabels={
                                            "host": peerHostname},
                                            metricValue=float(1), metricHelp='Number of Diameter Requests Recieved per Host',
                                            metricExpiry=60,
                                            usePrefix=True,
                                            prefixHostname=self.hostname,
                                            prefixServiceName='metric')

                        except Exception as e:
                            self.logTool.log(service='HSS', level='error', message=f"[HSS] [handleQueue] Error updating prom_diam_request_count_host: {traceback.format_exc()}", redisClient=self.redisMessaging)
                            pass

                        try:
                            messageBinary = bytes.fromhex(buffered_diameter_message)
                            diameterOutbound = self.diameterLibrary.generateDiameterResponse(binaryData=messageBinary)

                            if diameterOutbound == None:
                                continue
                            if not len(diameterOutbound) > 0:
                                continue

                            diameterMessageTypeDict = self.diameterLibrary.getDiameterMessageType(binaryData=messageBinary)
                            
                            if diameterMessageTypeDict == None:
                                continue
                            if not len(diameterMessageTypeDict) > 0:
                                continue

                            diameterMessageTypeInbound = diameterMessageTypeDict.get('inbound', '')
                            diameterMessageTypeOutbound = diameterMessageTypeDict.get('outbound', '')
                        except Exception as e:
                            self.logTool.log(service='HSS', level='warning', message=f"[HSS] [handleQueue] Failed to generate diameter outbound: {e}", redisClient=self.redisMessaging)
                            continue
                        
                        outboundQueue = f"diameter-outbound-{inboundData.SenderIp}-{inboundData.SenderPort}"
                        outboundMessage = OutboundData(DestinationIp=inboundData.SenderIp,
                                                    DestinationPort=inboundData.SenderPort,
                                                    InitialReceiveTimestamp=inboundData.InitialReceiveTimestamp,
                                                    OutboundHex=diameterOutbound)

                        self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] [{diameterMessageTypeOutbound}] Generated Diameter Outbound: {diameterOutbound}", redisClient=self.redisMessaging)
                        self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] [{diameterMessageTypeOutbound}] Outbound Diameter Queue: {outboundQueue}", redisClient=self.redisMessaging)
                        self.logTool.log(service='HSS', level='debug', message=f"[HSS] [handleQueue] [{diameterMessageTypeOutbound}] Outbound Diameter: {outboundMessage}", redisClient=self.redisMessaging)

                        self.redisMessaging.sendMessage(queue=outboundQueue, message=outboundMessage.model_dump_json(), queueExpiry=60, usePrefix=True, prefixHostname=self.hostname, prefixServiceName='diameter')
                        messageNumber += 1
                        if self.benchmarking:
                            self.logTool.log(service='HSS', level='info', message=f"[HSS] [handleQueue] [{diameterMessageTypeInbound}] Time taken to process request: {round(((time.perf_counter() - startTime)*1000), 3)} ms", redisClient=self.redisMessaging)

                        try:
                            peerHostname = self.getPeerHostname(inboundData.SenderIp, inboundData.SenderPort)
                            if peerHostname:
                                self.redisMessaging.sendMetric(serviceName='diameter', metricName='prom_diam_response_count_host',
                                            metricType='gauge', metricAction='inc',
                                            metricLabels={
                                            "host": peerHostname},
                                            metricValue=float(1), metricHelp='Number of Diameter Responses Sent per Host',
                                            metricExpiry=60,
                                            usePrefix=True,
                                            prefixHostname=self.hostname,
                                            prefixServiceName='metric')

                        except Exception as e:
                            self.logTool.log(service='HSS', level='error', message=f"[HSS] [handleQueue] Error updating prom_diam_response_count_host: {traceback.format_exc()}", redisClient=self.redisMessaging)
                            pass

                # Periodic housekeeping, once per batch at most, after the
                # responses for this batch have already been queued.
                self.clearExpiredEmergencySubscribers()

            except Exception as e:
                self.logTool.log(service='HSS', level='error', message=f"[HSS] [handleQueue] Exception: {traceback.format_exc()}", redisClient=self.redisMessaging)
                continue


def main():
    hssService = HssService()
    hssService.handleQueue()


if __name__ == '__main__':
    main()
