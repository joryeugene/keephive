import os
import shutil
from pathlib import Path

import pytest

from .terminal import Terminal

pytestmark = pytest.mark.skipif(
    os.environ.get("HIVE_RUN_INSTALL_E2E") != "1",
    reason="Set HIVE_RUN_INSTALL_E2E=1 to run installer E2E tests (network + uv install).",
)


@pytest.mark.terminal
class TestInstallE2E:
    def test_interactive_branch_selection_prefix(self, tmp_path):
        """Test that typing 'da' resolves to 'daemon' branch."""
        # Use a fresh HOME for isolation
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        # Copy the installer to the fake home so we can run it
        installer = Path(__file__).parent.parent / "install.sh"
        shutil.copy(installer, fake_home / "install.sh")

        with Terminal(fake_home) as t:
            # Override HOME inside the tmux session
            t._send(f"export HOME={fake_home}")
            t._send(f"export HIVE_HOME={fake_home}/.keephive/hive")

            # Run the installer
            t._send("bash install.sh")

            # Wait for the branch selection prompt
            t.wait_for("Install from branch")

            # Type 'da' and Enter
            t.send_char("d")
            t.send_char("a")
            t.send_char("\n")

            # Verify it resolved to daemon
            t.wait_for("resolved 'da' to 'daemon'")

            # Verify it uses the daemon branch for install
            t.wait_for("Installing keephive (branch: daemon)")

    def test_explicit_branch_flag(self, tmp_path):
        """Test that --branch flag skips interaction and uses specified branch."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        installer = Path(__file__).parent.parent / "install.sh"
        shutil.copy(installer, fake_home / "install.sh")

        with Terminal(fake_home) as t:
            t._send(f"export HOME={fake_home}")
            t.type("bash install.sh --branch daemon --yes").has(
                "Installing keephive (branch: daemon)"
            )

    def test_invalid_branch_fallback(self, tmp_path):
        """Test that an invalid branch name is used anyway but with a warning."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        installer = Path(__file__).parent.parent / "install.sh"
        shutil.copy(installer, fake_home / "install.sh")

        with Terminal(fake_home) as t:
            t._send(f"export HOME={fake_home}")
            t._send("bash install.sh")
            t.wait_for("Install from branch")
            # Type 'nonexist' and Enter
            for char in "nonexist":
                t.send_char(char)
            t.send_char("\n")

            t.wait_for("branch 'nonexist' not found")
            t.wait_for("Installing keephive (branch: nonexist)")

    def test_install_from_cross_branch(self, tmp_path):
        """Test that selecting 'cross' branch actually attempts install from git cross branch."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        installer = Path(__file__).parent.parent / "install.sh"
        shutil.copy(installer, fake_home / "install.sh")

        with Terminal(fake_home) as t:
            t._send(f"export HOME={fake_home}")
            t._send("bash install.sh")
            t.wait_for("Install from branch")

            # Type 'cross' and Enter
            for char in "cross":
                t.send_char(char)
            t.send_char("\n")

            t.wait_for("Installing keephive (branch: cross)")
            t.wait_for("source: git+https://github.com/joryeugene/keephive.git@cross")
            # We don't wait for full completion as it's slow,
            # verifying the source selection is enough.
