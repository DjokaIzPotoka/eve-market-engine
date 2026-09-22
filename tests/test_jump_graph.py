"""Tests for the bounded BFS over the solar-system adjacency graph."""

from __future__ import annotations

from eve.features.jump_graph import bfs_within


def test_bfs_within_includes_source_at_zero() -> None:
    graph = {1: {2}, 2: {1}}

    distances = bfs_within(graph, source=1, max_hops=5)

    assert distances[1] == 0


def test_bfs_within_finds_shortest_distance_along_a_chain() -> None:
    # 1 - 2 - 3 - 4 - 5
    graph = {1: {2}, 2: {1, 3}, 3: {2, 4}, 4: {3, 5}, 5: {4}}

    distances = bfs_within(graph, source=1, max_hops=5)

    assert distances == {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}


def test_bfs_within_stops_at_max_hops() -> None:
    graph = {1: {2}, 2: {1, 3}, 3: {2, 4}, 4: {3, 5}, 5: {4}}

    distances = bfs_within(graph, source=1, max_hops=2)

    assert distances == {1: 0, 2: 1, 3: 2}
    assert 4 not in distances
    assert 5 not in distances


def test_bfs_within_branches_takes_shortest_path() -> None:
    # 1 connects to 2 and 3; 3 also connects to 2 via a longer path -- 2 must stay at distance 1
    graph = {1: {2, 3}, 2: {1, 3}, 3: {1, 2}}

    distances = bfs_within(graph, source=1, max_hops=5)

    assert distances == {1: 0, 2: 1, 3: 1}


def test_bfs_within_unreachable_system_is_absent() -> None:
    graph = {1: {2}, 2: {1}, 99: set()}

    distances = bfs_within(graph, source=1, max_hops=5)

    assert 99 not in distances
