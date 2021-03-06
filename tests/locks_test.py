"""Tests for lock.py"""

import time
import unittest
import unittest.mock

from tulip import events
from tulip import futures
from tulip import locks
from tulip import tasks
from tulip.test_utils import run_briefly


class LockTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(None)

    def tearDown(self):
        self.loop.close()

    def test_ctor_loop(self):
        loop = unittest.mock.Mock()
        lock = locks.Lock(loop=loop)
        self.assertIs(lock._loop, loop)

        lock = locks.Lock(loop=self.loop)
        self.assertIs(lock._loop, self.loop)

    def test_ctor_noloop(self):
        try:
            events.set_event_loop(self.loop)
            lock = locks.Lock()
            self.assertIs(lock._loop, self.loop)
        finally:
            events.set_event_loop(None)

    def test_repr(self):
        lock = locks.Lock(loop=self.loop)
        self.assertTrue(repr(lock).endswith('[unlocked]>'))

        @tasks.coroutine
        def acquire_lock():
            yield from lock

        self.loop.run_until_complete(acquire_lock())
        self.assertTrue(repr(lock).endswith('[locked]>'))

    def test_lock(self):
        lock = locks.Lock(loop=self.loop)

        @tasks.coroutine
        def acquire_lock():
            return (yield from lock)

        res = self.loop.run_until_complete(acquire_lock())

        self.assertTrue(res)
        self.assertTrue(lock.locked())

        lock.release()
        self.assertFalse(lock.locked())

    def test_acquire(self):
        lock = locks.Lock(loop=self.loop)
        result = []

        self.assertTrue(self.loop.run_until_complete(lock.acquire()))

        @tasks.coroutine
        def c1(result):
            if (yield from lock.acquire()):
                result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            if (yield from lock.acquire()):
                result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            if (yield from lock.acquire()):
                result.append(3)
            return True

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)

        lock.release()
        run_briefly(self.loop)
        self.assertEqual([1], result)

        run_briefly(self.loop)
        self.assertEqual([1], result)

        t3 = tasks.Task(c3(result), loop=self.loop)

        lock.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2], result)

        lock.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2, 3], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_acquire_timeout(self):
        lock = locks.Lock(loop=self.loop)
        self.assertTrue(self.loop.run_until_complete(lock.acquire()))

        t0 = time.monotonic()
        acquired = self.loop.run_until_complete(lock.acquire(timeout=0.1))
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        lock = locks.Lock(loop=self.loop)
        self.loop.run_until_complete(lock.acquire())

        self.loop.call_later(0.01, lock.release)
        acquired = self.loop.run_until_complete(lock.acquire(10.1))
        self.assertTrue(acquired)

    def test_acquire_timeout_mixed(self):
        lock = locks.Lock(loop=self.loop)
        self.loop.run_until_complete(lock.acquire())
        tasks.Task(lock.acquire(), loop=self.loop)
        tasks.Task(lock.acquire(), loop=self.loop)
        acquire_task = tasks.Task(lock.acquire(0.01), loop=self.loop)
        tasks.Task(lock.acquire(), loop=self.loop)

        acquired = self.loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        self.assertEqual(3, len(lock._waiters))

    def test_acquire_cancel(self):
        lock = locks.Lock(loop=self.loop)
        self.assertTrue(self.loop.run_until_complete(lock.acquire()))

        task = tasks.Task(lock.acquire(), loop=self.loop)
        self.loop.call_soon(task.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.loop.run_until_complete, task)
        self.assertFalse(lock._waiters)

    def test_release_not_acquired(self):
        lock = locks.Lock(loop=self.loop)

        self.assertRaises(RuntimeError, lock.release)

    def test_release_no_waiters(self):
        lock = locks.Lock(loop=self.loop)
        self.loop.run_until_complete(lock.acquire())
        self.assertTrue(lock.locked())

        lock.release()
        self.assertFalse(lock.locked())

    def test_context_manager(self):
        lock = locks.Lock(loop=self.loop)

        @tasks.coroutine
        def acquire_lock():
            return (yield from lock)

        with self.loop.run_until_complete(acquire_lock()):
            self.assertTrue(lock.locked())

        self.assertFalse(lock.locked())

    def test_context_manager_no_yield(self):
        lock = locks.Lock(loop=self.loop)

        try:
            with lock:
                self.fail('RuntimeError is not raised in with expression')
        except RuntimeError as err:
            self.assertEqual(
                str(err),
                '"yield from" should be used as context manager expression')


class EventWaiterTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(None)

    def tearDown(self):
        self.loop.close()

    def test_ctor_loop(self):
        loop = unittest.mock.Mock()
        ev = locks.EventWaiter(loop=loop)
        self.assertIs(ev._loop, loop)

        ev = locks.EventWaiter(loop=self.loop)
        self.assertIs(ev._loop, self.loop)

    def test_ctor_noloop(self):
        try:
            events.set_event_loop(self.loop)
            ev = locks.EventWaiter()
            self.assertIs(ev._loop, self.loop)
        finally:
            events.set_event_loop(None)

    def test_repr(self):
        ev = locks.EventWaiter(loop=self.loop)
        self.assertTrue(repr(ev).endswith('[unset]>'))

        ev.set()
        self.assertTrue(repr(ev).endswith('[set]>'))

    def test_wait(self):
        ev = locks.EventWaiter(loop=self.loop)
        self.assertFalse(ev.is_set())

        result = []

        @tasks.coroutine
        def c1(result):
            if (yield from ev.wait()):
                result.append(1)

        @tasks.coroutine
        def c2(result):
            if (yield from ev.wait()):
                result.append(2)

        @tasks.coroutine
        def c3(result):
            if (yield from ev.wait()):
                result.append(3)

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)

        t3 = tasks.Task(c3(result), loop=self.loop)

        ev.set()
        run_briefly(self.loop)
        self.assertEqual([3, 1, 2], result)

        self.assertTrue(t1.done())
        self.assertIsNone(t1.result())
        self.assertTrue(t2.done())
        self.assertIsNone(t2.result())
        self.assertTrue(t3.done())
        self.assertIsNone(t3.result())

    def test_wait_on_set(self):
        ev = locks.EventWaiter(loop=self.loop)
        ev.set()

        res = self.loop.run_until_complete(ev.wait())
        self.assertTrue(res)

    def test_wait_timeout(self):
        ev = locks.EventWaiter(loop=self.loop)

        t0 = time.monotonic()
        res = self.loop.run_until_complete(ev.wait(0.1))
        self.assertFalse(res)
        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        ev = locks.EventWaiter(loop=self.loop)
        self.loop.call_later(0.01, ev.set)
        acquired = self.loop.run_until_complete(ev.wait(10.1))
        self.assertTrue(acquired)

    def test_wait_timeout_mixed(self):
        ev = locks.EventWaiter(loop=self.loop)
        tasks.Task(ev.wait(), loop=self.loop)
        tasks.Task(ev.wait(), loop=self.loop)
        acquire_task = tasks.Task(ev.wait(0.1), loop=self.loop)
        tasks.Task(ev.wait(), loop=self.loop)

        t0 = time.monotonic()
        acquired = self.loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        self.assertEqual(3, len(ev._waiters))

    def test_wait_cancel(self):
        ev = locks.EventWaiter(loop=self.loop)

        wait = tasks.Task(ev.wait(), loop=self.loop)
        self.loop.call_soon(wait.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.loop.run_until_complete, wait)
        self.assertFalse(ev._waiters)

    def test_clear(self):
        ev = locks.EventWaiter(loop=self.loop)
        self.assertFalse(ev.is_set())

        ev.set()
        self.assertTrue(ev.is_set())

        ev.clear()
        self.assertFalse(ev.is_set())

    def test_clear_with_waiters(self):
        ev = locks.EventWaiter(loop=self.loop)
        result = []

        @tasks.coroutine
        def c1(result):
            if (yield from ev.wait()):
                result.append(1)
            return True

        t = tasks.Task(c1(result), loop=self.loop)
        run_briefly(self.loop)
        self.assertEqual([], result)

        ev.set()
        ev.clear()
        self.assertFalse(ev.is_set())

        ev.set()
        ev.set()
        self.assertEqual(1, len(ev._waiters))

        run_briefly(self.loop)
        self.assertEqual([1], result)
        self.assertEqual(0, len(ev._waiters))

        self.assertTrue(t.done())
        self.assertTrue(t.result())


class ConditionTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(None)

    def tearDown(self):
        self.loop.close()

    def test_ctor_loop(self):
        loop = unittest.mock.Mock()
        cond = locks.Condition(loop=loop)
        self.assertIs(cond._loop, loop)

        cond = locks.Condition(loop=self.loop)
        self.assertIs(cond._loop, self.loop)

    def test_ctor_noloop(self):
        try:
            events.set_event_loop(self.loop)
            cond = locks.Condition()
            self.assertIs(cond._loop, self.loop)
        finally:
            events.set_event_loop(None)

    def test_wait(self):
        cond = locks.Condition(loop=self.loop)
        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(3)
            return True

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)
        t3 = tasks.Task(c3(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)
        self.assertFalse(cond.locked())

        self.assertTrue(self.loop.run_until_complete(cond.acquire()))
        cond.notify()
        run_briefly(self.loop)
        self.assertEqual([], result)
        self.assertTrue(cond.locked())

        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1], result)
        self.assertTrue(cond.locked())

        cond.notify(2)
        run_briefly(self.loop)
        self.assertEqual([1], result)
        self.assertTrue(cond.locked())

        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2], result)
        self.assertTrue(cond.locked())

        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2, 3], result)
        self.assertTrue(cond.locked())

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_wait_timeout(self):
        cond = locks.Condition(loop=self.loop)
        self.loop.run_until_complete(cond.acquire())

        t0 = time.monotonic()
        wait = self.loop.run_until_complete(cond.wait(0.1))
        self.assertFalse(wait)
        self.assertTrue(cond.locked())

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

    def test_wait_cancel(self):
        cond = locks.Condition(loop=self.loop)
        self.loop.run_until_complete(cond.acquire())

        wait = tasks.Task(cond.wait(), loop=self.loop)
        self.loop.call_soon(wait.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.loop.run_until_complete, wait)
        self.assertFalse(cond._condition_waiters)
        self.assertTrue(cond.locked())

    def test_wait_unacquired(self):
        cond = locks.Condition(loop=self.loop)
        self.assertRaises(
            RuntimeError,
            self.loop.run_until_complete, cond.wait())

    def test_wait_for(self):
        cond = locks.Condition(loop=self.loop)
        presult = False

        def predicate():
            return presult

        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait_for(predicate)):
                result.append(1)
                cond.release()
            return True

        t = tasks.Task(c1(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)

        self.loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([], result)

        presult = True
        self.loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1], result)

        self.assertTrue(t.done())
        self.assertTrue(t.result())

    def test_wait_for_timeout(self):
        cond = locks.Condition(loop=self.loop)

        result = []

        predicate = unittest.mock.Mock()
        predicate.return_value = False

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait_for(predicate, 0.1)):
                result.append(1)
            else:
                result.append(2)
            cond.release()

        wait_for = tasks.Task(c1(result), loop=self.loop)

        t0 = time.monotonic()

        run_briefly(self.loop)
        self.assertEqual([], result)

        self.loop.run_until_complete(cond.acquire())
        cond.notify()
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([], result)

        self.loop.run_until_complete(wait_for)
        self.assertEqual([2], result)
        self.assertEqual(3, predicate.call_count)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

    def test_wait_for_unacquired(self):
        cond = locks.Condition(loop=self.loop)

        # predicate can return true immediately
        res = self.loop.run_until_complete(cond.wait_for(lambda: [1, 2, 3]))
        self.assertEqual([1, 2, 3], res)

        self.assertRaises(
            RuntimeError,
            self.loop.run_until_complete,
            cond.wait_for(lambda: False))

    def test_notify(self):
        cond = locks.Condition(loop=self.loop)
        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
                cond.release()
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
                cond.release()
            return True

        @tasks.coroutine
        def c3(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(3)
                cond.release()
            return True

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)
        t3 = tasks.Task(c3(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)

        self.loop.run_until_complete(cond.acquire())
        cond.notify(1)
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1], result)

        self.loop.run_until_complete(cond.acquire())
        cond.notify(1)
        cond.notify(2048)
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2, 3], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())

    def test_notify_all(self):
        cond = locks.Condition(loop=self.loop)

        result = []

        @tasks.coroutine
        def c1(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(1)
                cond.release()
            return True

        @tasks.coroutine
        def c2(result):
            yield from cond.acquire()
            if (yield from cond.wait()):
                result.append(2)
                cond.release()
            return True

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([], result)

        self.loop.run_until_complete(cond.acquire())
        cond.notify_all()
        cond.release()
        run_briefly(self.loop)
        self.assertEqual([1, 2], result)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())

    def test_notify_unacquired(self):
        cond = locks.Condition(loop=self.loop)
        self.assertRaises(RuntimeError, cond.notify)

    def test_notify_all_unacquired(self):
        cond = locks.Condition(loop=self.loop)
        self.assertRaises(RuntimeError, cond.notify_all)


