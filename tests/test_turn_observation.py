"""Exercise submission and observation together; never contact a live backend."""
import datetime as dt
from unittest import TestCase
from unittest.mock import Mock, patch

import trigger
from turn_result import SendMissed, TurnError


class Clock:
    scheduled = dt.datetime(2026, 11, 10, 19, 30, tzinfo=trigger.TZ)

    def __init__(self, seconds=13):
        self.seconds = seconds

    def now(self):
        return self.scheduled + dt.timedelta(seconds=self.seconds)

    def advance(self, seconds):
        self.seconds += seconds


def notification(method, **params):
    return {'method': method, 'params': {'threadId': 'thread', **params}}


class EventRPC:
    def __init__(self, clock, acceptance_delay=3, finished_at=20, live=True):
        self.clock = clock
        self.acceptance_delay = acceptance_delay
        self.finished_at = finished_at
        self.live = live
        self.events = []
        self.notifications = []
        self.calls = []
        self.status = 'completed'
        self.history_thread = 'thread'
        self.history_turn = 'turn'
        self.user_text = 'ok'
        self.with_final = True
        self.private_error = None
        self.closed = False
        self.on_close = None
        self.read_failures = 0

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'turn/start':
            self.clock.advance(self.acceptance_delay)
            if self.live:
                self.events.append(notification('turn/started', turn={'id': 'turn'}))
                self.notifications.append((self.finished_at, notification(
                    'turn/completed', turn={'id': 'turn', 'status': self.status,
                                           'error': self.private_error})))
            return {'turn': {'id': 'turn'}}
        if method == 'thread/read':
            if self.read_failures:
                self.read_failures -= 1
                raise TimeoutError('private upstream diagnostic')
            items = [{'type': 'userMessage', 'content': [{'type': 'text', 'text': self.user_text}]}]
            if self.with_final:
                items.append({'type': 'agentMessage', 'phase': 'final_answer', 'text': 'ok'})
            return {'thread': {'id': self.history_thread, 'turns': [
                {'id': self.history_turn,
                 'status': self.status if self.clock.seconds >= self.finished_at else 'inProgress',
                 'items': items, 'error': self.private_error}]}}
        raise AssertionError(method)

    def recv(self, timeout):
        if self.notifications and self.notifications[0][0] <= self.clock.seconds + timeout:
            at, value = self.notifications.pop(0)
            self.clock.seconds = max(at, self.clock.seconds)
            return value
        self.clock.advance(timeout)
        raise TimeoutError()

    def close(self):
        if self.on_close:
            self.on_close()
        self.closed = True


