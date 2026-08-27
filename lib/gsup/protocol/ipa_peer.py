# PyHSS IP Access Peer - Represents a peer in the IPA protocol
# Copyright 2025 Lennart Rosam <hello@takuto.de>
# Copyright 2025 Alexander Couzens <lynxis@fe80.eu>
# SPDX-License-Identifier: AGPL-3.0-or-later
from asyncio import StreamReader, StreamWriter
from enum import IntEnum


class IPAPeerRole(IntEnum):
    SGSN = 0
    MSC = 1


class IPAPeer:
    SUPPORTED_IPA_TAGS = list(
        ['SERNR', 'UNITNAME', 'LOCATION1', 'LOCATION2', 'EQUIPVERS', 'SWVERSION', 'IPADDR', 'MACADDR', 'UNIT'])
    # SERNR (osmo-msc: net->msc_ipa_name) is explicitly configurable per instance, unlike UNIT
    # (site/bts/trx id, usually left at the 0/0/0 default) or MACADDR (usually all-zero for
    # software network elements without a real NIC).
    _PRIMARY_ID_PREFERENCE = list(['SERNR', 'UNIT', 'MACADDR'])
    _ROLE_PREFERENCE_TAGS = list(['LOCATION2', 'UNIT', 'UNITNAME'])

    def __init__(self, name: str, tags: dict, reader: StreamReader, writer: StreamWriter):
        self.name = name
        self.tags = tags
        self.primary_id = None
        self.role = None
        self.reader = reader
        self.writer = writer

        # Resolve the primary ID by preference
        for tag in self._PRIMARY_ID_PREFERENCE:
            if tag in tags:
                self.primary_id = tags[tag]
                break

        if self.primary_id is None:
            raise ValueError(
                "No primary ID found in the tags. Need at least one of: " + ', '.join(self._PRIMARY_ID_PREFERENCE))

        # Resolve role by tags
        for tag in self._ROLE_PREFERENCE_TAGS:
            if tag in tags:
                tag_val = tags[tag]
                if IPAPeerRole.MSC.name.lower() in tag_val.lower():
                    self.role = IPAPeerRole.MSC
                    break
                elif IPAPeerRole.SGSN.name.lower() in tag_val.lower():
                    self.role = IPAPeerRole.SGSN
                    break

        if self.role is None:
            raise ValueError(
                "Role not found in tags. 'sgsn' or 'msc' must appear in one of there tags: " + ', '.join(
                    self._ROLE_PREFERENCE_TAGS))
    def __str__(self):
        return f"[{self.name} ({self.role.name})]"
