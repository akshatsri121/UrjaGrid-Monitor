import random
import unittest
from data_structures import RecentEventList, recursive_merge_sort


class LinkedListTests(unittest.TestCase):
    def test_append_links_and_capacity(self):
        events = RecentEventList(2)
        events.append({'id':1})
        events.append({'id':2})
        self.assertIs(events.head.next, events.tail)
        self.assertIsNone(events.tail.next)
        events.append({'id':3})
        self.assertEqual(events.size, 2)
        self.assertEqual(events.snapshot(), [{'id':2}, {'id':3}])
        self.assertEqual(events.head.event['id'], 2)

    def test_empty_single_and_reuse(self):
        events = RecentEventList(1)
        self.assertIsNone(events.pop_oldest())
        events.append({'id':1})
        self.assertEqual(events.pop_oldest(), {'id':1})
        self.assertIsNone(events.head)
        self.assertIsNone(events.tail)
        self.assertEqual(events.size,0)
        events.append({'id':2})
        events.append({'id':3})
        self.assertEqual(events.snapshot(),[{'id':3}])

    def test_detached_payload_and_validation(self):
        events = RecentEventList()
        original = {'id':1}
        events.append(original)
        original['id']=99
        snapshot = events.snapshot()
        snapshot[0]['id']=100
        self.assertEqual(events.snapshot(),[{'id':1}])
        for capacity in [0,-1,True,1.5]:
            with self.assertRaises(ValueError):
                RecentEventList(capacity)


class MergeSortTests(unittest.TestCase):
    def test_empty_single_sorted_and_random(self):
        for items in [[],[1],list(range(20)),list(reversed(range(20)))]:
            self.assertEqual(recursive_merge_sort(items,key=lambda x:x),sorted(items))
        rng=random.Random(42)
        items=[rng.randrange(100) for _ in range(1000)]
        before=list(items)
        self.assertEqual(recursive_merge_sort(items,key=lambda x:x),sorted(items))
        self.assertEqual(recursive_merge_sort(items,key=lambda x:x,reverse=True),sorted(items,reverse=True))
        self.assertEqual(items,before)

    def test_stable_ties_both_directions(self):
        items=[{'ts':2,'name':'a'},{'ts':1,'name':'b'},{'ts':2,'name':'c'}]
        self.assertEqual([r['name'] for r in recursive_merge_sort(items,key=lambda r:r['ts'])],['b','a','c'])
        self.assertEqual([r['name'] for r in recursive_merge_sort(items,key=lambda r:r['ts'],reverse=True)],['a','c','b'])

if __name__=='__main__':
    unittest.main()
