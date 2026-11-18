"""Portable regressions for the synthetic integration assertions (no GPG needed)."""
import subprocess
import unittest

from integration_apt import assert_helper_failure, assert_validsig


class IntegrationAssertions(unittest.TestCase):
    primary = 'A' * 40
    subkey = 'B' * 40
    other = 'C' * 40

    def status(self, signing=None, primary=None):
        return (f'[GNUPG:] VALIDSIG {signing or self.subkey} 2026-11-17 '
                f'1794873600 0 4 0 1 10 01 {primary or self.primary}\n')

    def test_validsig_positive_control(self):
        assert_validsig(self.status(), self.subkey, self.primary)

    def test_unrelated_primary_cannot_satisfy_mismatched_field(self):
        status = self.status(primary=self.other) + f'[GNUPG:] NEWSIG {self.primary}\n'
        # This is precisely the false positive in the old whole-status assertion.
        self.assertIn(self.primary, status)
        with self.assertRaisesRegex(AssertionError, 'wrong VALIDSIG primary'):
            assert_validsig(status, self.subkey, self.primary)

    def test_wrong_signing_fingerprint(self):
        with self.assertRaisesRegex(AssertionError, 'wrong VALIDSIG signing'):
            assert_validsig(self.status(signing=self.other), self.subkey, self.primary)

    def test_requires_sole_complete_validsig(self):
        for status in ('', self.status() * 2, self.status().rsplit(' ', 1)[0] + '\n'):
            with self.subTest(status=status), self.assertRaises(AssertionError):
                assert_validsig(status, self.subkey, self.primary)

    def test_helper_causes_positive_controls_and_unrelated_failure(self):
        unrelated = subprocess.CalledProcessError(
            127, ['bash', 'helper'], stderr=b'bash: unrelated-command: command not found\n')
        for diagnostic in (b'gpg: signing failed: Bad passphrase', b'error: no deb packages'):
            with self.subTest(diagnostic=diagnostic):
                intended = subprocess.CalledProcessError(
                    1, ['bash', 'helper'], stderr=diagnostic + b'\n')
                assert_helper_failure(intended, diagnostic)
                with self.assertRaisesRegex(AssertionError, 'missing cause-specific'):
                    assert_helper_failure(unrelated, diagnostic)
                with self.assertRaisesRegex(AssertionError, 'missing cause-specific'):
                    assert_helper_failure(subprocess.CalledProcessError(1, ['bash']), diagnostic)


if __name__ == '__main__':
    unittest.main()
