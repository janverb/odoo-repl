#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A wrapper script around python_odoo from the Odoo buildout recipe."""

from __future__ import print_function

import argparse
import os
import sys

import odoo_repl

from odoo_repl.imports import t


def main(argv=sys.argv[1:]):
    # type: (t.Sequence[str]) -> int
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--command", type=str, default=None, help="Initial command to execute"
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Immediately quit odoo-repl after starting",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run odoo-repl's own tests, then exit",
    )
    parser.add_argument(
        "-s",
        "--with-server",
        action="store_true",
        help="Run the web server in the background",
    )
    parser.add_argument(
        "directory", type=str, default=".", nargs="?", help="Buildout directory to use"
    )
    parser.add_argument(
        "extra_args", nargs="*", help="Extra configuration arguments you'd pass to odoo"
    )
    args = parser.parse_args(argv)

    directory = os.path.abspath(args.directory)

    if not os.path.isdir(directory):
        print("Directory {!r} does not exist".format(directory))
        return 1

    executable = os.path.join(directory, "bin/python_odoo")

    if not os.path.isfile(executable):
        print("{!r} is not a buildout directory".format(directory))
        return 1

    cmd = """
import sys
sys.path.append({odoo_repl_path!r})
import odoo_repl
sys.path.pop()
odoo_repl.parse_config(['-c', session.openerp_config_file] + {extra_args!r})
session.open()
_, ns = odoo_repl.create_namespace(session.env)
""".format(
        odoo_repl_path=os.path.dirname(
            os.path.dirname(odoo_repl.__file__)
        ),  # Might be fragile
        extra_args=args.extra_args,
    )

    if os.environ.get("PYTHONSTARTUP"):
        # $PYTHONSTARTUP isn't read when executing a file, but if you have one
        # then you probably want to use it when running this script, so load
        # it manually
        cmd += """
with open({!r}) as f:
    exec(f.read(), ns)
""".format(
            os.environ["PYTHONSTARTUP"]
        )

    if args.command is not None:
        cmd += "exec({!r}, ns)\n".format(args.command)

    if args.run_tests:
        cmd += """
from odoo_repl import tests
result = tests.run()
sys.exit(1 if result.errors or result.failures else 0)
"""

    if args.with_server:
        cmd += """
server = odoo_repl.imports.odoo.service.server.ThreadedServer(
    odoo_repl.imports.odoo.service.wsgi_server.application
)
server.start()
"""

    if not args.no_interactive and not args.run_tests:
        cmd += """
from IPython import start_ipython
start_ipython(argv=[], user_ns=ns)
"""

    os.execvp(executable, [executable, "-c", cmd])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
