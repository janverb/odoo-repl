"""Messy repetitive compatibility/typing noise"""

import sys

PY3 = sys.version_info >= (3,)

MYPY = False
if MYPY:
    import odoo
else:
    try:
        import openerp as odoo
    except ImportError:
        try:
            import odoo
        except ImportError:
            odoo = None


if PY3:
    from collections import abc
else:
    import collections as abc

try:
    import typing as t
    from typing import overload
except ImportError:
    t = None  # type: ignore

    def overload(func):
        # type: (object) -> object
        return func


if PY3:
    Text = (str,)
    TextLike = (str, bytes)
else:
    Text = (str, unicode)  # noqa: F821
    TextLike = Text


if PY3:
    import builtins
else:
    import __builtin__ as builtins


if PY3:
    from io import StringIO
else:
    from StringIO import StringIO


if MYPY:
    Field = odoo.fields.Field[t.Any, t.Any]
elif odoo is not None and hasattr(odoo, "fields"):
    Field = odoo.fields.Field
else:
    Field = None


__all__ = (
    "MYPY",
    "PY3",
    "abc",
    "odoo",
    "t",
    "overload",
    "Text",
    "TextLike",
    "builtins",
    "Field",
    "StringIO",
)
