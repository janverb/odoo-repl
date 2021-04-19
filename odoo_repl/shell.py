"""A wrapper around Odoo's ``shell`` subcommand.

Can be executed as a drop-in replacement for ``odoo shell``, e.g.
``python -m odoo_repl.shell -c path/to/odoo.cfg``.
"""

from __future__ import print_function

import sys

import odoo_repl

from odoo_repl.imports import odoo, t

try:

    class OdooReplShell(odoo.cli.shell.Shell):
        def console(self, local_vars):
            # type: (t.Dict[str, t.Any]) -> None
            ns = odoo_repl.create_namespace(local_vars.get("env"))[1]
            local_vars.update(ns)  # type: ignore
            super(OdooReplShell, self).console(_QuietDict(local_vars))


except AttributeError:
    OdooReplShell = None  # type: ignore


class _QuietDict(dict):  # type: ignore
    """A dictionary that claims to have no keys.

    Odoo prints out the whole dict when starting a shell, but we put a lot of
    items in there, which gets spammy.
    """

    def __iter__(self):
        # type: () -> t.Iterator[t.NoReturn]
        return iter(())


def main(argv):
    # type: (t.Sequence[str]) -> int
    if OdooReplShell is None:
        if odoo:  # type: ignore
            print("Could not import shell command.", file=sys.stderr)
            if odoo.release.version_info < (9,):
                print("Odoo 8 and earlier are not supported.", file=sys.stderr)
            print("(Odoo path: {!r})".format(odoo.__file__), file=sys.stderr)
        else:
            print("Could not import Odoo.", file=sys.stderr)
            print("(Import path: {!r})".format(sys.path), file=sys.stderr)
        return 1
    OdooReplShell().run(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
