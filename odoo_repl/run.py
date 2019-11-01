# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import os
import sys

import odoo_repl


def main():
    # TODO: flags for disabling features
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--database', type=str, default=None,
                        help="Database name")
    parser.add_argument('-c', '--command', type=str, default=None,
                        help="Initial command to execute")
    parser.add_argument('--ipython', action='store_true', default=False,
                        help="Use IPython instead of the default REPL")
    parser.add_argument('--ipython-args', type=str, default=None,
                        help="Extra flags to pass to IPython")
    parser.add_argument('directory', type=str, default='.', nargs='?',
                        help="Buildout directory to use")
    args = parser.parse_args()

    executable = os.path.join(args.directory, 'bin/python_odoo')

    if not os.path.isfile(executable):
        print("{!r} is not a buildout directory".format(args.directory))
        sys.exit(1)

    with open(executable) as f:
        py2 = 'python2' in f.readline()

    cmd = ('session.open(db={!r})'.format(args.database)
           if args.database else 'session.open()')
    cmd += """
import sys
sys.path.append({!r})
import odoo_repl
sys.path.pop()
odoo_repl.enable()
""".format(os.path.dirname(os.path.dirname(odoo_repl.__file__)))

    if 'PYTHONSTARTUP' in os.environ:
        if py2:
            cmd += """with open({!r}) as f:
    exec f.read()
""".format(os.environ['PYTHONSTARTUP'])
        else:
            cmd += """with open({!r}) as f:
    exec(f.read(), globals(), locals())
""".format(os.environ['PYTHONSTARTUP'])

    if args.command is not None:
        cmd += args.command

    if args.ipython:
        interp = 'ipython' if py2 else 'ipython3'
        argv = [interp, '--no-banner', '-i']
        if args.ipython_args:
            argv.extend(args.ipython_args.split())
        argv.extend(['--', executable, '-c', cmd])
        os.execvp(interp, argv)

    os.execv(executable, [executable, '-i', '-c', cmd])


if __name__ == '__main__':
    main()
