import math
from typing import Dict


class MirrorFieldMesh:
    def __init__(self, nodes: Dict[str, "MirrorFieldNode"]):
        self.nodes = nodes

    def update(self, inputs: Dict[str, Dict[str, float]]) -> Dict:
        node_outputs = []
        for node_id, node in self.nodes.items():
            data = inputs.get(node_id)
            if not data:
                continue
            out = node.update(
                atmo_entropy=data.get("atmo_entropy", 0.0),
                market_entropy=data.get("market_entropy", 0.0),
                model_uncertainty=data.get("model_uncertainty", 0.0),
            )
            node_outputs.append(out)

        return self._aggregate(node_outputs)

    def _aggregate(self, outputs: list[Dict]) -> Dict:
        if not outputs:
            return {
                "global_coherence": 1.0,
                "spatial_entropy": 0.0,
                "synchronization": 1.0,
                "regime": "NO_DATA",
                "node_count": 0,
            }

        coherences = [o["coherence"] for o in outputs]
        global_c = sum(coherences) / len(coherences)

        variance = sum((c - global_c) ** 2 for c in coherences) / len(coherences)
        spatial_entropy = math.sqrt(variance)

        sync = 1.0 - min(1.0, spatial_entropy)
        regime = self._classify(global_c, spatial_entropy)

        return {
            "global_coherence": global_c,
            "spatial_entropy": round(spatial_entropy, 4),
            "synchronization": round(sync, 4),
            "regime": regime,
            "node_count": len(outputs),
        }

    def _classify(self, C: float, S: float) -> str:
        if C > 0.70 and S < 0.15:
            return "GLOBAL_CALM"
        if S >= 0.25:
            return "DESYNCHRONIZED_TRANSITION"
        if C > 0.15:
            return "GLOBAL_TRANSITION"
        return "GLOBAL_CHAOS"