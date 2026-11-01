"""Runs first inside every standalone Python program the playbook builds.

The bundler freezes the build machine's Python into the program, and two things
about that machine would otherwise follow it onto every other one. Neither
shows up on the machine that built it, so nothing here can wait for a bug report.

This file is passed to PyInstaller as a runtime hook. It uses the standard
library only and is never imported by the playbook itself, except by its tests.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

# Where each system keeps its trusted certificates. The frozen OpenSSL looks in
# the folder it was built for, such as Homebrew's on a build Mac, which a Mac
# without Homebrew doesn't have, and then every HTTPS request fails.
CERTIFICATE_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # macOS
)


def use_system_certificates(environment: MutableMapping[str, str]) -> None:
    """Point OpenSSL at this machine's certificates, unless the user already chose."""
    if "SSL_CERT_FILE" in environment or "SSL_CERT_DIR" in environment:
        return
    for bundle in CERTIFICATE_BUNDLES:
        if os.path.isfile(bundle):
            environment["SSL_CERT_FILE"] = bundle
            return


def restore_library_path(environment: MutableMapping[str, str]) -> None:
    """Keep the programs this one starts away from its bundled libraries.

    The bootloader adds the program's runtime folder to LD_LIBRARY_PATH, and a child
    such as ffmpeg inherits that and would load those libraries instead of its own.
    The program itself is safe: the system reads the variable only at startup.
    """
    original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        environment["LD_LIBRARY_PATH"] = original
    else:
        environment.pop("LD_LIBRARY_PATH", None)


def main() -> None:
    if sys.platform == "win32":
        # Windows's equivalent of restore_library_path. It has to be undone here,
        # because a child inherits it. Windows finds certificates in its own store.
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
        return
    use_system_certificates(os.environ)
    restore_library_path(os.environ)


# PyInstaller runs a hook as a script, so this runs in the program, not on import.
if __name__ == "__main__" and getattr(sys, "frozen", False):
    main()
