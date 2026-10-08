"""Explicit DSA implementations used by the microgrid backend."""
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class EventNode:
    """One singly linked node: payload + reference to the following node."""

    event: dict
    next: Optional['EventNode'] = None


class RecentEventList:
    """Bounded FIFO of recent events. Callers synchronize concurrent access.

    SQLite remains the permanent store; this cache retains only recent events.
    Appending and evicting the oldest event take O(1) time each.
    """

    def __init__(self, capacity=200):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError('capacity must be a positive integer')
        self.capacity = capacity
        self.head = None
        self.tail = None
        self.size = 0

    def append(self, event):
        node = EventNode(dict(event))
        if self.tail is None:
            self.head = node
        else:
            self.tail.next = node
        self.tail = node
        self.size += 1
        if self.size > self.capacity:
            self.pop_oldest()

    def pop_oldest(self):
        if self.head is None:
            return None
        node = self.head
        self.head = node.next
        node.next = None
        self.size -= 1
        if self.head is None:
            self.tail = None
        return dict(node.event)

    def snapshot(self):
        """Traverse links, returning detached records for JSON serialization."""
        events = []
        current = self.head
        while current is not None:
            events.append(dict(current.event))
            current = current.next
        return events


def recursive_merge_sort(items, key: Callable[[Any], Any], reverse=False):
    """Stable divide-and-conquer sorting; no built-in sorted()/list.sort().

    Base case: zero or one item. Recursive case: sort both smaller halves,
    then merge them. O(n log n) time, O(n) auxiliary space and O(log n)
    recursion depth. Does not mutate the input sequence.
    """
    if len(items) <= 1:
        return list(items)
    midpoint = len(items) // 2
    left = recursive_merge_sort(items[:midpoint], key, reverse)
    right = recursive_merge_sort(items[midpoint:], key, reverse)
    merged = []
    i = j = 0
    while i < len(left) and j < len(right):
        # Choosing left for ties preserves the original relative order.
        take_left = key(left[i]) >= key(right[j]) if reverse else key(left[i]) <= key(right[j])
        if take_left:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged
