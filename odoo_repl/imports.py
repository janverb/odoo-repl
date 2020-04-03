"""Messy repetitive compatibility/typing noise"""

import sys

PY3 = sys.version_info >= (3,)

MYPY = False
if MYPY:
    import odoo
elif PY3:
    # Newer versions get a bit loud if we try to import openerp
    # But in Odoo 8 importing odoo may give the wrong module, so we should
    # try openerp first
    # Luckily we only have to import openerp on PY2-exclusive versions
    # So we can tell ahead of time that we don't have to try
    try:
        import odoo
    except ImportError:
        odoo = None
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
    from typing import cast, overload
except ImportError:
    t = None  # type: ignore

    def cast(_typ, val):  # type: ignore
        # type: (object, object) -> object
        return val

    def overload(func):
        # type: (object) -> object
        return func


if PY3:
    Unicode = str
    Text = (str,)
    TextLike = (str, bytes)
else:
    Unicode = unicode  # noqa: F821
    Text = (str, Unicode)
    TextLike = Text


if PY3:
    import builtins
else:
    import __builtin__ as builtins


if PY3:
    from io import StringIO
else:
    from StringIO import StringIO


if PY3:
    from urllib.parse import urlparse
else:
    from urlparse import urlparse


if MYPY:
    Field = odoo.fields.Field[t.Any, t.Any]
    BaseModel = odoo.models.BaseModel
    AnyModel = t.TypeVar("AnyModel", bound=BaseModel)
elif odoo is not None and hasattr(odoo, "fields") and hasattr(odoo, "models"):
    Field = odoo.fields.Field
    BaseModel = odoo.models.BaseModel
    AnyModel = None
else:
    Field = None
    BaseModel = None
    AnyModel = None


__all__ = (
    "MYPY",
    "PY3",
    "abc",
    "urlparse",
    "odoo",
    "t",
    "cast",
    "overload",
    "Text",
    "TextLike",
    "Unicode",
    "builtins",
    "Field",
    "BaseModel",
    "AnyModel",
    "StringIO",
)
