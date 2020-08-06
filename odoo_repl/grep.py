"""Functions for running grep or alternatives. Used by various methods and functions.

Examples:
>>> grep_("test")
>>> grep_("-e", "foo", "-e", "bar")
>>> grep_("test", A=5)  # grep -A 5
>>> grep_("test", i=True)  # grep -i
>>> grep_("test", max_count=3)  # grep --max-count 3

Because grep takes up a lot of horizontal space to display filenames,
this method defaults to rg (ripgrep), ag (the silver searcher) or ack,
if they're available. grep is used otherwise.

ripgrep's flags are most similar to grep's if you're looking for
something familiar.

Set the $ODOO_REPL_GREP environment variable to override the command.
You can use flags in it.

TODO: GNU grep is assumed. If you use another implementation then your
best option is to install one of the other tools listed above.
"""

from __future__ import print_function

import inspect
import os
import shlex
import subprocess
import sys

from odoo_repl import color
from odoo_repl import config
from odoo_repl import sources
from odoo_repl.imports import t, PY3, Text


def find_grep(default="grep"):
    # type: (t.Text) -> t.List[t.Any]
    """Look for a grep-like program to use."""
    if config.grep:
        return shlex.split(config.grep)
    for prog in "rg", "ag", "ack":
        if which(prog):
            return [prog]
    # For disgusting technical reasons, default may not contain unicode in PY2
    return shlex.split(str(default))


def build_grep_argv(args, kwargs, recursive=False):
    # type: (t.Iterable[object], t.Mapping[str, object], bool) -> t.List[t.Text]
    argv = find_grep()
    if argv[0] == "grep" and config.color:
        argv.append("--color=auto")
    for key, value in kwargs.items():
        flag = "-" + key if len(key) == 1 else "--" + key.replace("_", "-")
        argv.append(flag)
        if value is not True:
            argv.append(str(value))
    argv.extend(map(str, args))
    argv.append("--")
    if recursive and argv[0] == "grep":
        argv[1:1] = ["-r", "--exclude-dir=.git"]
    return argv


def partial_grep(argv, thing, header=None, lnum=None):
    # type: (t.Sequence[t.Union[bytes, t.Text]], t.Any, t.Any, t.Any) -> None
    """Simulate grepping through just part of a file."""
    if isinstance(thing, Text):
        lines = thing.split("\n")  # type: t.Sequence[t.Text]
    else:
        header = sources.getsourcefile(thing)
        lines, lnum = inspect.getsourcelines(thing)
    # We mimic the output of ripgrep, which itself blends grep and ack
    # One difference is that ripgrep prints non-matching line numbers
    # with a dash following the number instead of a colon
    proc_input = "".join(
        "{}:{}\n".format(color.green(str(lnum + ind)), line.rstrip("\n"))
        for ind, line in enumerate(lines)
    )
    # First we do a test run just to see if there are results
    # That way we can skip writing the filename if there aren't any
    # We could capture the output and print it, but then terminal
    # detection would fail
    with open(os.devnull, "w") as outfile:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=outfile,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        assert proc.stdin is not None
        assert proc.stderr is not None
        proc.stdin.write(proc_input)
        proc.stdin.close()
        error = proc.stderr.read()
        if proc.wait() != 0:
            if error:
                # The command printed *something* to stderr, so
                # let's assume it's an error message about a
                # non-existent flag or something and quit.
                # stderr is ignored if the command exited
                # successfully, if it's interesting it'll probably
                # pop up again in the "real" run.
                raise BadCommandline(error)
            raise NoResults

    print(color.purple(header))
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, universal_newlines=True)
    assert proc.stdin is not None
    proc.stdin.write(proc_input)
    proc.stdin.close()
    proc.wait()


class BadCommandline(RuntimeError):
    pass


class NoResults(RuntimeError):
    pass


if PY3:
    from shutil import which
else:
    # Copied from the Python 3.7 stdlib
    def which(cmd, mode=os.F_OK | os.X_OK, path=None):
        # type: (t.Text, int, t.Optional[t.Text]) -> t.Optional[t.Text]
        """Given a command, mode, and a PATH string, return the path which
        conforms to the given mode on the PATH, or None if there is no such
        file.

        `mode` defaults to os.F_OK | os.X_OK. `path` defaults to the result
        of os.environ.get("PATH"), or can be overridden with a custom search
        path.

        """
        # Check that a given file can be accessed with the correct mode.
        # Additionally check that `file` is not a directory, as on Windows
        # directories pass the os.access check.
        def _access_check(fn, mode):
            # type: (t.Text, int) -> bool
            return os.path.exists(fn) and os.access(fn, mode) and not os.path.isdir(fn)

        # If we're given a path with a directory part, look it up directly rather
        # than referring to PATH directories. This includes checking relative to the
        # current directory, e.g. ./script
        if os.path.dirname(cmd):
            if _access_check(cmd, mode):
                return cmd
            return None

        if path is None:
            path = os.environ.get("PATH", os.defpath)
        if not path:
            return None
        path_l = path.split(os.pathsep)

        if sys.platform == "win32":
            # The current directory takes precedence on Windows.
            if os.curdir not in path_l:
                path_l.insert(0, os.curdir)

            # PATHEXT is necessary to check on Windows.
            pathext = os.environ.get("PATHEXT", "").split(os.pathsep)
            # See if the given file matches any of the expected path extensions.
            # This will allow us to short circuit when given "python.exe".
            # If it does match, only test that one, otherwise we have to try
            # others.
            if any(cmd.lower().endswith(ext.lower()) for ext in pathext):
                files = [cmd]
            else:
                files = [cmd + ext for ext in pathext]
        else:
            # On other platforms you don't have things like PATHEXT to tell you
            # what file suffixes are executable, so just pass on cmd as-is.
            files = [cmd]

        seen = set()  # type: t.Set[t.Text]
        for directory in path_l:
            normdir = os.path.normcase(directory)
            if normdir not in seen:
                seen.add(normdir)
                for thefile in files:
                    name = os.path.join(directory, thefile)
                    if _access_check(name, mode):
                        return name
        return None
