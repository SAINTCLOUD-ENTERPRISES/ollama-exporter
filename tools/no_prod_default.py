#!/usr/bin/env python3
"""Fail when a test, dev or e2e default names a prod host, the no-prod-default lint of platform-0128.

Vendored byte for byte into every service repo, platform-0138, so it holds nothing
repo-specific. It reads only the git-tracked files where a default lives:

- test files: ``*_test.go``, ``test_*.py``, ``*_test.py``, ``*.test.*``, ``*.spec.*``,
  ``conftest.py`` and any file named ``*fixture*``;
- anything under a ``test/``, ``tests/``, ``__tests__/``, ``testutil/``, ``testing/``,
  ``testdata/``, ``fixtures/`` or ``e2e/`` directory, ``.txt`` included, except prose:
  ``.md``, ``.mdx`` and ``.rst``;
- ``.env.example``, ``.env.*.example``, ``.env.test``, ``.env.development``, ``Makefile``,
  ``compose.{dev,test,e2e,local,override}.yml``, the same ``docker-compose.*.yml``,
  ``playwright.config.*``, ``vitest.config.*`` and ``jest.config.*``.

It reports a line when it holds:

- ``apollo`` or ``mnemosyne`` as a host: quoted bare, as in ``"apollo"``; after ``//`` with
  optional userinfo; after ``=`` or ``:``, optionally followed by ``-``, as in
  ``${DB_HOST:-apollo}``; after the no-colon default ``${NAME-``, as in ``${DB_HOST-apollo}``,
  where either default form may add ``.lan``; or before ``:<port>``. An npm scope such as ``@apollo/client`` and a
  domain such as ``apollo.io`` are not hosts. In a Makefile or a ``.env*`` file, also a whole
  word after whitespace, as in ``psql -h apollo``;
- ``apollo.lan``, ``mnemosyne.lan`` and any ``<name>.ts.net`` tailnet name;
- any ``<name>.stcloud.house`` except ``docs.stcloud.house``. The bare apex
  ``stcloud.house`` counts only in a URL: outside one, as in an email address or
  ``DOMAIN=stcloud.house``, it is allowed;
- an address in the house LANs 10.0.0.0/24 and 10.20.0.0/16, or the tailnet 100.64.0.0/10
  and fd7a:115c:a1e0::/48.

``.no-prod-default-allow`` at the repo root lists exceptions, one per line:
``<path glob> [<literal the line must contain>] # <reason>``. The reason is required and is
everything after the last `` # ``, space hash space, so a literal may hold a ``#`` and a
reason may cite ``#12``. A tab or no space beside the ``#`` is no separator, so such a line
has no reason. A line that starts with ``#`` is a comment. An allow line with no reason,
or one that excuses nothing, fails the check. Binary files, symlinks and any file named
``no_prod_default.py`` or ``test_no_prod_default.py``, this script and its test, are skipped.

Usage: ``python3 no_prod_default.py [repo root]``. Exit 0 clean, 1 on any hit.
"""

import fnmatch
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

PROSE = (".md", ".mdx", ".rst")
SCOPE_DIRS = {"test", "tests", "__tests__", "testutil", "testing", "testdata", "fixtures", "e2e"}
TEST_FILE = re.compile(
    r"_test\.(go|py)$|^test_.*\.py$|\.(test|spec)\.[cm]?[jt]sx?$|^conftest\.py$|fixture"
)
DEV_FILE = re.compile(
    r"^(\.env(\.[\w-]+)?\.example|\.env\.(test|development)|Makefile"
    r"|(docker-)?compose\.(dev|test|e2e|local|override)\.ya?ml"
    r"|playwright\.config\.[cm]?[jt]s|(vitest|jest)\.config\.\w+)$"
)
# Every alternative starts at a fixed character or behind a lookbehind, and every
# repetition stops at a delimiter, so a scan is linear in the line's length.
HOST = r"(?:apollo|mnemosyne)"
NAME = re.compile(
    r"([\"'`])" + HOST + r"\1"
    r"|//(?:[^\s/@]+@)?" + HOST + r"(?![\w.-])"
    r"|(?:[=:]-?|\$\{\w+-)\s*" + HOST + r"(?:\.lan)?(?![\w.-])"
    r"|(?<![\w.-])" + HOST + r":\d"
    r"|(?<![\w.-])" + HOST + r"\.lan(?![\w-]|\.\w)"
    r"|(?<![\w.-])(?:[a-z0-9-]+\.)+ts\.net(?![\w-]|\.\w)",
    re.I,
)
# Makefiles and .env* files, where a host is often a bare word: psql -h apollo.
LOOSE = re.compile(r"(?<=\s)" + HOST + r"(?![\w.-])", re.I)
HOUSE = re.compile(
    r"(?<![\w.-])(//(?:[^\s/@]+@)?)?((?:[a-z0-9-]+\.)*stcloud\.house)(?![\w-]|\.\w)", re.I
)
IP4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?!\w|\.\d)")
TAILNET6 = re.compile(r"(?<![\w:])fd7a:115c:a1e0:[0-9a-f:]*", re.I)
NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/24", "10.20.0.0/16", "100.64.0.0/10")]
ALLOW = ".no-prod-default-allow"
SELF = {"no_prod_default.py", "test_no_prod_default.py"}


