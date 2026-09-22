"""Bounded BFS over the solar-system stargate adjacency graph."""

from __future__ import annotations

from collections import deque


def bfs_within(graph: dict[int, set[int]], source: int, max_hops: int) -> dict[int, int]:
    """Jump-distance from `source` to every system reachable within `max_hops`.

    Includes `source` itself at distance 0. Stops expanding once `max_hops` is reached.
    """
    distances: dict[int, int] = {source: 0}
    queue: deque[int] = deque([source])

    while queue:
        current = queue.popleft()
        current_distance = distances[current]
        if current_distance == max_hops:
            continue
        for neighbor in graph.get(current, ()):
            if neighbor not in distances:
                distances[neighbor] = current_distance + 1
                queue.append(neighbor)

    return distances
