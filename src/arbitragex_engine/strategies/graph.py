"""Directed rate graph + Bellman-Ford negative-cycle detection for
triangular arbitrage. Standard reduction: an arbitrage cycle exists iff
the graph with edge weights w(i,j) = -log(rate(i,j)) has a negative-weight
cycle, because sum(-log(rate)) < 0  <=>  product(rate) > 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise


@dataclass(frozen=True)
class RateEdge:
    base: str
    quote: str
    rate: float  # units of quote per 1 unit of base


class RateGraph:
    def __init__(self) -> None:
        self._edges: dict[str, dict[str, float]] = {}

    def add_edge(self, base: str, quote: str, rate: float) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate} for {base}->{quote}")
        self._edges.setdefault(base, {})[quote] = rate

    def nodes(self) -> list[str]:
        node_set = set(self._edges.keys())
        for targets in self._edges.values():
            node_set.update(targets.keys())
        return sorted(node_set)

    def weight(self, u: str, v: str) -> float | None:
        rate = self._edges.get(u, {}).get(v)
        if rate is None:
            return None
        return -math.log(rate)

    def find_negative_cycle(self) -> list[str] | None:
        """Bellman-Ford from a virtual source connected to every node with
        weight 0, relaxing |V|-1 times then checking for a further
        relaxable edge on the |V|-th pass. Returns the cycle (list of node
        names, closed — first == last) or None."""
        nodes = self.nodes()
        if len(nodes) < 3:
            return None

        dist = {n: 0.0 for n in nodes}  # virtual source has 0-weight edges to all nodes
        predecessor: dict[str, str | None] = {n: None for n in nodes}

        edges = [(u, v, w) for u, targets in self._edges.items() for v, rate in targets.items() if (w := self.weight(u, v)) is not None]

        x: str | None = None
        for _ in range(len(nodes) - 1):
            x = None
            for u, v, w in edges:
                if dist[u] + w < dist[v] - 1e-12:
                    dist[v] = dist[u] + w
                    predecessor[v] = u

        # One more pass to find a node still relaxable => part of a negative cycle
        x = None
        for u, v, w in edges:
            if dist[u] + w < dist[v] - 1e-12:
                x = v
                dist[v] = dist[u] + w
                predecessor[v] = u
                break

        if x is None:
            return None

        # Walk predecessors |V| times to guarantee landing inside the cycle
        for _ in range(len(nodes)):
            x = predecessor[x]
            if x is None:
                return None

        cycle = [x]
        cur = predecessor[x]
        while cur != x:
            if cur is None:
                return None
            cycle.append(cur)
            cur = predecessor[cur]
        cycle.append(x)
        cycle.reverse()
        return cycle

    def cycle_profit_multiplier(self, cycle: list[str]) -> float:
        """Product of rates around the cycle — > 1 means a gross-profitable
        loop before fees. `cycle` is a closed path (first == last)."""
        product = 1.0
        for u, v in pairwise(cycle):
            rate = self._edges.get(u, {}).get(v)
            if rate is None:
                raise ValueError(f"no edge {u}->{v} in graph")
            product *= rate
        return product
