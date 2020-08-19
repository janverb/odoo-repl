"""Small utility functions."""

from __future__ import unicode_literals

import collections
import contextlib
import itertools
import keyword
import string

import odoo_repl

from odoo_repl.imports import (
    t,
    overload,
    odoo,
    MYPY,
    Field,
    PY3,
    BaseModel,
    AnyModel,
    cast,
)


# Globally accessible environment. Use sparingly.
env = None  # type: odoo.api.Environment  # type: ignore


def module(cls):
    # type: (t.Type[BaseModel]) -> str
    return getattr(cls, "_module", cls.__name__)  # type: ignore


if MYPY:
    _XmlId = t.NamedTuple("XmlId", [("module", t.Text), ("name", t.Text)])
else:
    _XmlId = collections.namedtuple("XmlId", ("module", "name"))


class XmlId(_XmlId):
    __slots__ = ()

    def __str__(self):
        # type: () -> str
        return str(".".join(self))

    def to_ref(self):
        # type: () -> t.Text
        if not (is_name(self.module) and is_name(self.name)):
            return "ref({!r})".format(str(self))
        return "ref.{}.{}".format(self.module, self.name)


IDENT_CHARS = set(string.ascii_letters + string.digits + "_")


def is_name(ident):
    # type: (t.Text) -> bool
    if not ident:
        return False
    if keyword.iskeyword(ident):
        return False
    if PY3:
        return ident.isidentifier()
    else:
        return set(ident) <= IDENT_CHARS and not ident[0].isdigit()


def xml_ids(obj):
    # type: (BaseModel) -> t.List[XmlId]
    """Return all of a record's XML ids.

    .get_external_id() returns at most one result per record.
    """
    ids = [
        XmlId(data_record.module, data_record.name)
        for data_record in (
            obj.env["ir.model.data"]
            .sudo()
            .search([("model", "=", obj._name), ("res_id", "in", obj.ids)])
        )
        if data_record.module != "__export__"
    ]
    # Note: checking that obj is not empty prevents infinite recursion
    # It's not a silly optimization
    if obj and obj._name == "ir.ui.view":
        obj = cast("odoo.models.IrUiView", obj)
        # find_record_source uses xml_ids, so adding these here means they're
        # available downstream in record_repr, .source_(), .edit_(), etc.
        heirs = obj.mapped("inherit_children_ids").filtered(
            lambda view: view.mode == "extension"
        )
        ids.extend(xml_ids(heirs))
    return ids


def xml_id_tag(obj):
    # type: (BaseModel) -> t.Text
    """Return an affix for an object's XML ID, if it has one."""
    ids = xml_ids(obj)
    if ids:
        return " ({})".format(ids[0].to_ref())
    return ""


def unpack_function(func):
    # type: (t.Any) -> t.Callable[..., t.Any]
    """Remove wrappers to get the real function."""
    while True:
        for attr in "_orig", "__wrapped__", "__func__":
            if hasattr(func, attr):
                func = getattr(func, attr)
                break
        else:
            break
    return func  # type: ignore


_savepoint_count = itertools.count()


@contextlib.contextmanager
def savepoint(cr):
    # type: (odoo.sql_db.Cursor) -> t.Iterator[t.Text]
    """Make a savepoint for a cursor, with rollback if an exception happens.

    Note: SQL-related exceptions should be caught outside the ``with`` block,
    or they'll leave the cursor in an aborted state.
    """
    name = "odoo_repl_savepoint_{}".format(next(_savepoint_count))
    cr.execute("SAVEPOINT {}".format(name))
    try:
        yield name
    except Exception:
        cr.execute("ROLLBACK TO SAVEPOINT {}".format(name))
        raise
    else:
        cr.execute("RELEASE SAVEPOINT {}".format(name))


def sql(env_, query, *args):
    # type: (odoo.api.Environment, t.Text, object) -> t.List[t.Any]
    """Execute a SQL query and make the result more convenient.

    The query is executed with a savepoint and rolled back if necessary.
    """
    cr = env_.cr._obj  # Avoid logging
    with savepoint(cr):
        cr.execute(query, args)
        result = cr.fetchall()
    if result and len(result[0]) == 1:
        result = [row[0] for row in result]
    return result


if MYPY:
    T = t.TypeVar("T", BaseModel, Field, t.Callable[..., t.Any])


@overload
def unwrap(obj):
    # type: (odoo_repl.models.ModelProxy) -> BaseModel
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (odoo_repl.fields.FieldProxy) -> Field
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (odoo_repl.methods.MethodProxy) -> t.Callable[..., t.Any]
    pass


@overload  # noqa: F811
def unwrap(obj):
    # type: (T) -> T
    pass


def unwrap(obj):  # noqa: F811
    # type: (object) -> object
    if isinstance(
        obj,
        (
            odoo_repl.models.ModelProxy,
            odoo_repl.methods.MethodProxy,
            odoo_repl.fields.FieldProxy,
        ),
    ):
        obj = obj._real
    return obj


_base_url = None


def get_base_url():
    # type: () -> t.Text
    global _base_url
    if not _base_url:
        base_url = env["ir.config_parameter"].get_param("web.base.url")
        if not base_url or "localhost" in base_url:
            port = (
                odoo.tools.config.get("xmlrpc_port")
                or odoo.tools.config.get("http_port")
                or "8069"
            )
            base_url = "http://localhost:{}".format(port)
        _base_url = base_url
    return _base_url  # type: ignore


def generate_url(**params):
    # type: (object) -> t.Text
    return "{}/web?debug=1#{}".format(
        get_base_url(),
        "&".join("{}={}".format(key, value) for key, value in params.items()),
    )


def link_for_record(obj):
    # type: (BaseModel) -> t.Text
    return generate_url(model=obj._name, id=obj.id)


def is_record(obj):
    # type: (object) -> bool
    """Return whether an object is an Odoo record."""
    return isinstance(obj, BaseModel) and hasattr(obj, "_ids")


if MYPY:
    C = t.TypeVar("C", bound=t.Callable[..., object])


def patch(cls, name=None, func=None):
    # type: (t.Type[object], t.Optional[str], t.Any) -> t.Callable[[C], C]
    def decorator(method):
        # type: (C) -> C
        if cls is not None:
            setattr(cls, name if name is not None else method.__name__, method)
        return method

    if func is not None:
        decorator(func)
    return decorator


def with_user(record, user):
    # type: (AnyModel, odoo.models.ResUsers) -> AnyModel
    """Like .sudo() in Odoo <=12 and .with_user() in Odoo 13+."""
    if odoo.release.version_info >= (13, 0):
        return record.with_user(user)
    return record.sudo(user)


def loosely_callable(obj):
    # type: (object) -> bool
    """Like callable(), but tolerates classmethods and staticmethods."""
    return callable(obj) or isinstance(obj, (classmethod, staticmethod))


if PY3:

    def try_decode(text):
        # type: (str) -> str
        return text


else:

    def try_decode(text):
        # type: (t.Union[bytes, t.Text]) -> t.Text
        if not isinstance(text, bytes):
            return text
        return text.decode("utf8", errors="replace")
