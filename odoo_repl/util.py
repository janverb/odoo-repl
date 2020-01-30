"""Small utility functions."""

from __future__ import unicode_literals

import contextlib
import itertools

import odoo_repl

from odoo_repl.imports import t, overload, odoo, MYPY


def module(cls):
    # type: (t.Type[odoo.models.BaseModel]) -> t.Text
    return getattr(cls, "_module", cls.__name__)  # type: ignore


def xml_ids(obj):
    # type: (odoo.models.BaseModel) -> t.List[t.Tuple[t.Text, t.Text]]
    """Return all of a record's XML ids.

    .get_external_id() returns at most one result per record.
    """
    return [
        (data_record.module, data_record.name)
        for data_record in odoo_repl.env["ir.model.data"].search(
            [("model", "=", obj._name), ("res_id", "=", obj.id)]
        )
        if data_record.module != "__export__"
    ]


def unpack_function(func):
    # type: (t.Any) -> t.Callable[..., t.Any]
    """Remove wrappers to get the real function."""
    while hasattr(func, "_orig"):
        func = func._orig
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    if hasattr(func, "__func__"):
        func = func.__func__
    return func  # type: ignore


_savepoint_count = itertools.count()


@contextlib.contextmanager
def savepoint(cr):
    # type: (odoo.sql_db.Cursor) -> t.Iterator[t.Text]
    name = "odoo_repl_savepoint_{}".format(next(_savepoint_count))
    cr.execute("SAVEPOINT {}".format(name))
    try:
        yield name
    except Exception:
        cr.execute("ROLLBACK TO SAVEPOINT {}".format(name))
        raise
    else:
        cr.execute("RELEASE SAVEPOINT {}".format(name))


if MYPY:
    T = t.TypeVar("T", odoo.models.BaseModel, odoo.fields.Field, t.Callable[..., t.Any])


@overload
def unwrap(obj):
    # type: (odoo_repl.ModelProxy) -> odoo.models.BaseModel
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (odoo_repl.FieldProxy) -> odoo.fields.Field
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (odoo_repl.MethodProxy) -> t.Callable[..., t.Any]
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (T) -> T
    pass


def unwrap(obj):  # noqa: F811
    # type: (object) -> object
    if isinstance(
        obj, (odoo_repl.ModelProxy, odoo_repl.MethodProxy, odoo_repl.FieldProxy)
    ):
        obj = obj._real
    return obj
