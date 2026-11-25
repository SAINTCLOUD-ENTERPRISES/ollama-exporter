"""Tests for no_prod_default.py: python3 tools/test_no_prod_default.py. Vendored with it."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("no_prod_default.py")
# An inherited GIT_DIR, as a pre-push hook sets, would point git init at the real repo.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def lint(files, script=SCRIPT, links=None):
    """Stage ``files`` in a fresh repo, run the lint over it, return (exit code, stdout).

    A ``bytes`` value is written as is. ``links`` maps a symlink path to its target.
    ``script`` may be a callable that takes the repo root and returns the script to run.
    """
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            p = Path(d) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, bytes):
                p.write_bytes(text)
            else:
                p.write_text(text)
        for rel, target in (links or {}).items():
            (Path(d) / rel).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, Path(d) / rel)
        subprocess.run(["git", "init", "-q", d], check=True, env=ENV)
        subprocess.run(["git", "-C", d, "add", "-f", "."], check=True, env=ENV)
        if callable(script):
            script = script(Path(d))
        r = subprocess.run(
            [sys.executable, str(script), d], capture_output=True, text=True, env=ENV
        )
        return r.returncode, r.stdout


# Imports the script by path and runs hits() on stdin, in every mode, in a child
# process, so a catastrophic regex is killed at the timeout rather than hanging.
TIMED = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('npd', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "line = sys.stdin.read(); m.hits(line); m.hits(line, loose=True)\n"
)


class NoProdDefault(unittest.TestCase):
    def test_a_prod_dsn_default_fails(self):
        code, out = lint({".env.example": "DATABASE_URL=postgres://x@apollo:5432/x\n"})
        self.assertEqual(code, 1)
        self.assertIn(".env.example:1: //x@apollo", out)

    def test_each_kind_of_prod_name_fails(self):
        bad = [
            "API ?= https://photos.stcloud.house/",
            "base: 'http://10.0.0.2:3112'",
            "dns: 10.20.3.3",
            "peer = 100.103.1.1",
            'HOST: "mnemosyne"',
            "DB_HOST=apollo",
            "nc apollo:5432",
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertEqual(lint({"Makefile": line + "\n"})[0], 1)

    def test_each_new_catch_fails(self):
        bad = [
            'addr := []string{"apollo", "5432"}',
            "host := 'mnemosyne'",
            "host := `apollo`",
            "dial 10.0.0.254",
            "dial 10.20.255.1",
            "peer 100.64.0.1",
            "peer 100.127.255.255",
            "peer fd7a:115c:a1e0::1",
            "peer [FD7A:115C:A1E0:ab::2]:443",
            "url := grafana.stcloud.house",
            "url := x.docs.stcloud.house",
            "url := https://u:p@stcloud.house/",
            "url := smtp://ksc@stcloud.house",
        ]
        for line in bad:
            with self.subTest(line=line):
                self.assertEqual(lint({"a_test.go": line + "\n"})[0], 1)

    def test_each_non_catch_passes(self):
        good = [
            'import { gql } from "@apollo/client"',
            "import x from '@apollo/client/core'",
            'site := "apollo.io"',
            "site := https://apollo.io/docs",
            "mail := ksc@stcloud.house",
            "docs := docs.stcloud.house",
            "docs := https://docs.stcloud.house/systems/platform/",
            "dial 10.0.1.5",
            "dial 10.1.0.1",
            "dial 10.21.0.1",
            "peer 100.128.0.1",
            "peer fd7a:115c:a1e1::1",
            'title := "Deploy to apollo"',
            'runner := "apollo-org"',
        ]
        for line in good:
            with self.subTest(line=line):
                self.assertEqual(lint({"a_test.go": line + "\n"}), (0, "no-prod-default: ok\n"))

    def test_safe_defaults_and_prose_pass(self):
        code, out = lint(
            {
                "compose.yml": "url: postgres://x@localhost:5432/x\nhost: 127.0.0.1\n",
                "x_test.go": 'title := "Deploy to apollo"\nerr := "dial 192.0.2.4:1883"\n',
                "ci.yml": "runs-on: apollo-org\nversion: 1.10.0.3.4\n",
                "notes.txt": "renew stcloud.house\n",
                "README.md": "e.g. http://apollo:3112/memory-api\n",
            }
        )
        self.assertEqual((code, out.strip()), (0, "no-prod-default: ok"))

    def test_only_test_dev_and_e2e_paths_are_scanned(self):
        line = 'host := "apollo"\n'
        scanned = [
            "a_test.go",
            "tools/test_x.py",
            "x_test.py",
            "web/a.test.ts",
            "web/a.spec.tsx",
            "internal/x/testdata/feed.txt",
            "fixtures/x.json",
            "e2e/helpers.ts",
            ".env.example",
            ".env.local.example",
            "Makefile",
            "compose.dev.yml",
            "docker-compose.test.yaml",
            "playwright.config.ts",
        ]
        for rel in scanned:
            with self.subTest(rel=rel):
                self.assertEqual(lint({rel: line})[0], 1)
        skipped = {
            rel: line
            for rel in (
                "cmd/main.go",
                "ci.yml",
                "notes.txt",
                "compose.yml",
                ".env",
                "e2e/README.md",
                "fixtures/notes.rst",
            )
        }
        self.assertEqual(lint(skipped), (0, "no-prod-default: ok\n"))

    def test_binary_symlink_and_the_script_itself_are_skipped(self):
        files = {
            "bin_test.go": b'\x00host := "apollo"\n',
            "ops/hosts.yml": 'host: "apollo"\n',
        }
        code, out = lint(files, links={"link_test.go": "ops/hosts.yml"})
        self.assertEqual((code, out), (0, "no-prod-default: ok\n"))

        def vendored(root):
            (root / "tools").mkdir()
            shutil.copy(SCRIPT, root / "tools" / SCRIPT.name)
            shutil.copy(Path(__file__).resolve(), root / "tools" / "test_no_prod_default.py")
            subprocess.run(["git", "-C", str(root), "add", "-f", "."], check=True, env=ENV)
            return root / "tools" / SCRIPT.name

        self.assertEqual(lint({}, script=vendored), (0, "no-prod-default: ok\n"))

    def test_the_allowlist_takes_a_glob_an_optional_literal_and_a_reason(self):
        files = {
            "cmd/a_test.go": 'url := "http://apollo:1"\nother := "http://apollo:2"\n',
            "e2e/hosts.yml": "host: mnemosyne\n",
            ".no-prod-default-allow": "# a comment line\n"
            "cmd/*_test.go apollo:1 # the parser's own fixture\n"
            "e2e/* # pinned against a recorded fixture\n",
        }
        code, out = lint(files)
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], "cmd/a_test.go:2: //apollo")
        self.assertNotIn("e2e/hosts.yml", out)

    def test_an_allow_literal_may_hold_a_hash(self):
        files = {
            "a_test.go": 'u := "http://apollo:1/#frag"\n',
            ".no-prod-default-allow": "a_test.go apollo:1/#frag # fragment fixture\n",
        }
        self.assertEqual(lint(files), (0, "no-prod-default: ok\n"))

    def test_an_allow_line_without_a_reason_fails(self):
        files = {
            "a_test.go": 'u := "http://apollo:1"\n',
            ".no-prod-default-allow": "a_test.go apollo:1\n",
        }
        code, out = lint(files)
        self.assertEqual(code, 1)
        self.assertIn(".no-prod-default-allow:1: allow line needs a reason after #", out)

    def test_an_allow_line_that_matches_nothing_fails(self):
        files = {
            "a_test.go": "ok := true\n",
            ".no-prod-default-allow": "a_test.go apollo:1 # stale\n",
        }
        code, out = lint(files)
        self.assertEqual(code, 1)
        self.assertIn(".no-prod-default-allow:1: allow line matches nothing", out)

    def test_an_allow_reason_may_cite_an_issue(self):
        files = {
            "a_test.go": 'u := "http://apollo:1"\n',
            ".no-prod-default-allow": "a_test.go apollo:1 # recorded fixture, see issue #12\n",
        }
        self.assertEqual(lint(files), (0, "no-prod-default: ok\n"))

    def test_an_allow_literal_may_hold_space_hash_space(self):
        files = {
            "a_test.go": 'u := "http://apollo:1" # note\n',
            ".no-prod-default-allow": 'a_test.go apollo:1" # note # fixture\n',
        }
        self.assertEqual(lint(files), (0, "no-prod-default: ok\n"))

    def test_an_allow_line_splits_on_the_last_space_hash_space(self):
        # A first-occurrence split would cut the literal to 'apollo:1"' and excuse line 2 too.
        files = {
            "a_test.go": 'u := "http://apollo:1" # note\nv := "http://apollo:1"\n',
            ".no-prod-default-allow": 'a_test.go apollo:1" # note # fixture\n',
        }
        code, out = lint(files)
        self.assertEqual((code, out.splitlines()[0]), (1, "a_test.go:2: //apollo"))

    def test_a_dash_default_names_a_host(self):
        for line in (
            "host := ${DB_HOST:-apollo}",
            "host := ${X:-mnemosyne}",
            "HOST=-apollo",
            "host := ${X:-apollo.lan}",
            "host := ${DB_HOST-apollo}",
            "addr := ${A-apollo:5432}",
            "cmd := psql -h ${A-apollo}",
        ):
            for rel in ("a_test.go", "Makefile", "compose.dev.yml"):
                with self.subTest(rel=rel, line=line):
                    self.assertEqual(lint({rel: line + "\n"})[0], 1)
        for line in (
            'import x from "@apollo/client"',
            "site := apollo.io",
            "host := ${X:-localhost}",
            'name := "deploy-apollo"',
            "resilio-sync-apollo and more",
            "x-mnemosyne",
        ):
            for rel in ("a_test.go", "Makefile"):
                with self.subTest(rel=rel, line=line):
                    self.assertEqual(lint({rel: line + "\n"}), (0, "no-prod-default: ok\n"))

    def test_a_tab_or_no_space_around_the_hash_is_no_separator(self):
        for allow in ("a_test.go apollo:1\t# fixture\n", "a_test.go apollo:1 #fixture\n"):
            with self.subTest(allow=allow):
                code, out = lint({"a_test.go": 'u := "http://apollo:1"\n', ".no-prod-default-allow": allow})
                self.assertEqual(code, 1)
                self.assertIn(".no-prod-default-allow:1: allow line needs a reason after #", out)

    def test_the_widened_scope_is_scanned(self):
        line = 'host := "apollo"\n'
        for rel in (
            "test/helpers.go",
            "tests/it.rs",
            "web/__tests__/x.ts",
            "internal/testutil/db.go",
            "pkg/testing/x.go",
            "conftest.py",
            "tests/conftest.py",
            "db_fixture.sql",
            "seed-fixtures.json",
            ".env.test",
            ".env.development",
            ".env.local.example",
            "compose.local.yml",
            "compose.override.yml",
            "docker-compose.override.yml",
            "vitest.config.ts",
            "vitest.config.mts",
            "jest.config.js",
            "jest.config.json",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(lint({rel: line})[0], 1)
        self.assertEqual(lint({"tests/README.md": line}), (0, "no-prod-default: ok\n"))

    def test_makefiles_and_env_files_catch_a_host_after_whitespace(self):
        bad = ["psql -h apollo -p 5432", "HOST apollo", "\tssh mnemosyne", "DB_HOST=apollo"]
        for rel in ("Makefile", "web/Makefile", ".env.test", ".env.example"):
            for line in bad:
                with self.subTest(rel=rel, line=line):
                    self.assertEqual(lint({rel: line + "\n"})[0], 1)
        good = [
            "npm i @apollo/client",
            "SITE ?= https://apollo.io",
            "echo apollo.io",
            "RUNNER ?= apollo-org",
            "DOMAIN ?= stcloud.house",
        ]
        for rel in ("Makefile", ".env.test"):
            for line in good:
                with self.subTest(rel=rel, line=line):
                    self.assertEqual(lint({rel: line + "\n"}), (0, "no-prod-default: ok\n"))
        code, out = lint({"a_test.go": 'title := "psql -h apollo"\n'})
        self.assertEqual((code, out), (0, "no-prod-default: ok\n"))

    def test_lan_and_tailnet_names_fail(self):
        for line in (
            "url := http://apollo.lan:3112",
            "host := mnemosyne.lan",
            "API ?= http://apollo.lan:3112",
            "peer := apollo.tail1234.ts.net",
            "url := https://x.ts.net/",
        ):
            for rel in ("a_test.go", "Makefile"):
                with self.subTest(rel=rel, line=line):
                    self.assertEqual(lint({rel: line + "\n"})[0], 1)
        for line in ("site := apollo.landing.io", "site := cats.net", "x := ts.net.example"):
            with self.subTest(line=line):
                self.assertEqual(lint({"a_test.go": line + "\n"}), (0, "no-prod-default: ok\n"))

    def test_a_bare_apex_outside_a_url_passes(self):
        for rel, line in (("a_test.go", "domain := stcloud.house"), ("Makefile", "DOMAIN ?= stcloud.house")):
            with self.subTest(rel=rel):
                self.assertEqual(lint({rel: line + "\n"}), (0, "no-prod-default: ok\n"))

    def test_the_lint_files_are_skipped_by_name_wherever_the_script_runs(self):
        def elsewhere(root):
            (root / "tools").mkdir()
            shutil.copy(SCRIPT, root / "tools" / SCRIPT.name)
            shutil.copy(Path(__file__).resolve(), root / "tools" / "test_no_prod_default.py")
            subprocess.run(["git", "-C", str(root), "add", "-f", "."], check=True, env=ENV)
            (root / "run").mkdir()
            return shutil.copy(SCRIPT, root / "run" / "lint.py")  # untracked

        self.assertEqual(lint({}, script=elsewhere), (0, "no-prod-default: ok\n"))

    def test_hostile_lines_scan_in_linear_time(self):
        n = 20000
        for line in (
            "a" * n,
            "a." * n,
            "//" + "a" * n,
            "1." * n,
            "apollo" * 5000,
            "fd7a:" * 5000,
            "= " * n,
            "//a@" * n,
            "\t" * n + "apollo",
            " " + "a-" * n + "x",
            "apollo." * 5000 + "lan",
            "a." * n + "ts.ne",
            ".stcloud.house" * 3000,
        ):
            with self.subTest(line=line[:12]):
                try:
                    r = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", TIMED, str(SCRIPT)],
                        input=line, capture_output=True, text=True, timeout=5,
                    )
                except subprocess.TimeoutExpired:
                    self.fail("hits() took over 5s")
                self.assertEqual(r.returncode, 0, r.stderr)

if __name__ == "__main__":
    unittest.main()
