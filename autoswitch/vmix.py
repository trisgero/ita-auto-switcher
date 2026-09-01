"""HTTP client for the vMix API.

Using the API instead of injecting keystrokes solves two problems at once:
1. commands no longer depend on which window has focus;
2. we can READ vMix's actual state, which is the feedback the OBS pixel
   matcher lacks - and without which there's no way to notice a dropped
   command.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests


class VmixError(RuntimeError):
    pass


@dataclass
class VmixState:
    active: int
    preview: int
    overlays: dict[int, str] = field(default_factory=dict)
    inputs: dict[int, str] = field(default_factory=dict)  # number -> title


class VmixClient:
    def __init__(self, url: str = "http://127.0.0.1:8088/api", timeout: float = 1.0):
        self.url = url
        self.timeout = timeout
        self._session = requests.Session()

    def state(self) -> VmixState:
        try:
            resp = self._session.get(self.url, timeout=self.timeout)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            raise VmixError(f"failed to read vMix state: {exc}") from exc

        inputs: dict[int, str] = {}
        for node in root.findall("./inputs/input"):
            try:
                inputs[int(node.get("number", "0"))] = node.get("title", "")
            except ValueError:
                continue

        key_to_number = {
            node.get("key"): int(node.get("number", "0"))
            for node in root.findall("./inputs/input")
            if node.get("key")
        }
        overlays: dict[int, str] = {}
        for node in root.findall("./overlays/overlay"):
            if node.text:
                idx = int(node.get("number", "0"))
                overlays[idx] = str(key_to_number.get(node.text, node.text))

        def _int(tag: str) -> int:
            node = root.find(tag)
            try:
                return int(node.text) if node is not None and node.text else 0
            except ValueError:
                return 0

        return VmixState(active=_int("active"), preview=_int("preview"), overlays=overlays, inputs=inputs)

    def call(self, function: str, **params) -> None:
        query = {"Function": function}
        query.update({k: str(v) for k, v in params.items() if v is not None})
        try:
            resp = self._session.get(self.url, params=query, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise VmixError(f"command {function} {params} failed: {exc}") from exc

    def merge(self, input_number: int) -> None:
        """Same function called by the Up/Left/Right/Space shortcuts."""
        self.call("Merge", Input=input_number)
