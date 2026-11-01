import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playbook.ci import build, frozen_runtime
from playbook.ci.frozen_runtime import restore_library_path, use_system_certificates


class CertificatesTest(unittest.TestCase):
    def test_the_first_bundle_this_machine_has_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "cert.pem"
            bundle.write_text("certificates")
            environment = {}
            with mock.patch.object(frozen_runtime, "CERTIFICATE_BUNDLES", ("/nowhere/a.pem", str(bundle))):
                use_system_certificates(environment)
        self.assertEqual(environment, {"SSL_CERT_FILE": str(bundle)})

    def test_every_system_the_program_runs_on_is_covered(self):
        for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            self.assertIn(path, frozen_runtime.CERTIFICATE_BUNDLES)

    def test_a_machine_without_a_bundle_keeps_openssls_own_default(self):
        environment = {}
        with mock.patch.object(frozen_runtime, "CERTIFICATE_BUNDLES", ("/nowhere/a.pem",)):
            use_system_certificates(environment)
        self.assertEqual(environment, {})

    def test_the_users_own_choice_wins(self):
        for chosen in ({"SSL_CERT_FILE": "/mine.pem"}, {"SSL_CERT_DIR": "/mine"}):
            with self.subTest(chosen=chosen):
                environment = dict(chosen)
                use_system_certificates(environment)
                self.assertEqual(environment, chosen)


class LibraryPathTest(unittest.TestCase):
    def test_the_users_own_path_comes_back(self):
        environment = {"LD_LIBRARY_PATH": "/tmp/_MEI123:/mine", "LD_LIBRARY_PATH_ORIG": "/mine"}
        restore_library_path(environment)
        self.assertEqual(environment, {"LD_LIBRARY_PATH": "/mine"})

    def test_a_path_the_bootloader_added_goes_away(self):
        environment = {"LD_LIBRARY_PATH": "/tmp/_MEI123"}
        restore_library_path(environment)
        self.assertEqual(environment, {})


class HookTest(unittest.TestCase):
    def test_the_build_passes_this_file_to_the_bundler(self):
        self.assertEqual(build.FROZEN_RUNTIME, Path(frozen_runtime.__file__))

    def test_it_runs_as_a_script_in_a_frozen_program(self):
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEI123"}),
        ):
            runpy.run_path(frozen_runtime.__file__, run_name="__main__")
            self.assertNotIn("LD_LIBRARY_PATH", os.environ)

    def test_importing_it_changes_nothing(self):
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/keep"}),
        ):
            runpy.run_path(frozen_runtime.__file__, run_name="frozen_runtime")
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/keep")

    def test_it_stays_out_of_an_ordinary_python(self):
        with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/keep"}):
            runpy.run_path(frozen_runtime.__file__, run_name="__main__")
            self.assertEqual(os.environ["LD_LIBRARY_PATH"], "/keep")


if __name__ == "__main__":
    unittest.main()
