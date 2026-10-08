# DSA concepts used in the running project

These are application code implementations, not claims about hidden library
internals. Run the backend and open the frontend to exercise them.

| Evaluation concept | Actual implementation | Application use |
| --- | --- | --- |
| Classes | `Archive`, `RecentEventList`, `EventNode`, `Handler` | Database abstraction, linked event cache, HTTP handling |
| Inheritance | `Handler(BaseHTTPRequestHandler)` | Extends HTTP handling with application GET/POST methods |
| Recursion | `recursive_merge_sort()` | Orders the combined event/note report timeline and recent events |
| Linked lists | `RecentEventList` with `EventNode.next` | Keeps the latest 200 device events for the dashboard |

## Linked list, piece by piece

Implementation: `backend/data_structures.py`.

1. `EventNode` stores one event dictionary and a `next` reference.
2. `RecentEventList.head` refers to the oldest node; `tail` to the newest.
3. `append()` connects the previous tail to a new node using `tail.next`.
4. When capacity exceeds 200, `pop_oldest()` advances `head` to `head.next`.
5. `snapshot()` walks from head through each `next` reference and builds detached
   dictionaries for JSON output. Serialization requires an array; the internal
   cache itself is an actual singly linked list.

Example with capacity 3:

```text
append A, B, C: head -> A -> B -> C -> None (tail = C)
append D:      head -> B -> C -> D -> None (tail = D)
```

Append and eviction: O(1) each. Snapshot traversal: O(n). Cache memory: O(capacity).
The backend locks access because serial collection and HTTP requests run in
different threads. A new event is committed to SQLite before it enters the cache.
On restart, the most recently inserted 200 events are restored from SQLite.
Older events remain in SQLite and are still available in historical reports.
Newest insertion IDs determine cache retention; timestamps determine display order.

The cache is intended for a single backend process. Another process or manual SQL
write to the database will not update this process's cache until restart.

Application path:

```text
serial status/connection event
 -> Archive.event()
 -> SQLite commit
 -> RecentEventList.append()
 -> GET /api/recent-events
 -> snapshot + recursive sort
 -> frontend Recent device activity
```

## Recursion, piece by piece

`recursive_merge_sort(items, key, reverse)` implements stable merge sort.

1. Base case: if the sequence contains zero or one item, return a copy.
2. Split the sequence at its midpoint.
3. Call the same function on the smaller left half.
4. Call the same function on the smaller right half.
5. Merge the sorted halves with two indices. On equal keys, select the left item
   first to preserve stable order.

Example timestamp input:

```text
[30, 10, 40, 20]
       split
[30, 10]       [40, 20]
 split          split
[30] [10]      [40] [20]    <- base cases
       merge
[10, 30]       [20, 40]
       merge
[10, 20, 30, 40]
```

The application uses descending timestamps (`reverse=True`) so newest records
appear first. It combines database events and maintenance notes from separate
tables before sorting; neither table alone provides the merged timeline order.
This function is called by `Archive.window()` and the result is displayed in
the frontend's Report activity timeline and included when printing the report.
It also sorts linked-list snapshots for the recent-event endpoint.

Time: O(n log n). Auxiliary memory: O(n). Recursion stack depth: O(log n).
Historical timeline inputs are bounded to the newest 200 records from each
table in the selected period. Recent device activity is bounded to 200 events.
Thus reports show at most 400 timeline entries, not every event in a large month.

## Evaluation demonstration

Run:

```powershell
cd backend
python -m unittest discover -v
python server.py --demo
```

Open http://127.0.0.1:8000. The demo records a device event on first initialization.
Save a maintenance note. The Report activity timeline combines both categories
in descending timestamp order using recursion. Recent device activity reads
from the linked-list cache. Stop and restart to demonstrate cache restoration.
To demonstrate insertion/eviction and recursive sorting edge cases, show the
unit tests; do not flood the real hardware with commands.

Existing firmware uses an array of `Load` structures and fixed priority-based
threshold conditions. It does not implement a heap/priority queue. Arduino code
and physical relay-control behavior were not changed for these DSA additions.