class SemaphoreTests(unittest.TestCase):

    def setUp(self):
        self.loop = events.new_event_loop()
        events.set_event_loop(None)

    def tearDown(self):
        self.loop.close()

    def test_ctor_loop(self):
        loop = unittest.mock.Mock()
        sem = locks.Semaphore(loop=loop)
        self.assertIs(sem._loop, loop)

        sem = locks.Semaphore(loop=self.loop)
        self.assertIs(sem._loop, self.loop)

    def test_ctor_noloop(self):
        try:
            events.set_event_loop(self.loop)
            sem = locks.Semaphore()
            self.assertIs(sem._loop, self.loop)
        finally:
            events.set_event_loop(None)

    def test_repr(self):
        sem = locks.Semaphore(loop=self.loop)
        self.assertTrue(repr(sem).endswith('[unlocked,value:1]>'))

        self.loop.run_until_complete(sem.acquire())
        self.assertTrue(repr(sem).endswith('[locked]>'))

    def test_semaphore(self):
        sem = locks.Semaphore(loop=self.loop)
        self.assertEqual(1, sem._value)

        @tasks.coroutine
        def acquire_lock():
            return (yield from sem)

        res = self.loop.run_until_complete(acquire_lock())

        self.assertTrue(res)
        self.assertTrue(sem.locked())
        self.assertEqual(0, sem._value)

        sem.release()
        self.assertFalse(sem.locked())
        self.assertEqual(1, sem._value)

    def test_semaphore_value(self):
        self.assertRaises(ValueError, locks.Semaphore, -1)

    def test_acquire(self):
        sem = locks.Semaphore(3, loop=self.loop)
        result = []

        self.assertTrue(self.loop.run_until_complete(sem.acquire()))
        self.assertTrue(self.loop.run_until_complete(sem.acquire()))
        self.assertFalse(sem.locked())

        @tasks.coroutine
        def c1(result):
            yield from sem.acquire()
            result.append(1)
            return True

        @tasks.coroutine
        def c2(result):
            yield from sem.acquire()
            result.append(2)
            return True

        @tasks.coroutine
        def c3(result):
            yield from sem.acquire()
            result.append(3)
            return True

        @tasks.coroutine
        def c4(result):
            yield from sem.acquire()
            result.append(4)
            return True

        t1 = tasks.Task(c1(result), loop=self.loop)
        t2 = tasks.Task(c2(result), loop=self.loop)
        t3 = tasks.Task(c3(result), loop=self.loop)

        run_briefly(self.loop)
        self.assertEqual([1], result)
        self.assertTrue(sem.locked())
        self.assertEqual(2, len(sem._waiters))
        self.assertEqual(0, sem._value)

        t4 = tasks.Task(c4(result), loop=self.loop)

        sem.release()
        sem.release()
        self.assertEqual(2, sem._value)

        run_briefly(self.loop)
        self.assertEqual(0, sem._value)
        self.assertEqual([1, 2, 3], result)
        self.assertTrue(sem.locked())
        self.assertEqual(1, len(sem._waiters))
        self.assertEqual(0, sem._value)

        self.assertTrue(t1.done())
        self.assertTrue(t1.result())
        self.assertTrue(t2.done())
        self.assertTrue(t2.result())
        self.assertTrue(t3.done())
        self.assertTrue(t3.result())
        self.assertFalse(t4.done())

    def test_acquire_timeout(self):
        sem = locks.Semaphore(loop=self.loop)
        self.loop.run_until_complete(sem.acquire())

        t0 = time.monotonic()
        acquired = self.loop.run_until_complete(sem.acquire(0.1))
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        sem = locks.Semaphore(loop=self.loop)
        self.loop.run_until_complete(sem.acquire())

        self.loop.call_later(0.01, sem.release)
        acquired = self.loop.run_until_complete(sem.acquire(10.1))
        self.assertTrue(acquired)

    def test_acquire_timeout_mixed(self):
        sem = locks.Semaphore(loop=self.loop)
        self.loop.run_until_complete(sem.acquire())
        tasks.Task(sem.acquire(), loop=self.loop)
        tasks.Task(sem.acquire(), loop=self.loop)
        acquire_task = tasks.Task(sem.acquire(0.1), loop=self.loop)
        tasks.Task(sem.acquire(), loop=self.loop)

        t0 = time.monotonic()
        acquired = self.loop.run_until_complete(acquire_task)
        self.assertFalse(acquired)

        total_time = (time.monotonic() - t0)
        self.assertTrue(0.08 < total_time < 0.12)

        self.assertEqual(3, len(sem._waiters))

    def test_acquire_cancel(self):
        sem = locks.Semaphore(loop=self.loop)
        self.loop.run_until_complete(sem.acquire())

        acquire = tasks.Task(sem.acquire(), loop=self.loop)
        self.loop.call_soon(acquire.cancel)
        self.assertRaises(
            futures.CancelledError,
            self.loop.run_until_complete, acquire)
        self.assertFalse(sem._waiters)

    def test_release_not_acquired(self):
        sem = locks.Semaphore(bound=True, loop=self.loop)

        self.assertRaises(ValueError, sem.release)

    def test_release_no_waiters(self):
        sem = locks.Semaphore(loop=self.loop)
        self.loop.run_until_complete(sem.acquire())
        self.assertTrue(sem.locked())

        sem.release()
        self.assertFalse(sem.locked())

    def test_context_manager(self):
        sem = locks.Semaphore(2, loop=self.loop)

        @tasks.coroutine
        def acquire_lock():
            return (yield from sem)

        with self.loop.run_until_complete(acquire_lock()):
            self.assertFalse(sem.locked())
            self.assertEqual(1, sem._value)

            with self.loop.run_until_complete(acquire_lock()):
                self.assertTrue(sem.locked())

        self.assertEqual(2, sem._value)


if __name__ == '__main__':
    unittest.main()