def in_scope(rel):
    """Return whether the repo-relative path ``rel`` is a test, dev or e2e file."""
    parts = rel.split("/")
    name = parts[-1]
    if SCOPE_DIRS.intersection(parts[:-1]):
        return not name.lower().endswith(PROSE)
    return bool(TEST_FILE.search(name) or DEV_FILE.match(name))


def prod_ip(s):
    """Return whether ``s`` is an IPv4 address in a house LAN or the tailnet."""
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False
    return any(ip in n for n in NETS)


def _house(m, line):
    url, host = m.group(1), m.group(2).lower()
    if host == "docs.stcloud.house":
        return False
    return bool(url) or (host != "stcloud.house" and line[m.start() - 1 : m.start()] != "@")


def loose_file(rel):
    """Return whether ``rel`` is a Makefile or ``.env*`` file, where bare words count."""
    name = rel.rsplit("/", 1)[-1]
    return name == "Makefile" or name.startswith(".env")


def hits(line, loose=False):
    """Return each prod host or address the line names, in the order of the checks."""
    found = [m.group(0) for m in NAME.finditer(line)]
    if loose:
        found += [m.group(0) for m in LOOSE.finditer(line)]
    found += [m.group(0) for m in HOUSE.finditer(line) if _house(m, line)]
    found += [m.group(0) for m in IP4.finditer(line) if prod_ip(m.group(0))]
    return found + [m.group(0) for m in TAILNET6.finditer(line)]


def allowlist(root):
    """Return ``(rules, errors)`` from the allow file: rules are (line no, glob, literal)."""
    path = root / ALLOW
    if not path.is_file():
        return [], []
    rules, errors = [], []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body, sep, reason = raw.rpartition(" # ")
        if not sep or not reason.strip() or not body.strip():
            errors.append(f"{ALLOW}:{n}: allow line needs a reason after #")
            continue
        glob, _, literal = body.strip().partition(" ")
        rules.append((n, glob, literal.strip()))
    return rules, errors


def scan(root):
    """Yield one ``path:line: hits`` or allow-file error per problem in the repo at ``root``."""
    rules, errors = allowlist(root)
    used = set()
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode()
    for rel in filter(None, listed.split("\0")):
        path = root / rel
        if not in_scope(rel) or path.is_symlink() or not path.is_file():
            continue
        if rel.rsplit("/", 1)[-1] in SELF:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            found = hits(line, loose_file(rel))
            if not found:
                continue
            excused = [r for r in rules if fnmatch.fnmatch(rel, r[1]) and r[2] in line]
            used.update(r[0] for r in excused)
            if not excused:
                yield f"{rel}:{n}: {', '.join(found)}"
    yield from errors
    for n, _, _ in rules:
        if n not in used:
            yield f"{ALLOW}:{n}: allow line matches nothing"


def main(argv):
    """Lint the repo at ``argv[1]``, default the working directory; return the exit code."""
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    found = list(scan(root))
    for f in found:
        print(f)
    if found:
        print(f"no-prod-default: {len(found)} problem(s), platform-0128")
        return 1
    print("no-prod-default: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
