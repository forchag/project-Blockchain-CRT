"""Semtech UDP packet-forwarder (GWMP v2) client.

Lets the MAC run against any SX1301/SX1302 concentrator running lora_pkt_fwd
(configured with server_address = this host). Uplinks arrive as PUSH_DATA
"rxpk"; downlinks (Poll, ACK) leave as PULL_RESP "txpk" with immediate or
timestamped transmission.
"""
from __future__ import annotations

import base64
import json
import os
import socket
from dataclasses import dataclass

PUSH_DATA, PUSH_ACK, PULL_DATA, PULL_RESP, PULL_ACK, TX_ACK = 0, 1, 2, 3, 4, 5
VERSION = 2


@dataclass
class Uplink:
    gateway_eui: str
    tmst: int            # concentrator microsecond counter at end of RX
    freq_mhz: float
    datr: str
    rssi: int
    lsnr: float
    payload: bytes
    crc_ok: bool


class SemtechUdp:
    def __init__(self, bind: str = "0.0.0.0", port: int = 1700, timeout_s: float = 0.05):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, port))
        self.sock.settimeout(timeout_s)
        self.pull_addr = None   # forwarder address learned from PULL_DATA, needed for downlinks
        self.gateway_eui = None

    def poll(self) -> list[Uplink]:
        """Handle at most one datagram; returns any uplinks it carried."""
        try:
            data, addr = self.sock.recvfrom(65535)
        except socket.timeout:
            return []
        if len(data) < 4 or data[0] != VERSION:
            return []
        token, ident = data[1:3], data[3]
        if ident == PULL_DATA:
            self.pull_addr = addr
            self.gateway_eui = data[4:12].hex()
            self.sock.sendto(bytes([VERSION]) + token + bytes([PULL_ACK]), addr)
            return []
        if ident != PUSH_DATA:
            return []
        self.sock.sendto(bytes([VERSION]) + token + bytes([PUSH_ACK]), addr)
        eui = data[4:12].hex()
        try:
            msg = json.loads(data[12:])
        except ValueError:
            return []
        out = []
        for r in msg.get("rxpk", []):
            out.append(Uplink(eui, int(r.get("tmst", 0)), float(r["freq"]), r.get("datr", ""), int(r.get("rssi", 0)),
                              float(r.get("lsnr", 0.0)), base64.b64decode(r["data"]), r.get("stat", 0) == 1))
        return out

    def send(self, payload: bytes, freq_hz: int, sf: int = 9, bw_khz: int = 125, power_dbm: int = 14,
             tmst: int | None = None) -> bool:
        """Queue a downlink. tmst=None transmits immediately."""
        if self.pull_addr is None:
            return False
        txpk = {"imme": tmst is None, "freq": freq_hz / 1e6, "rfch": 0, "powe": power_dbm, "modu": "LORA",
                "datr": f"SF{sf}BW{bw_khz}", "codr": "4/5", "ipol": False, "size": len(payload),
                "data": base64.b64encode(payload).decode(), "ncrc": False}
        if tmst is not None:
            txpk["tmst"] = tmst
        token = os.urandom(2)
        self.sock.sendto(bytes([VERSION]) + token + bytes([PULL_RESP]) + json.dumps({"txpk": txpk}).encode(),
                         self.pull_addr)
        return True
