"""Offline regression tests: no real config, email, browser, or site access."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import bms_watcher as watcher


class ConfigTests(unittest.TestCase):
    def test_explicit_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'user.env'
            path.write_text('GMAIL_ADDRESS=file@example.com\n'
                            'GMAIL_APP_PASSWORD=abcd efgh\n'
                            'WATCH_URL=https://example.com/movie\n')
            with patch.dict(os.environ, {'GMAIL_ADDRESS': 'env@example.com'}, clear=True):
                settings = watcher.load_settings(path)
            self.assertEqual(settings.gmail_address, 'env@example.com')
            self.assertEqual(settings.alert_to, 'env@example.com')
            self.assertEqual(settings.gmail_app_password, 'abcdefgh')

    def test_does_not_search_parent_or_change_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            (parent / '.env').write_text('GMAIL_ADDRESS=parent@example.com\n')
            child = parent / 'child'
            child.mkdir()
            with patch.dict(os.environ, {}, clear=True):
                settings = watcher.load_settings(child / '.env')
                self.assertEqual(settings.gmail_address, '')
                self.assertNotIn('GMAIL_ADDRESS', os.environ)

    def test_invalid_intervals_and_headless_are_rejected(self):
        for values in [{'CHECK_INTERVAL': 'nope'}, {'CHECK_INTERVAL': '-1'},
                       {'CHECK_INTERVAL': '30'}, {'HEADLESS': 'maybe'}]:
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    watcher.load_settings('/nonexistent/env')

    def test_help_does_not_load_configuration(self):
        with patch.object(watcher, 'load_settings') as load:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                watcher.run(['--help'])
            self.assertEqual(cm.exception.code, 0)
            load.assert_not_called()

    def test_test_url_needs_no_email_settings(self):
        with patch.object(watcher, 'load_settings', return_value=watcher.Settings()), \
             patch.object(watcher, 'check_once') as check, \
             patch.object(watcher, 'send_email') as send:
            watcher.run(['--test-url', 'file:///test.html', '--headless'])
            check.assert_called_once_with('file:///test.html', True)
            send.assert_not_called()

    def test_explicit_missing_file_is_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            watcher.run(['--env-file', '/nonexistent/config.env'])
        self.assertEqual(cm.exception.code, 2)


class FakeMatches:
    def __init__(self, values):
        self.values = values

    def count(self):
        return len(self.values)

    def nth(self, index):
        item = MagicMock()
        item.is_visible.return_value = self.values[index][1]
        return item


class FakePage:
    def __init__(self, values):
        self.values = values

    def goto(self, *args, **kwargs):
        pass

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, *args):
        pass

    def get_by_text(self, pattern):
        return FakeMatches([v for v in self.values if pattern.search(v[0])])

    def title(self):
        return 'Offline fixture'


class DetectionTests(unittest.TestCase):
    def test_open_and_expected_interest_labels(self):
        for text, expected in [('Book tickets', True), ('Interested', False),
                               ("I'm interested", False), ('I’m interested', False),
                               ('  BOOK   TICKETS  ', True)]:
            with self.subTest(text=text):
                self.assertEqual(watcher.booking_is_open(FakePage([(text, True)]), 'fixture'), expected)

    def test_unrelated_and_hidden_labels_are_not_success(self):
        for values in [[('Interested in joining our team?', True)],
                       [('How to book tickets online', True)], [('Book tickets', False)], []]:
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                watcher.booking_is_open(FakePage(values), 'fixture')


class AlertTests(unittest.TestCase):
    def test_all_failed_alerts_are_retried_without_rechecking_site(self):
        settings = watcher.Settings()
        with patch.object(watcher, 'try_send_email', side_effect=[False]*3 + [True, False, False]) as send, \
             patch.object(watcher, 'wait_for_next_check') as wait, \
             patch.object(watcher.time, 'sleep'), patch.object(watcher, 'booking_is_open') as check:
            watcher.send_booking_alerts(settings, 'fixture')
        self.assertEqual(send.call_count, 6)
        wait.assert_called_once_with(settings)
        check.assert_not_called()

    def test_successful_batch_does_not_retry(self):
        with patch.object(watcher, 'try_send_email', side_effect=[False, True, False]) as send, \
             patch.object(watcher, 'wait_for_next_check') as wait, patch.object(watcher.time, 'sleep'):
            watcher.send_booking_alerts(watcher.Settings(), 'fixture')
        self.assertEqual(send.call_count, 3)
        wait.assert_not_called()

    def test_failed_health_alert_retries_on_next_failed_check(self):
        fake_playwright = MagicMock()
        smtp_results = [True, False, True]  # startup, failed health email, retry succeeds
        detections = [RuntimeError('blocked') for _ in range(6)] + [True]
        with patch.object(watcher, 'sync_playwright', return_value=fake_playwright), \
             patch.object(watcher, 'booking_is_open', side_effect=detections), \
             patch.object(watcher, 'try_send_email', side_effect=smtp_results) as send, \
             patch.object(watcher, 'send_booking_alerts') as alert, \
             patch.object(watcher, 'wait_for_next_check'):
            watcher.watch(watcher.Settings(watch_url='fixture'), True)
        self.assertEqual(send.call_count, 3)
        self.assertIn('5 checks', send.call_args_list[1].args[2])
        self.assertIn('6 checks', send.call_args_list[2].args[2])
        alert.assert_called_once()

    def test_smtp_authentication_failure_does_not_fallback(self):
        settings = watcher.Settings(gmail_address='sender@example.com',
                                    gmail_app_password='dummy', alert_to='recipient@example.com')
        with patch.object(watcher.smtplib, 'SMTP_SSL') as secure, \
             patch.object(watcher.smtplib, 'SMTP') as fallback:
            secure.return_value.__enter__.return_value.login.side_effect = \
                watcher.smtplib.SMTPAuthenticationError(535, b'bad credentials')
            with self.assertRaises(watcher.smtplib.SMTPAuthenticationError):
                watcher.send_email(settings, 'subject', 'body')
            fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
