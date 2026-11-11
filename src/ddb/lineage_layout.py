# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — lineage_layout                                            ║
# ║  « columns by generation, parents beside children »              ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Layered layout for a lineage DAG: longest-path layers,          ║
# ║  barycentre ordering sweeps, then vertical slots that centre     ║
# ║  each parent on its children without overlapping cards.          ║
# ║  Pure Python, no Qt.                                             ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Place lineage nodes on a grid of layers (columns) and slots (rows).

A simplified Sugiyama layout, sized for vial families (tens of nodes):

1. **Layers**: a node sits one layer right of its deepest parent, so
   every arrow points left → right.
2. **Ordering**: alternating barycentre sweeps sort each layer by the
   average position of its neighbours, which removes most crossings.
3. **Slots**: each node moves towards the mean slot of its neighbours,
   keeping at least ``MIN_GAP`` between nodes of the same layer, so
   parents end up centred on their children.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from statistics import fmean

MIN_GAP = 1.0
SWEEPS = 4


@dataclass(frozen=True)
class Placement:
    """Grid position: ``layer`` is the column, ``slot`` the row (>= 0)."""

    layer: int
    slot: float


def layout_layers(
    node_ids: Iterable[int],
    edges: Iterable[tuple[int, int]],
    order_key: Callable[[int], Hashable] | None = None,
) -> dict[int, Placement]:
    """Lay out a DAG given as node ids and ``(parent, child)`` edges.

    Args:
        node_ids:  Nodes to place. Edges touching other ids are ignored.
        edges:     ``(parent, child)`` pairs.
        order_key: Initial within-layer order (default: node id).

    Returns:
        Placement for every node.
    """
    nodes = list(dict.fromkeys(node_ids))
    parents: dict[int, list[int]] = {n: [] for n in nodes}
    children: dict[int, list[int]] = {n: [] for n in nodes}
    for parent, child in edges:
        if parent in parents and child in parents and parent != child:
            children[parent].append(child)
            parents[child].append(parent)

    layer_of = _assign_layers(nodes, parents, children)
    key = order_key or (lambda n: n)
    depth = max(layer_of.values(), default=-1) + 1
    layers = [sorted((n for n in nodes if layer_of[n] == i), key=key) for i in range(depth)]
    _order_layers(layers, parents, children)
    slots = _assign_slots(layers, parents, children)
    return {n: Placement(layer=layer_of[n], slot=slots[n]) for n in nodes}


# ┌────────────────────────────────────────────────────────────┐
# │ Layers  « longest path from the roots »                    │
# └────────────────────────────────────────────────────────────┘


def _assign_layers(
    nodes: list[int], parents: dict[int, list[int]], children: dict[int, list[int]]
) -> dict[int, int]:
    """Kahn's topological order; nodes stuck in a cycle fall back to layer 0."""
    layer_of = dict.fromkeys(nodes, 0)
    waiting = {n: len(parents[n]) for n in nodes}
    ready = [n for n in nodes if waiting[n] == 0]
    while ready:
        node = ready.pop()
        for child in children[node]:
            layer_of[child] = max(layer_of[child], layer_of[node] + 1)
            waiting[child] -= 1
            if waiting[child] == 0:
                ready.append(child)
    return layer_of


# ┌────────────────────────────────────────────────────────────┐
# │ Ordering  « barycentre sweeps reduce crossings »           │
# └────────────────────────────────────────────────────────────┘


def _order_layers(
    layers: list[list[int]], parents: dict[int, list[int]], children: dict[int, list[int]]
) -> None:
    for _ in range(SWEEPS):
        for i in range(1, len(layers)):
            layers[i] = _by_barycentre(layers[i], layers[i - 1], parents)
        for i in range(len(layers) - 2, -1, -1):
            layers[i] = _by_barycentre(layers[i], layers[i + 1], children)


def _by_barycentre(
    layer: list[int], neighbour_layer: list[int], links: dict[int, list[int]]
) -> list[int]:
    index = {n: i for i, n in enumerate(neighbour_layer)}

    def centre(position: int, node: int) -> tuple[float, int]:
        around = [index[m] for m in links[node] if m in index]
        return (fmean(around) if around else float(position), position)

    keyed = [(centre(pos, node), node) for pos, node in enumerate(layer)]
    return [node for _, node in sorted(keyed)]


# ┌────────────────────────────────────────────────────────────┐
# │ Slots  « align with neighbours, keep a minimum gap »       │
# └────────────────────────────────────────────────────────────┘


def _assign_slots(
    layers: list[list[int]], parents: dict[int, list[int]], children: dict[int, list[int]]
) -> dict[int, float]:
    slot = {n: float(i) for layer in layers for i, n in enumerate(layer)}
    for _ in range(SWEEPS):
        for layer in layers[1:]:
            _pull_towards(layer, parents, slot)
        for layer in reversed(layers[:-1]):
            _pull_towards(layer, children, slot)
    lowest = min(slot.values(), default=0.0)
    return {n: s - lowest for n, s in slot.items()}


def _pull_towards(layer: list[int], links: dict[int, list[int]], slot: dict[int, float]) -> None:
    """Move nodes to their neighbours' mean slot, then restore spacing.

    Spacing is enforced top-down, which only ever pushes nodes down; the
    whole layer is then shifted back by the average push so it stays
    centred on where its neighbours want it.
    """
    desired = [fmean(slot[m] for m in links[n]) if links[n] else slot[n] for n in layer]
    placed: list[float] = []
    for want in desired:
        placed.append(want if not placed else max(want, placed[-1] + MIN_GAP))
    shift = fmean(w - p for w, p in zip(desired, placed, strict=True)) if placed else 0.0
    for node, position in zip(layer, placed, strict=True):
        slot[node] = position + shift
