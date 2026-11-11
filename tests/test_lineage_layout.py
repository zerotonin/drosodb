"""Layered layout used by the genealogy chart (pure, no Qt)."""

from __future__ import annotations

from ddb.lineage_layout import MIN_GAP, layout_layers


def _assert_no_overlap(placements: dict) -> None:
    by_layer: dict[int, list[float]] = {}
    for p in placements.values():
        by_layer.setdefault(p.layer, []).append(p.slot)
    for slots in by_layer.values():
        slots.sort()
        assert all(b - a >= MIN_GAP - 1e-9 for a, b in zip(slots, slots[1:], strict=False))


def test_empty_graph() -> None:
    assert layout_layers([], []) == {}


def test_chain_is_one_straight_row() -> None:
    placements = layout_layers([1, 2, 3, 4], [(1, 2), (2, 3), (3, 4)])
    assert [placements[n].layer for n in (1, 2, 3, 4)] == [0, 1, 2, 3]
    assert {placements[n].slot for n in placements} == {0.0}


def test_parent_is_centred_on_its_children() -> None:
    placements = layout_layers([1, 2, 3, 4], [(1, 2), (1, 3), (1, 4)])
    child_slots = [placements[c].slot for c in (2, 3, 4)]
    assert placements[1].slot == sum(child_slots) / 3
    _assert_no_overlap(placements)
    assert min(p.slot for p in placements.values()) == 0.0


def test_arrows_always_point_right() -> None:
    edges = [(1, 2), (2, 3), (1, 4), (4, 5), (3, 6), (5, 6)]
    placements = layout_layers(range(1, 7), edges)
    assert all(placements[p].layer < placements[c].layer for p, c in edges)
    assert placements[6].layer == 3  # longest path wins for the cross child


def test_two_families_do_not_cross() -> None:
    # 1 → (3, 4) and 2 → (5, 6): children should stay grouped under parents.
    placements = layout_layers([1, 2, 3, 4, 5, 6], [(1, 3), (1, 4), (2, 5), (2, 6)])
    top_family = max(placements[3].slot, placements[4].slot)
    bottom_family = min(placements[5].slot, placements[6].slot)
    assert (top_family < bottom_family) == (placements[1].slot < placements[2].slot)
    _assert_no_overlap(placements)


def test_edges_to_unknown_nodes_and_self_loops_are_ignored() -> None:
    placements = layout_layers([1, 2], [(1, 2), (2, 99), (1, 1)])
    assert placements[2].layer == 1
    assert set(placements) == {1, 2}


def test_cycle_does_not_hang() -> None:
    placements = layout_layers([1, 2, 3], [(1, 2), (2, 3), (3, 1)])
    assert set(placements) == {1, 2, 3}