class ObservationTests(TestCase):
    def setUp(self):
        self.clock = Clock()
        self.log = Mock()
        for name, value in [('now', self.clock.now), ('log', self.log)]:
            p = patch.object(trigger, name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(trigger.time, 'monotonic', lambda: self.clock.seconds)
        p.start(); self.addCleanup(p.stop)

    def run_turn(self, rpc):
        return trigger.run_turn(rpc, 'thread', 'slot', self.clock.scheduled)

    def assert_single_send(self, rpc):
        self.assertEqual(sum(m == 'turn/start' for m, _ in rpc.calls), 1)

    def test_send_at_13_seconds_start_at_16_seconds_still_completes(self):
        rpc = EventRPC(self.clock)
        proof = self.run_turn(rpc)
        self.assertEqual(proof['turn_id'], 'turn')
        self.assertEqual(proof['final_reply'], 'ok')
        self.assertTrue(proof['persisted_same_thread'])
        started = next(c.kwargs for c in self.log.call_args_list if c.args[0] == 'turn_started')
        self.assertEqual(started['delay_seconds'], 16)
        self.assertTrue(started['late'])
        self.assert_single_send(rpc)
        self.assertFalse(rpc.closed)

    def test_final_pre_submit_gate_rejects_late_or_early_send(self):
        for seconds in (-1, 15.001, 20):
            with self.subTest(seconds=seconds):
                self.clock.seconds = seconds
                rpc = EventRPC(self.clock)
                with self.assertRaises(SendMissed):
                    self.run_turn(rpc)
                self.assertEqual(rpc.calls, [])

    def test_send_at_exact_deadline_is_allowed(self):
        self.clock.seconds = 15
        rpc = EventRPC(self.clock)
        self.assertEqual(self.run_turn(rpc)['turn_id'], 'turn')
        self.assert_single_send(rpc)

    def test_missing_all_live_events_is_resolved_by_same_turn_history(self):
        rpc = EventRPC(self.clock, live=False)
        proof = self.run_turn(rpc)
        self.assertFalse(any(proof['live_events'].values()))
        self.assertTrue(proof['persisted_same_thread'])
        self.assert_single_send(rpc)

    def test_live_completion_before_history_flush_waits_for_persistence(self):
        rpc = EventRPC(self.clock, finished_at=25, live=False)
        rpc.notifications = [(17, notification('turn/completed', turn={'id': 'turn', 'status': 'completed'}))]
        self.assertEqual(self.run_turn(rpc)['turn_id'], 'turn')
        self.assertGreaterEqual(self.clock.seconds, 25)
        self.assert_single_send(rpc)

    def test_transient_history_read_failure_does_not_close_or_resend(self):
        rpc = EventRPC(self.clock, live=False)
        rpc.read_failures = 1
        self.assertEqual(self.run_turn(rpc)['turn_id'], 'turn')
        self.assertFalse(rpc.closed)
        self.assert_single_send(rpc)

    def test_live_completion_does_not_accept_another_turn_or_wrong_message(self):
        for field, value in [('history_turn', 'previous'), ('user_text', 'other'), ('with_final', False)]:
            with self.subTest(field=field):
                self.clock.seconds = 13
                rpc = EventRPC(self.clock)
                setattr(rpc, field, value)
                with self.assertRaises(TurnError) as caught:
                    self.run_turn(rpc)
                self.assertEqual(caught.exception.code, 'verification_failed')
                self.assertEqual(caught.exception.turn_id, 'turn')
                self.assert_single_send(rpc)

    def test_wrong_thread_history_is_never_accepted(self):
        rpc = EventRPC(self.clock)
        rpc.history_thread = 'other-thread'
        with self.assertRaises(TurnError) as caught:
            self.run_turn(rpc)
        self.assertEqual(caught.exception.code, 'verification_failed')
        self.assert_single_send(rpc)

    def test_failed_and_interrupted_turns_have_specific_safe_errors(self):
        for live in (True, False):
            for status, info, code in [('failed', None, 'turn_failed'),
                                       ('interrupted', None, 'turn_interrupted'),
                                       ('failed', 'usageLimitExceeded', 'quota_exhausted')]:
                with self.subTest(live=live, status=status, info=info):
                    self.clock.seconds = 13
                    rpc = EventRPC(self.clock, live=live)
                    rpc.status = status
                    rpc.private_error = {'codexErrorInfo': info, 'message': 'private token details'}
                    with self.assertRaises(TurnError) as caught:
                        self.run_turn(rpc)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn('private', str(caught.exception))
                    self.assert_single_send(rpc)

    def test_observation_timeout_preserves_accepted_turn_and_never_resends(self):
        rpc = EventRPC(self.clock, live=False, finished_at=1000)
        with self.assertRaises(TurnError) as caught:
            self.run_turn(rpc)
        self.assertEqual(caught.exception.code, 'observation_timeout')
        self.assertEqual(caught.exception.turn_id, 'turn')
        self.assertEqual(self.clock.seconds, 16 + trigger.OBSERVE_SECONDS)
        self.assert_single_send(rpc)

    def test_disconnect_final_read_can_still_prove_completion(self):
        rpc = EventRPC(self.clock, live=False)
        def disconnect(timeout):
            self.clock.seconds = 20
            raise ConnectionError()
        rpc.recv = disconnect
        self.assertEqual(self.run_turn(rpc)['turn_id'], 'turn')
        self.assert_single_send(rpc)

    def test_disconnect_without_completion_is_uncertain(self):
        rpc = EventRPC(self.clock, live=False, finished_at=1000)
        rpc.recv = Mock(side_effect=ConnectionError())
        with self.assertRaises(TurnError) as caught:
            self.run_turn(rpc)
        self.assertEqual(caught.exception.code, 'observation_disconnected')
        self.assert_single_send(rpc)
