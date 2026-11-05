"""Loads the MAC configuration into typed objects shared by the MAC modules.

Two files are merged:
  config/radio.example.yaml       radio, frames, schedule, ack, retry, event_reports, capacity
  config/deployment.example.yaml  zones and node roster (illustrative in the public branch)
A single file that already contains `zones` (the layout used before the split) is accepted as is.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

_CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config"))
DEFAULT_PATH = os.path.join(_CONFIG_DIR, "radio.example.yaml")
DEFAULT_DEPLOYMENT = os.path.join(_CONFIG_DIR, "deployment.example.yaml")


@dataclass(frozen=True)
class Node:
    node_id: int
    sensor_id: str
    device_id: str
    zone: str
    zone_index: int
    position: int
    gateway_id: str


@dataclass
class MacConfig:
    raw: dict
    nodes: dict[int, Node] = field(default_factory=dict)

    @property
    def schedule(self) -> dict:
        return self.raw["schedule"]

    @property
    def frame_mode(self) -> int:
        return int(self.raw["frames"].get("frame_mode", 15))

    @property
    def ack(self) -> dict:
        return self.raw["ack"]

    @property
    def retry(self) -> dict:
        return self.raw["retry"]

    @property
    def radio(self) -> dict:
        return self.raw["radio"]

    def zone_nodes(self, zone: str) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.zone == zone), key=lambda n: n.position)

    def gateway_zone(self, gateway_id: str) -> str:
        for z, v in self.raw["zones"].items():
            if v["gateway_id"] == gateway_id:
                return z
        raise KeyError(gateway_id)


def load(path: str = DEFAULT_PATH, deployment: str | None = DEFAULT_DEPLOYMENT) -> MacConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)
    if "zones" not in raw:
        if not deployment:
            raise ValueError(f"{path} has no zones and no deployment roster was given")
        with open(deployment) as f:
            dep = yaml.safe_load(f)
        raw = {**raw, "zones": dep["zones"], "deployment_provenance": dep.get("provenance", "unspecified")}
    cfg = MacConfig(raw)
    s = raw["schedule"]
    if len(raw["zones"]) * s["zone_window_s"] != s["cycle_s"]:
        raise ValueError("zone windows must tile the cycle exactly")
    for zone, z in raw["zones"].items():
        if len(z["nodes"]) * s["node_slot_s"] > s["zone_window_s"]:
            raise ValueError(f"{zone}: {len(z['nodes'])} slots of {s['node_slot_s']} s exceed the zone window")
        for n in z["nodes"]:
            node = Node(n["node_id"], n["sensor_id"], n["device_id"], zone, z["zone_index"], n["position"], z["gateway_id"])
            if node.node_id in cfg.nodes:
                raise ValueError(f"duplicate node_id {node.node_id}")
            cfg.nodes[node.node_id] = node
    return cfg
