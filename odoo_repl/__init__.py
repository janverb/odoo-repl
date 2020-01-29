# -*- coding: utf-8 -*-
# TODO:
# - access rights?
# - FieldProxy to be able to follow e.g. res.users.log_ids.create_date
# - refactor into submodules
# - list methods in model_repr
# - group by implementing module in model_repr
# - `addons`/`modules` browser object
#   - addons.mail.[tests|controllers|models|...]
#   - properly sort even for non-standard directory structure
#   - method for running tests, either entire module or more granular
# - show required fields in model summary (shorthand notation?)
# - .write_()
# - rename without "odoo" (trademark? CONTRIBUTING.rst#821naming)
# - don't treat mixins as base
# - things like constrainers as attributes on field(proxy)
# - unify .source_() and .edit_() more so you can e.g. do .source_(-1)
# - show .search in field_repr/as attr on FieldProxy
#   - this is safe, but doesn't stop the transaction from being aborted
# - at least document optional bs4 and pygments dependencies
# - put shuf_() on BaseModel
# - toggle to start pdb on log message (error/warning/specific message)
# - grep_ on XML records, for completeness
# - make separate threads use separate cursors

# hijack `odoo-bin shell`:
# - write to its stdin and somehow hook it back up to a tty
#   - how to hook it back up?
# - use --shell-interface and hijack with an import and PYTHONPATH
#   - but what if we actually want to use ipython/btpython/whatever?
# - use --shell-interface=__eq__ and run with `python -i`
#   - how to execute code when it's done?
# - use --shell-interface=python and hijack the `code` module with PYTHONPATH
#   - do other modules depend on `code`?
# - import, monkeypatch, then start normal odoo-bin shell entrypoint
#   - probably the cleanest

# add tests:
# - module odoo_repl.tests
# - CLI flag to run odoo_repl.tests.run_tests(); sys.exit()
# - checks for Odoo version, presence of demo data, etc.
# - model after TransactionCase, with new namespace + transaction for each test

from __future__ import print_function

import atexit
import collections
import contextlib
import importlib
import inspect
import itertools
import keyword
import linecache
import logging
import os
import pprint
import random
import re
import string
import shlex
import subprocess
import sys
import textwrap
import threading
import types

from datetime import datetime, date

from odoo_repl import color
from odoo_repl.imports import (
    MYPY,
    PY3,
    abc,
    odoo,
    t,
    overload,
    Text,
    TextLike,
    builtins,
    urlparse,
    which,
)
from odoo_repl.opdb import set_trace, post_mortem, pm

if MYPY:
    import bs4

__all__ = ("odoo_repr", "enable", "set_trace", "post_mortem", "pm")

env = None  # type: odoo.api.Environment  # type: ignore
edit_bg = False

RE_FIELD = re.compile(
    r"""
    ^\s*             # leading whitespace from the start of the line
    ['"]?(\w+)['"]?  # field name, quoted if key in a _columns dict
    \s*[:=]\s*       # : for an old-style dict, = for new-style assignment
    fields\.         # assume "from odoo import fields"
    (\w+)\(          # a single attribute deep, to avoid
                     # "date = fields.date.today()" false positive
    """,
    re.VERBOSE,
)


FIELD_BLACKLIST = {
    # These are on all models
    "__last_update",
    "display_name",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
    "id",
    # Showing these by default feels icky
    "password",
    "password_crypt",
}

# Copied from odoo.tools.convert.xml_import.__init__ (Odoo 8)
# There may be false positives, I didn't check them all
RECORD_TAGS = [
    "record",
    "delete",
    "function",
    "menuitem",
    "template",
    "workflow",
    "report",
    "ir_set",
    "act_window",
    "url",
    "assert",
]


def parse_config(argv):
    # type: (t.List[t.Text]) -> None
    """Set up odoo.tools.config from command line arguments."""
    logging.getLogger().handlers = []
    odoo.netsvc._logger_init = False
    odoo.tools.config.parse_config(argv)


def create_namespace(
    db,  # type: t.Union[None, t.Text, odoo.sql_db.Cursor, odoo.api.Environment]
):
    # type: (...) -> t.Tuple[odoo.api.Environment, t.Dict[t.Text, t.Any]]
    global env  # TODO

    if db is None or isinstance(db, Text):
        db_name = db or odoo.tools.config["db_name"]
        if not db_name:
            raise ValueError(
                "Can't determine database name. Run with `-d dbname` "
                "or pass it as the first argument to odoo_repl.enable()."
            )
        cursor = odoo.sql_db.db_connect(db_name).cursor()
        env = odoo.api.Environment(cursor, odoo.SUPERUSER_ID, {})
        atexit.register(cursor.close)
    elif isinstance(db, odoo.sql_db.Cursor):
        env = odoo.api.Environment(db, odoo.SUPERUSER_ID, {})
    elif isinstance(db, odoo.api.Environment):
        env = db
    else:
        raise TypeError(db)

    envproxy = EnvProxy()

    namespace = {
        "self": env.user,
        "odoo": odoo,
        "openerp": odoo,
        "browse": browse,
        "sql": sql,
        "grep_": grep_,
        "translate": translate,
        "env": envproxy,
        "u": UserBrowser(),
        "emp": EmployeeBrowser(),
        "ref": DataBrowser(),
        "addons": AddonBrowser(),
    }  # type: t.Dict[t.Text, t.Any]
    namespace.update({part: ModelProxy(part) for part in envproxy._base_parts()})
    if _xml_records is None:
        xml_thread = threading.Thread(target=xml_records)
        xml_thread.daemon = True
        xml_thread.start()
    return env, namespace


def enable(db=None, module_name=None, color=True, bg_editor=False):
    """Enable all the bells and whistles.

    :param db: Either an Odoo environment object, an Odoo cursor, a database
               name, or ``None`` to guess the database to use.
    :param module_name: Either a module, the name of a module, or ``None`` to
                        install into the module of the caller.
    :param bool color: Enable colored output.
    :param bool bg_editor: Don't wait for text editors invoked by ``.edit()``
                           to finish.
    """
    global edit_bg

    if module_name is None:
        try:
            module_name = sys._getframe().f_back.f_globals["__name__"]
        except Exception:
            pass
        if module_name in {None, "odoo_repl"}:
            print("Warning: can't determine module_name, assuming '__main__'")
            module_name = "__main__"

    if module_name in {"__builtin__", "builtins"}:
        __main__ = builtins
    elif isinstance(module_name, Text):
        __main__ = importlib.import_module(module_name)
    else:
        __main__ = module_name

    env_, to_install = create_namespace(db)

    atexit.register(env_.cr.close)

    edit_bg = bg_editor

    sys.displayhook = displayhook

    # Whenever this would be useful you should probably just use OPdb directly
    # But maybe there are cases in which it's hard to switch out pdb
    # TODO: It should probably run iff odoo_repl.enable() is called from pdb

    # pdb.Pdb.displayhook = OPdb.displayhook

    for name, obj in to_install.items():
        if not hasattr(builtins, name) and not hasattr(__main__, name):
            setattr(__main__, name, obj)

    if not color:
        color.enabled = False


def _color_repr(owner, field_name):
    # type: (odoo.models.BaseModel, t.Text) -> t.Text
    """Return a color-coded representation of a record's field value."""
    # TODO: refactor, move most to odoo_repl.color
    if hasattr(owner.env, "prefetch"):  # Not all Odoo versions
        # The prefetch cache may be filled up by previous calls, see record_repr
        owner.env.prefetch.clear()
    try:
        obj = getattr(owner, field_name)
    except Exception as err:
        return color.missing(repr(err))
    field_type = owner._fields[field_name].type
    if obj is False and field_type != "boolean" or obj is None:
        return color.missing(repr(obj))
    elif isinstance(obj, bool):
        # False shows up as green if it's a Boolean, and red if it's a
        # default value, so red values always mean "missing"
        return color.boolean(repr(obj))
    elif _is_record(obj):
        if not obj._ids:
            return color.missing("{}[]".format(obj._name))
        if len(obj._ids) > 10:
            return color.record(u"{} × {}".format(obj._name, len(obj._ids)))
        try:
            if obj._name == "res.users":
                return ", ".join(
                    color.record(UserBrowser._repr_for_value(user.login))
                    if user.login and user.active
                    else color.record("res.users[{}]".format(user.id))
                    for user in obj
                )
            elif obj._name == "hr.employee":
                return ", ".join(
                    color.record(EmployeeBrowser._repr_for_value(em.user_id.login))
                    if (
                        em.active
                        and em.user_id
                        and em.user_id.login
                        and len(em.user_id.employee_ids) == 1
                    )
                    else color.record("hr.employee[{}]".format(em.id))
                    for em in obj
                )
        except Exception:
            pass
        return color.record("{}[{}]".format(obj._name, _ids_repr(obj._ids)))
    elif isinstance(obj, TextLike):
        if len(obj) > 120:
            return color.string(repr(obj)[:120] + "...")
        return color.string(repr(obj))
    elif isinstance(obj, (datetime, date)):
        # Blue for consistency with versions where they're strings
        return color.string(str(obj))
    elif isinstance(obj, (int, float)):
        return color.number(repr(obj))
    else:
        return repr(obj)


if MYPY:
    T = t.TypeVar("T", odoo.models.BaseModel, odoo.fields.Field, t.Callable)


@overload
def _unwrap(obj):
    # type: (ModelProxy) -> odoo.models.BaseModel
    pass


@overload  # noqa: F811
def _unwrap(obj):
    # type: (FieldProxy) -> odoo.fields.Field
    pass


@overload  # noqa: F811
def _unwrap(obj):
    # type: (MethodProxy) -> t.Callable
    pass


@overload  # noqa: F811
def _unwrap(obj):
    # type: (T) -> T
    pass


def _unwrap(obj):  # noqa: F811
    if isinstance(obj, (ModelProxy, MethodProxy, FieldProxy)):
        obj = obj._real
    return obj


def odoo_repr(obj):
    # type: (object) -> t.Text
    if isinstance(obj, ModelProxy):
        return model_repr(obj)
    elif isinstance(obj, MethodProxy):
        return method_repr(obj)
    elif isinstance(obj, FieldProxy):
        return field_repr(obj)
    elif isinstance(obj, odoo.models.BaseModel):
        return record_repr(obj)
    elif isinstance(obj, (odoo.fields.Field, FieldProxy)):
        return field_repr(obj)
    elif isinstance(obj, Addon):
        return str(obj)
    else:
        return repr(obj)


def odoo_print(obj, **kwargs):
    # type: (t.Any, t.Any) -> None
    if _is_record(obj) and len(obj) > 1:
        print("\n\n".join(record_repr(record) for record in obj), **kwargs)
    else:
        print(odoo_repr(obj), **kwargs)


def _fmt_properties(field):
    # type: (odoo.fields.Field) -> t.Text
    return "".join(
        attr[0] if getattr(field, attr, False) else " "
        for attr in ["required", "store", "default"]
    ) + ("c" if _find_computer(field) else " ")


def model_repr(obj):
    # type: (t.Union[ModelProxy, odoo.models.BaseModel]) -> t.Text
    """Summarize a model's fields."""
    if isinstance(obj, ModelProxy) and obj._real is None:
        return repr(obj)
    obj = _unwrap(obj)

    fields = []
    delegated = []
    for field in sorted(obj._fields):
        if field in FIELD_BLACKLIST:
            continue
        if getattr(obj._fields[field], "related", False):
            delegated.append(obj._fields[field])
            continue
        fields.append(field)
    max_len = max(len(f) for f in fields) if fields else 0
    parts = []

    parts.append(color.header(obj._name))
    if getattr(obj, "_description", False):
        parts.append(obj._description)
    if getattr(obj, "_inherits", False):
        for model_name, field_name in obj._inherits.items():
            parts.append(
                "Inherits from {} through {}".format(
                    color.model(model_name), color.field(field_name)
                )
            )
    for field in fields:
        f_obj = obj._fields[field]
        parts.append(
            color.blue.bold(_fmt_properties(f_obj))
            + " {}: ".format(color.field(field))
            # Like str.ljust, but not confused about colors
            + (max_len - len(field)) * " "
            + color.color_field(f_obj)
            + " ({})".format(f_obj.string)
        )
    if delegated:
        buckets = collections.defaultdict(
            list
        )  # type: t.DefaultDict[t.Tuple[t.Text, ...], t.List[t.Text]]
        for f_obj in delegated:
            assert f_obj.related
            buckets[tuple(f_obj.related[:-1])].append(
                color.field(f_obj.name)
                if f_obj.related[-1] == f_obj.name
                else "{} (.{})".format(color.field(f_obj.name), f_obj.related[-1])
            )
        parts.append("")
        for related_field, field_names in buckets.items():
            # TODO: figure out name of model of real field
            parts.append(
                "Delegated to {}: {}".format(
                    color.yellow.bold(".".join(related_field)), ", ".join(field_names)
                )
            )
    parts.append("")
    parts.extend(_format_sources(_find_source(obj)))
    return "\n".join(parts)


def _xml_ids(obj):
    # type: (odoo.models.BaseModel) -> t.List[t.Tuple[t.Text, t.Text]]
    # .get_external_id() returns at most one result per record
    return [
        (data_record.module, data_record.name)
        for data_record in env[u"ir.model.data"].search(
            [("model", "=", obj._name), ("res_id", "=", obj.id)]
        )
        if data_record.module != "__export__"
    ]


def _xml_id_tag(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    return "".join(
        " (ref.{}.{})".format(module, name) for module, name in _xml_ids(obj)
    )


def _record_header(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    header = color.header("{}[{!r}]".format(obj._name, obj.id)) + _xml_id_tag(obj)
    if obj.env.uid != 1:
        header += " (as {})".format(UserBrowser._repr_for_value(obj.env.user.login))
    return header


def _ids_repr(idlist):
    # type: (t.Iterable[object]) -> t.Text
    fragments = []
    news = 0
    for ident in idlist:
        if isinstance(ident, int):
            fragments.append(str(ident))
        else:
            news += 1
    if news:
        if news == 1:
            fragments.append("NewId")
        else:
            fragments.append(u"NewId × {}".format(news))
    return ", ".join(fragments)


def record_repr(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    """Display all of a record's fields."""
    obj = _unwrap(obj)

    if not obj:
        return "{}[]".format(obj._name)
    elif len(obj) > 1:
        return "{}[{}]".format(obj._name, _ids_repr(obj._ids))

    if obj.env.cr.closed:
        return "{}[{}] (closed cursor)".format(obj._name, _ids_repr(obj._ids))

    fields = sorted(
        field
        for field in obj._fields
        if field not in FIELD_BLACKLIST and not obj._fields[field].related
    )
    max_len = max(len(f) for f in fields) if fields else 0
    parts = []

    parts.append(_record_header(obj))
    parts.append(color.display_name(obj.display_name))

    if not obj.exists():
        parts.append(color.missing("Missing"))
        return "\n".join(parts)

    # Odoo precomputes a field for up to 200 records at a time.
    # This can be a problem if we're only interested in one of them.
    # The solution: do everything in a separate env where the ID cache is
    # empty.
    no_prefetch_obj = obj.with_context(odoo_repl=True)
    for field in fields:
        parts.append(
            "{}: ".format(color.field(field))
            + (max_len - len(field)) * " "
            + _color_repr(no_prefetch_obj, field)
        )

    if _xml_records is not None:
        sources = _find_source(obj)
        if sources:
            parts.append("")
            parts.extend(_format_sources(sources))

    return "\n".join(parts)


def _find_computer(field):
    # type: (odoo.fields.Field) -> object
    if field.compute is not None:
        func = field.compute
        func = getattr(func, "__func__", func)
        if isinstance(func, Text):
            func = getattr(env[field.model_name], func)
        return func
    elif type(getattr(field, "column", None)).__name__ == "function":
        return field.column._fnct
    return None


def _decipher_lambda(func):
    # type: (types.FunctionType) -> t.Text
    """Try to retrieve a lambda's source code. Very nasty.

    Signals failure by throwing random exceptions.
    """
    source = inspect.getsource(func)
    source = re.sub(r" *\n *", " ", source).strip()
    match = re.search("lambda [^:]*:.*", source)
    if not match:
        raise RuntimeError
    source = match.group().strip()
    try:
        compile(source, "", "eval")
    except SyntaxError as err:
        assert err.offset is not None
        source = source[: err.offset - 1].strip()
        if re.search(r",[^)]*$", source):
            source = source.rsplit(",")[0].strip()
        compile(source, "", "eval")
    return source


def _find_field_default(field):
    # type: (odoo.fields.Field) -> object
    model = env[field.model_name]
    # TODO: was the commented out code useful?
    if hasattr(model, "_defaults"):  # and not callable(model._defaults[field.name]):
        default = model._defaults[field.name]
    elif field.default:
        default = field.default
    else:
        return None

    try:
        # Very nasty but works some of the time
        # Hopefully something better exists
        if (
            isinstance(default, types.FunctionType)
            and default.__module__ in {"odoo.fields", "openerp.fields"}
            and default.__name__ == "<lambda>"
            and "value" in default.__code__.co_freevars
            and default.__closure__
        ):
            default = default.__closure__[
                default.__code__.co_freevars.index("value")
            ].cell_contents
    except Exception:
        pass

    return default


def field_repr(field):
    # type: (t.Union[FieldProxy, odoo.fields.Field]) -> t.Text
    """List detailed information about a field."""
    # TODO:
    # - .groups, .copy, .states, .inverse, .column[12]
    field = _unwrap(field)
    model = env[field.model_name]
    record = env[u"ir.model.fields"].search(
        [("model", "=", field.model_name), ("name", "=", field.name)]
    )
    parts = []  # type: t.List[t.Text]
    parts.append(
        "{} {} on {}".format(
            color.blue.bold(record.ttype),
            color.field(record.name),
            color.model(record.model),
        )
    )
    if record.relation:
        parts[-1] += " to {}".format(color.model(record.relation))

    properties = [
        attr
        for attr in (
            "readonly",
            "required",
            "store",
            "index",
            "auto_join",
            "compute_sudo",
            "related_sudo",
            "translate",
        )
        if getattr(field, attr, False)
    ]
    if properties:
        parts[-1] += " ({})".format(", ".join(properties))

    parts.append(record.field_description)
    if field.help:
        if "\n" in field.help:
            parts.append(field.help)
        else:
            parts[-1] += ": " + field.help

    if field.related:
        parts.append("Delegated to {}".format(color.field(".".join(field.related))))
    elif getattr(field, "column", False) and type(field.column).__name__ == "related":
        parts.append("Delegated to {}".format(color.field(".".join(field.column.arg))))
    else:
        func = _find_computer(field)
        if getattr(func, "__name__", None) == "<lambda>":
            assert isinstance(func, types.FunctionType)
            try:
                func = _decipher_lambda(func)
            except Exception:
                pass
        if callable(func):
            func = getattr(func, "__name__", func)
        if func:
            parts.append("Computed by {}".format(color.method(str(func))))

    if getattr(model, "_constraint_methods", False):
        for constrainer in model._constraint_methods:
            if field.name in constrainer._constrains:
                parts.append(
                    "Constrained by {}".format(
                        color.method(getattr(constrainer, "__name__", constrainer))
                    )
                )

    if getattr(field, "inverse_fields", False):
        parts.append(
            "Inverted by {}".format(
                ", ".join(color.field(inv.name) for inv in field.inverse_fields)
            )
        )

    if field.default:
        default = _find_field_default(field)

        show_literal = False

        if getattr(default, "__module__", None) in {"odoo.fields", "openerp.fields"}:
            default = color.purple.bold("(Unknown)")
            show_literal = True

        try:
            if getattr(default, "__name__", None) == "<lambda>":
                assert isinstance(default, types.FunctionType)
                source = _decipher_lambda(default)
                default = color.purple.bold(source)
                show_literal = True
        except Exception:
            pass

        if callable(default):
            default = color.method(getattr(default, "__name__", str(default)))
            show_literal = True

        if show_literal:
            parts.append("Default value: {}".format(default))
        else:
            parts.append("Default value: {!r}".format(default))

    if record.ttype == "selection":
        parts.append(pprint.pformat(field.selection))

    sources = _find_source(field)
    parts.extend(_format_sources(sources))

    if not sources and record.modules:
        parts.append(
            "Defined in module {}".format(
                ", ".join(color.module(module) for module in record.modules.split(", "))
            )
        )

    return "\n".join(parts)


def _find_decorators(method):
    # type: (t.Any) -> t.Iterator[t.Text]
    if hasattr(method, "_constrains"):
        yield color.decorator("@api.constrains") + "({})".format(
            ", ".join(map(repr, method._constrains))
        )
    if hasattr(method, "_depends"):
        if callable(method._depends):
            yield color.decorator("@api.depends") + "({!r})".format(method._depends)
        else:
            yield color.decorator("@api.depends") + "({})".format(
                ", ".join(map(repr, method._depends))
            )
    if hasattr(method, "_onchange"):
        yield color.decorator("@api.onchange") + "({})".format(
            ", ".join(map(repr, method._onchange))
        )
    if getattr(method, "_api", False):
        api = method._api
        yield color.decorator("@api.{}".format(api.__name__ if callable(api) else api))
    if not hasattr(method, "__self__"):
        yield color.decorator("@staticmethod")
    elif isinstance(method.__self__, type):
        yield color.decorator("@classmethod")


def _unpack_function(func):
    # type: (t.Any) -> t.Callable
    while hasattr(func, "_orig"):
        func = func._orig
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    if hasattr(func, "__func__"):
        func = func.__func__
    return func


def _func_signature(func):
    # type: (t.Callable) -> t.Text
    if PY3:
        return str(inspect.signature(func))
    else:
        return inspect.formatargspec(*inspect.getargspec(func))


def method_repr(methodproxy):
    # type: (MethodProxy) -> t.Text
    sources = _find_method_source(methodproxy)
    model = methodproxy.model
    name = methodproxy.name

    method = methodproxy._real
    decorators = list(_find_decorators(method))
    method = _unpack_function(method)

    signature = _func_signature(method)
    doc = inspect.getdoc(method)  # type: t.Optional[t.Text]
    if not doc:
        # inspect.getdoc() can't deal with Odoo's unorthodox inheritance
        for cls in type(model).mro():
            if name in vars(cls):
                doc = inspect.getdoc(vars(cls)[name])
            if doc:
                break
    if not PY3 and isinstance(doc, str):
        # Sometimes people put unicode in non-unicode docstrings
        # Probably in other places too, but here is where I found out the hard way
        # unicode.join does not like non-ascii strs so this has to be early
        try:
            # everybody's source code is UTF-8-compatible, right?
            doc = doc.decode("utf8")
        except UnicodeDecodeError:
            # Let's just hope for the best
            pass
    parts = []
    parts.extend(decorators)
    parts.append(
        "{model}.{name}{signature}".format(
            model=color.model(model._name), name=color.method(name), signature=signature
        )
    )
    if doc:
        parts.append(doc)
    parts.append("")
    parts.extend(_format_sources(sources))
    return "\n".join(parts)


def edit(thing, index=-1, bg=None):
    """Open a model or field definition in an editor."""
    # TODO: editor kwarg and/or argparse flag
    if bg is None:
        bg = edit_bg
    sources = _find_source(thing)
    if not sources:
        raise RuntimeError("Can't find source file!")
    if isinstance(index, int):
        try:
            module, fname, lnum = sources[index]
        except IndexError:
            raise RuntimeError("Can't find match #{}".format(index))
    elif isinstance(index, Text):
        for module, fname, lnum in sources:
            if module == index:
                break
        else:
            raise RuntimeError("Can't find match for module {!r}".format(index))
    else:
        raise TypeError(index)
    # $EDITOR could be an empty string
    argv = (os.environ.get("EDITOR") or "nano").split()
    if lnum is not None:
        argv.append("+{}".format(lnum))
    argv.append(fname)
    if bg:
        # os.setpgrp avoids KeyboardInterrupt/SIGINT
        subprocess.Popen(argv, preexec_fn=os.setpgrp)
    else:
        subprocess.Popen(argv).wait()


def _format_source(source):
    # type: (Source) -> t.Text
    module, fname, lnum = source
    if lnum is not None:
        return "{}: {}:{}".format(color.module(module), fname, lnum)
    else:
        return "{}: {}".format(color.module(module), fname)


def _format_sources(sources):
    # type: (t.Iterable[Source]) -> t.List[t.Text]
    return [_format_source(source) for source in sources]


def _module(cls):
    # type: (t.Type) -> t.Text
    return getattr(cls, "_module", cls.__name__)


class Source(collections.namedtuple("Source", ("module", "fname", "lnum"))):
    __slots__ = ()

    @classmethod
    def from_cls(cls, src_cls):
        # type: (t.Type) -> Source
        return cls(
            _module(src_cls),
            inspect.getsourcefile(src_cls),
            inspect.getsourcelines(src_cls)[1],
        )


def _get_source_loc(thing):
    # type: (t.Type) -> t.Tuple[t.Text, int]
    return inspect.getsourcefile(thing) or "???", inspect.getsourcelines(thing)[1]


def _find_source(
    thing,  # type: (t.Union[odoo.models.BaseModel, odoo.fields.Field, MethodProxy])
):
    # type: (...) -> t.List[Source]
    if isinstance(thing, odoo.models.BaseModel) and hasattr(thing, "_ids"):
        if not thing._ids:
            return _find_model_source(_unwrap(thing))
        else:
            return _find_record_source(thing)
    elif isinstance(thing, odoo.fields.Field):
        return _find_field_source(thing)
    elif isinstance(thing, MethodProxy):
        return _find_method_source(thing)
    else:
        raise TypeError(thing)


def _find_model_source(model):
    # type: (odoo.models.BaseModel) -> t.List[Source]
    return [
        Source.from_cls(cls)
        for cls in type(model).__bases__
        if cls.__module__ not in {"odoo.api", "openerp.api"}
    ]


def _find_record_source(record):
    # type: (odoo.models.BaseModel) -> t.List[Source]
    return [
        Source(defin.module, defin.fname, defin.elem.sourceline)
        for rec in record
        for rec_id in _xml_ids(rec)
        for defin in xml_records()[".".join(rec_id)]
    ]


def _find_field_source(field):
    # type: (odoo.fields.Field) -> t.List[Source]
    res = []
    for cls in type(env[field.model_name]).__bases__:
        if field.name in getattr(cls, "_columns", ()) or field.name in vars(cls):
            if cls.__module__ in {"odoo.api", "openerp.api"}:
                continue
            fname = inspect.getsourcefile(cls)
            lines, lnum = inspect.getsourcelines(cls)
            for line in lines:
                match = RE_FIELD.match(line)
                if match and match.group(1) == field.name:
                    break
                lnum += 1
            else:
                lnum = None  # type: ignore
            res.append(Source(_module(cls), fname, lnum))
    return res


def _find_method_source(method):
    # type: (MethodProxy) -> t.List[Source]
    res = []
    for cls in type(method.model).mro()[1:]:
        if method.name in vars(cls):
            func = _unpack_function(vars(cls)[method.name])
            res.append(
                Source(
                    _module(cls),
                    inspect.getsourcefile(func),
                    inspect.getsourcelines(func)[1],
                )
            )
    return res


def _BaseModel_repr_pretty_(self, printer, _cycle):
    # type: (odoo.models.BaseModel, t.Any, t.Any) -> None
    if printer.indentation == 0 and hasattr(self, "_ids"):
        printer.text(record_repr(self))
    else:
        printer.text(repr(self))


def _Field_repr_pretty_(self, printer, _cycle):
    # type: (odoo.fields.Field, t.Any, t.Any) -> None
    if printer.indentation == 0 and hasattr(self, "model_name"):
        printer.text(field_repr(self))
    elif not hasattr(self, "model_name"):
        printer.text("<Undisplayable field>")  # Work around bug
    else:
        printer.text(repr(self))


def displayhook(obj):
    # type: (object) -> None
    """A sys.displayhook replacement that pretty-prints models and records."""
    if obj is not None:
        print(odoo_repr(obj))
        builtins._ = obj  # type: ignore


class EnvProxy(object):
    """A wrapper around an odoo.api.Environment object.

    Models returned by indexing will be wrapped in a ModelProxy for nicer
    behavior. Models can also be accessed as attributes, with tab completion.
    """

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError
        if hasattr(env, attr):
            return getattr(env, attr)
        if attr in self._base_parts():
            return ModelProxy(attr)
        raise AttributeError

    def __dir__(self):
        # type: () -> t.List[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"_base_parts"}  # type: t.Set[t.Text]
        listing.update(self._base_parts())
        listing.update(attr for attr in dir(env) if not attr.startswith("__"))
        return sorted(listing)

    def _base_parts(self):
        # type: () -> t.List[t.Text]
        return list({mod.split(".", 1)[0] for mod in env.registry})

    def __repr__(self):
        # type: () -> str
        return "{}({!r})".format(self.__class__.__name__, env)

    def __getitem__(self, ind):
        # type: (t.Text) -> ModelProxy
        if ind not in env.registry:
            raise KeyError("Model '{}' does not exist".format(ind))
        return ModelProxy(ind, nocomplete=True)

    def __iter__(self):
        # type: () -> t.Iterator[ModelProxy]
        for mod in env.registry:
            yield self[mod]

    def __eq__(self, other):
        # type: (object) -> bool
        return self.__class__ is other.__class__

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        return list(env.registry)


def _BaseModel_create_(
    self,  # type: odoo.models.BaseModel
    vals=None,  # type: t.Optional[t.Dict[str, t.Any]]
    **fields  # type: t.Any
):
    # type: (...) -> odoo.models.BaseModel
    """Create a new record, optionally with keyword arguments.

    .create_(x='test', y=<some record>) is typically equivalent to
    .create({"x": "test", "y": <some record>id}). 2many fields are also
    handled.

    If you make a typo in a field name you get a proper error.
    """
    if vals:
        fields.update(vals)
    for key, value in fields.items():
        if key not in self._fields:
            raise TypeError("Field '{}' does not exist".format(key))
        if _is_record(value) or (
            isinstance(value, (list, tuple)) and value and _is_record(value[0])
        ):
            # TODO: typecheck model
            field_type = self._fields[key].type
            if field_type.endswith("2many"):
                fields[key] = [(6, 0, value.ids)]
            elif field_type.endswith("2one"):
                if len(value) > 1:
                    raise TypeError("Can't link multiple records for '{}'".format(key))
                fields[key] = value.id
    return self.create(fields)


def _parse_search_query(
    args,  # type: t.Tuple[object, ...]
    fields,  # type: t.Mapping[str, object]
):
    # type: (...) -> t.List[t.Tuple[str, str, object]]
    clauses = []
    state = "OUT"
    curr = None  # type: t.Optional[t.List[t.Any]]
    for arg in args:
        if state == "OUT":
            if isinstance(arg, list):
                clauses.extend(arg)
            elif isinstance(arg, tuple):
                clauses.append(arg)
            else:
                assert curr is None
                state = "IN"
                if isinstance(arg, Text):
                    curr = arg.split(None, 2)
                else:
                    curr = [arg]
        elif state == "IN":
            assert curr is not None
            curr.append(arg)

        if curr and len(curr) >= 3:
            clauses.append(tuple(curr))
            state = "OUT"
            curr = None

    if state == "IN":
        assert isinstance(curr, list)
        raise ValueError(
            "Couldn't divide into leaves: {!r}".format(clauses + [tuple(curr)])
        )
    clauses.extend((k, "=", getattr(v, "id", v)) for k, v in fields.items())

    return clauses


def _BaseModel_search_(
    self,  # type: odoo.models.BaseModel
    *args,  # type: object
    **fields  # type: t.Any
):
    # type: (...) -> odoo.models.BaseModel
    # if count=True, this returns an int, but that may not be worth annotating
    """Perform a quick and dirty search.

    .search_(x='test', y=<some record>) is roughly equivalent to
    .search([('x', '=', 'test'), ('y', '=', <some record>.id)]).
    .search_() gets all records.
    """
    # TODO:
    # - inspect fields
    # - handle 2many relations
    offset = fields.pop("offset", 0)  # type: int
    limit = fields.pop("limit", None)  # type: t.Optional[int]
    order = fields.pop("order", "id")  # type: t.Optional[t.Text]
    count = fields.pop("count", False)  # type: bool
    shuf = fields.pop("shuf", None)  # type: t.Optional[int]
    if shuf and not (args or fields or offset or limit or count):
        # Doing a search seeds the cache with IDs, which tanks performance
        # Odoo will compute fields on many records at once even though you
        # won't use them
        query = "SELECT id FROM {}".format(self._table)
        if "active" in self._fields:
            query += " WHERE active = true"
        all_ids = sql(query)
        shuf = min(shuf, len(all_ids))
        return self.browse(random.sample(all_ids, shuf))
    clauses = _parse_search_query(args, fields)
    result = self.search(clauses, offset=offset, limit=limit, order=order, count=count)
    if shuf:
        shuf = min(shuf, len(result))
        return result.browse(random.sample(result._ids, shuf))
    return result


def _BaseModel_filtered_(self, func=None, **fields):
    """Filter based on field values in addition to the usual .filtered() features.

    .filtered_(state='done') is equivalent to
    .filtered(lambda x: x.state == 'done').
    """
    this = self
    if func:
        this = this.filtered(func)
    if fields:
        this = this.filtered(
            lambda record: all(
                getattr(record, field) == value for field, value in fields.items()
            )
        )
    return this


class ModelProxy(object):
    """A wrapper around an Odoo model.

    Records can be browsed with indexing syntax, other models can be used
    with tab-completed attribute access, there are added convenience methods,
    and instead of an ordinary repr a summary of the fields is shown.
    """

    def __init__(self, path, nocomplete=False):
        # type: (t.Text, bool) -> None
        self._path = path
        self._real = env[path] if path in env.registry else None
        if nocomplete and self._real is None:
            raise ValueError("Model '{}' does not exist".format(self._path))
        self._nocomplete = nocomplete

    def __getattr__(self, attr):
        # type: (t.Text) -> object
        if attr.startswith("__"):
            raise AttributeError
        if not self._nocomplete:
            new = self._path + "." + attr
            if new in env.registry:
                return self.__class__(new)
            if any(m.startswith(new + ".") for m in env.registry):
                return self.__class__(new)
        if self._real is None:
            raise AttributeError("Model '{}' does not exist".format(new))
        if attr in self._real._fields:
            return FieldProxy(self._real._fields[attr])
        thing = getattr(self._real, attr)
        if callable(thing) and hasattr(type(self._real), attr):
            thing = MethodProxy(thing, self._real, attr)
        return thing

    def __dir__(self):
        real_methods = {
            "shuf_",
            "mod_",
            "source_",
            "rules_",
            "view_",
            "sql_",
            "grep_",
            "methods_",
        }  # type: t.Set[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = real_methods.copy()
        if self._real is not None:
            listing.update(
                attr for attr in dir(self._real) if not attr.startswith("__")
            )
            # https://github.com/odoo/odoo/blob/5cdfd53d/odoo/models.py#L341 adds a
            # bogus attribute that's annoying for tab completion
            listing -= {"<lambda>"}
        else:
            listing -= real_methods
        # This can include entries that contain periods.
        # Both the default completer and IPython handle that well.
        listing.update(
            mod[len(self._path) + 1 :]
            for mod in env.registry
            if mod.startswith(self._path + ".")
        )
        return sorted(listing)

    def __iter__(self):
        # type: () -> t.Iterator[FieldProxy]
        assert self._real is not None
        for field in sorted(self._real._fields.values(), key=lambda f: f.name):
            yield FieldProxy(field)

    def __len__(self):
        # type: () -> int
        assert self._real is not None
        return self._real.search([], count=True)

    def mapped(self, *a, **k):
        # type: (t.Any, t.Any) -> t.Any
        assert self._real is not None
        return self._real.search([]).mapped(*a, **k)

    def filtered(self, *a, **k):
        # type: (t.Any, t.Any) -> odoo.models.BaseModel
        assert self._real is not None
        return self._real.search([]).filtered(*a, **k)

    def filtered_(self, *a, **k):
        # type: (t.Any, t.Any) -> odoo.models.BaseModel
        assert self._real is not None
        return self._real.search([]).filtered_(*a, **k)  # type: ignore

    def __repr__(self):
        if self._real is not None:
            return "{}[]".format(self._path)
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, _cycle):
        if self._real is not None and printer.indentation == 0:
            printer.text(model_repr(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(
        self, ind  # type: t.Union[t.Iterable[int], t.Text, int]
    ):
        # type: (...) -> t.Union[MethodProxy, FieldProxy, odoo.models.BaseModel]
        if self._real is None:
            raise KeyError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if isinstance(ind, Text):
            if ind in self._real._fields:
                return FieldProxy(self._real._fields[ind])
            thing = getattr(self._real, ind)
            if callable(thing):
                return MethodProxy(thing, self._real, ind)
            return thing
        if isinstance(ind, abc.Iterable):
            assert not isinstance(ind, Text)
            ind = tuple(ind)
        if not isinstance(ind, tuple):
            ind = (ind,)
        # Browsing a non-existent record can cause weird caching problems, so
        # check first
        real_ind = set(
            sql('SELECT id FROM "{}" WHERE id IN %s'.format(self._real._table), ind)
        )
        missing = set(ind) - real_ind
        if missing:
            raise KeyError(
                "Records {} do not exist".format(", ".join(map(str, missing)))
            )
        return self._real.browse(ind)

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        assert self._real is not None
        return list(self._real._fields)

    def _ensure_real(self):
        # type: () -> None
        if self._real is None:
            raise TypeError("Model '{}' does not exist".format(self._path))

    def _all_ids_(self):
        # type: () -> t.List[int]
        """Get all record IDs in the database."""
        self._ensure_real()
        return sql("SELECT id FROM {}".format(env[self._path]._table))

    def mod_(self):
        # type: () -> odoo.models.IrModel
        """Get the ir.model record of the model."""
        self._ensure_real()
        return env[u"ir.model"].search([("model", "=", self._path)])

    def shuf_(self, num=1):
        # type: (int) -> odoo.models.BaseModel
        """Return a random record, or multiple."""
        assert self._real is not None
        return _BaseModel_search_(self._real, shuf=num)

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        assert self._real is not None
        for cls in type(self._real).__bases__:
            name = getattr(cls, "_name", None)
            if location is not None and _module(cls) != location:
                continue
            if location is None and name != self._real._name:
                continue
            print(_format_source(Source.from_cls(cls)))
            print(color.highlight(inspect.getsource(cls)))

    def rules_(self, user=None):
        # type: (t.Optional[odoo.models.ResUsers]) -> None
        # TODO: is it possible to collapse the rules into a single policy for a user?
        print(
            "\n\n".join(
                [
                    _access_repr(access)
                    for access in env[u"ir.model.access"].search(
                        [("model_id", "=", self.mod_().id)]
                    )
                    if access.active
                    and not (
                        user is not None
                        and access.group_id
                        and not user.has_group(*access.group_id.get_xml_id().values())
                    )
                ]
                + [
                    _rule_repr(rule)
                    for rule in env[u"ir.rule"].search(
                        [("model_id", "=", self.mod_().id)]
                    )
                    if rule.active
                    and not (
                        user is not None
                        and rule.groups
                        and not any(
                            user.has_group(*group.get_xml_id().values())
                            for group in rule.groups
                        )
                    )
                ]
            )
        )

    def view_(
        self,
        user=None,  # type: t.Optional[t.Union[t.Text, int, odoo.models.ResUsers]]
        **kwargs  # type: t.Any
    ):
        # type: (...) -> t.Union[_PrettySoup, t.Text]
        """Build up a view as a user. Returns beautifulsoup of the XML.

        Takes the same arguments as ir.model.fields_view_get, notably
        view_id and view_type.
        """
        assert self._real is not None
        context = kwargs.pop("context", None)
        kwargs.setdefault("view_type", "form")
        model = self._real
        if user is not None:
            # TODO: handle viewing as group
            model = model.sudo(_to_user(user))
        if context is not None:
            model = model.with_context(context)
        form = model.fields_view_get(**kwargs)["arch"]
        return _PrettySoup._from_string(form)

    def sql_(self):
        # type: () -> None
        """Display basic PostgreSQL information about stored fields."""
        # TODO: make more informative
        assert self._real is not None
        with savepoint(env.cr):
            env.cr.execute("SELECT * FROM {} LIMIT 0;".format(self._real._table))
            columns = env.cr.description
        print(self._real._table)
        for name in sorted(c.name for c in columns):
            print("  {}".format(name))

    def grep_(self, *args, **kwargs):
        """grep through the combined source code of the model.

        Examples:
        >>> account.invoice.grep_("test")
        >>> account.invoice.grep_("-e", "foo", "-e", "bar")
        >>> account.invoice.grep_("test", A=5)  # grep -A 5
        >>> account.invoice.grep_("test", i=True)  # grep -i
        >>> account.invoice.grep_("test", max_count=3)  # grep --max-count 3

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
        assert self._real is not None
        # TODO: handle multiple classes in single file properly
        argv = _build_grep_argv(args, kwargs)
        argv.extend(fname for _module, fname, _lnum in _find_source(self._real))
        subprocess.Popen(argv).wait()

    def methods_(self):
        # type: () -> None
        self._ensure_real()
        for cls in type(self._real).__bases__:
            meths = [
                (name, attr)
                for name, attr in sorted(vars(cls).items())
                if callable(attr)
            ]
            if meths:
                print()
                print(color.module(_module(cls)))
                for name, meth in meths:
                    print(color.method(name) + _func_signature(_unpack_function(meth)))

    _ = _BaseModel_search_


def _to_user(user):
    # type: (t.Union[odoo.models.BaseModel, t.Text, int]) -> odoo.models.ResUsers
    if isinstance(user, Text):
        login = user
        user = env[u"res.users"].search([("login", "=", login)])
        if len(user) != 1:
            raise ValueError("No user {!r}".format(login))
        return user
    elif isinstance(user, int):
        return env[u"res.users"].browse(user)
    if not isinstance(user, odoo.models.BaseModel):
        raise ValueError("Can't convert type of {!r} to user".format(user))
    if user._name == "res.users":
        return user  # type: ignore
    candidate = getattr(user, "user_id", user)
    if getattr(candidate, "_name", None) != "res.users":
        raise ValueError("{!r} is not a user".format(candidate))
    return candidate


def _find_grep(default="grep"):
    # type: (t.Text) -> t.List[t.Any]
    """Look for a grep-like program to use."""
    user_conf = os.environ.get("ODOO_REPL_GREP")
    if user_conf:
        return shlex.split(user_conf)
    for prog in "rg", "ag", "ack":
        if which(prog):
            return [prog]
    # For disgusting technical reasons, default may not contain unicode in PY2
    return shlex.split(str(default))


def _build_grep_argv(args, kwargs):
    # type: (t.Iterable[str], t.Mapping[str, object]) -> t.List[t.Text]
    argv = _find_grep()
    if argv[0] == "grep" and color.enabled:
        argv.append("--color=auto")
    for key, value in kwargs.items():
        flag = "-" + key if len(key) == 1 else "--" + key.replace("_", "-")
        argv.append(flag)
        if value is not True:
            argv.append(str(value))
    argv.extend(args)
    argv.append("--")
    return argv


class _PrettySoup(object):
    """A wrapper around beautifulsoup tag soup to make the repr pretty.

    See https://www.crummy.com/software/BeautifulSoup/bs4/doc/ for more useful
    things to do.
    """

    def __init__(self, soup):
        # type: (bs4.BeautifulSoup) -> None
        self._real = soup

    @classmethod
    def _from_string(cls, text):
        try:
            # Requires bs4 and lxml
            import bs4

            return cls(bs4.BeautifulSoup(text, "xml"))
        except ImportError as err:
            print("Couldn't soupify XML: {}".format(err))
            return text

    def __getattr__(self, attr):
        return getattr(self._real, attr)

    def __dir__(self):
        return dir(self._real)

    def __getitem__(self, ind):
        return self._real[ind]

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)

    def __repr__(self):
        src = self._real.prettify()
        if not PY3:
            src = src.encode("ascii", errors="xmlcharrefreplace")
        return color.highlight(src, "xml")


def _rule_repr(rule):
    # type: (odoo.models.IrRule) -> t.Text
    parts = []
    parts.append(_record_header(rule))
    parts.append(color.display_name(rule.display_name))
    groups = ", ".join(
        color.record(group.name) + _xml_id_tag(group) for group in rule.groups
    )
    if not groups:
        parts.append(
            color.green.bold("Everyone")
            if getattr(rule, "global")
            else color.red.bold("No-one")
        )
    else:
        parts.append(groups)
    parts.append(_crud_format(rule))
    if rule.domain_force not in {None, False, "[]", "[(1, '=', 1)]", '[(1, "=", 1)]'}:
        assert rule.domain_force
        parts.append(color.highlight(_domain_format(rule.domain_force)))
    return "\n".join(parts)


def _access_repr(access):
    # type: (odoo.models.IrModelAccess) -> t.Text
    parts = []
    parts.append(_record_header(access))
    parts.append(color.display_name(access.display_name))
    parts.append(
        color.record(access.group_id.name) + _xml_id_tag(access.group_id)
        if access.group_id
        else color.green.bold("Everyone")
    )
    parts.append(_crud_format(access))
    return "\n".join(parts)


def _domain_format(domain):
    # type: (t.Text) -> t.Text
    context = {
        key: _Expressionizer(key) for key in env[u"ir.rule"]._eval_context().keys()
    }
    try:
        # dont_inherit avoids ugly __future__ unicode_literals
        compiled = compile(domain.strip(), "<domain>", "eval", dont_inherit=True)
        evaled = eval(compiled, context)
    except Exception:
        return domain
    return pprint.pformat(odoo.osv.expression.normalize_domain(evaled))


class _Expressionizer(object):
    """Remember attribute accesses and such to show in the repr.

    Useful for running code through eval.

    >>> _Expressionizer("foo").bar("baz", k=3)
    foo.bar('baz', k=3)
    """

    def __init__(self, path):
        # type: (t.Text) -> None
        self._path = path

    def __repr__(self):
        return self._path

    def __getattr__(self, attr):
        return self.__class__("{}.{}".format(self._path, attr))

    def __getitem__(self, ind):
        return self.__class__("{}[{!r}]".format(self._path, ind))

    def __iter__(self):
        # type: () -> t.NoReturn
        raise TypeError

    def __call__(self, *args, **kwargs):
        argfmt = [repr(arg) for arg in args]
        argfmt.extend("{}={!r}".format(key, value) for key, value in kwargs.items())
        return self.__class__("{}({})".format(self._path, ", ".join(argfmt)))


def _crud_format(rule):
    # type: (t.Union[odoo.models.IrModelAccess, odoo.models.IrRule]) -> t.Text
    return ", ".join(
        color.permission(name) if perm else " " * len(name)
        for name, perm in [
            ("read", rule.perm_read),
            ("write", rule.perm_write),
            ("create", rule.perm_create),
            ("unlink", rule.perm_unlink),
        ]
    )


class MethodProxy(object):
    def __init__(self, method, model, name):
        # type: (t.Callable, odoo.models.BaseModel, t.Text) -> None
        self._real = method
        self.model = model
        self.name = str(name)

    def __call__(self, *args, **kwargs):
        # type: (t.Any, t.Any) -> t.Any
        return self._real(*args, **kwargs)

    def __getattr__(self, attr):
        # type: (t.Text) -> t.Any
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"edit_", "source_", "grep_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        return "{}({!r}, {!r}, {!r})".format(
            self.__class__.__name__, self._real, self.model, self.name
        )

    def _repr_pretty_(self, printer, _cycle):
        if printer.indentation == 0:
            printer.text(method_repr(self))
        else:
            printer.text(repr(self))

    edit_ = edit

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        for cls in type(self.model).mro()[1:]:
            module = _module(cls)
            if location is not None and location != module:
                continue
            if self.name in vars(cls):
                func = _unpack_function(vars(cls)[self.name])
                fname = inspect.getsourcefile(func)
                lines, lnum = inspect.getsourcelines(func)
                print(_format_source(Source(module, fname, lnum)))
                print(color.highlight("".join(lines)))

    def grep_(self, *args, **kwargs):
        """grep through all of the method's definitions, ignoring other file content.

        The implementation is hacky. If you get weird results it's probably not
        your fault.
        """
        # We mimic the output of ripgrep, which itself blends grep and ack
        # One difference is that ripgrep prints non-matching line numbers
        # with a dash following the number instead of a colon
        argv = _build_grep_argv(args, kwargs)
        first = True
        for cls in type(self.model).mro()[1:]:
            if self.name in vars(cls):
                lines, lnum = inspect.getsourcelines(
                    _unpack_function(vars(cls)[self.name])
                )
                proc_input = "".join(
                    "{}:{}".format(color.green(str(lnum + ind)), line)
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
                            print(error, file=sys.stderr)
                            break
                        continue

                print(color.purple(inspect.getsourcefile(cls) or "???"))
                proc = subprocess.Popen(
                    argv, stdin=subprocess.PIPE, universal_newlines=True
                )
                assert proc.stdin is not None
                proc.stdin.write(proc_input)
                proc.stdin.close()
                proc.wait()
                if not first:
                    print()
                else:
                    first = False


def _extract_field_source(fname, lnum):
    # type: (t.Text, int) -> t.Text
    pieces = []
    depth = 0
    for line in iter(lambda: linecache.getline(fname, lnum), ""):
        for ind, char in enumerate(line):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    pieces.append(line[: ind + 1])
                    return "".join(pieces)
        pieces.append(line)
        lnum += 1
    return "".join(pieces)


class FieldProxy(object):
    def __init__(self, field):
        # type: (odoo.fields.Field) -> None
        self._real = field

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"source_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        return repr(self._real)

    def _repr_pretty_(self, printer, cycle):
        _Field_repr_pretty_(self._real, printer, cycle)

    def source_(self, location=None):
        for source in _find_source(self._real):
            if location is not None and location != source.module:
                continue
            print(_format_source(source))
            print(color.highlight(_extract_field_source(source.fname, source.lnum)))

    def _make_method_proxy_(self, func):
        # type: (object) ->  object
        if not callable(func):
            return func
        name = getattr(func, "__name__", False)
        if not name:
            return func
        model = env[self._real.model_name]
        if hasattr(model, name):
            get = getattr(func, "__get__", False)
            if get:
                func = get(model)
            if not callable(func):
                return func
            return MethodProxy(func, model, name)
        return func

    @property
    def compute(self):
        # type: () -> object
        return self._make_method_proxy_(_find_computer(self._real))

    @property
    def default(self):
        # type: () -> object
        if not self._real.default:
            raise AttributeError
        return self._make_method_proxy_(_find_field_default(self._real))


def sql(query, *args):
    # type: (t.Text, object) -> t.List[t.Any]
    """Execute a SQL query and try to make the result nicer.

    Optimized for ease of use, at the cost of performance and boringness.
    """
    with savepoint(env.cr):
        env.cr.execute(query, args)
        result = env.cr.fetchall()
    if result and len(result[0]) == 1:
        result = [row[0] for row in result]
    return result


def browse(url):
    # type: (t.Text) -> odoo.models.BaseModel
    """Take a browser form URL and figure out its record."""
    # TODO: handle other views more intelligently
    #       perhaps based on the user?
    query = urlparse.parse_qs(urlparse.urlparse(url).fragment)
    return env[query["model"][0]].browse(int(query["id"][0]))


class RecordBrowser(object):
    _model = NotImplemented
    _field = NotImplemented
    _listing = NotImplemented
    _abbrev = NotImplemented

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            thing = env[self._model].search([(self._field, "=", attr)])
        except AttributeError as err:
            if err.args == ("environments",):
                # This happens when IPython runs completions in a separate thread
                # Returning an empty record means it can complete without making
                # queries, even across relations
                # When the line is actually executed __getattr__ will run again
                return env[self._model]
            raise
        if not thing:
            raise AttributeError("Record '{}' not found".format(attr))
        return thing

    def __dir__(self):
        if self._model not in env.registry:
            # Avoid aborting SQL transaction
            raise TypeError("Model '{}' is not installed".format(self._model))
        return ["_model", "_field", "_listing", "_abbrev"] + sql(self._listing)

    def __eq__(self, other):
        # type: (object) -> bool
        return self.__class__ is other.__class__

    __getitem__ = __getattr__
    _ipython_key_completions_ = __dir__

    @classmethod
    def _repr_for_value(cls, ident):
        # type: (t.Text) -> t.Text
        if ident and not keyword.iskeyword(ident):
            if PY3:
                if ident.isidentifier():
                    return "{}.{}".format(cls._abbrev, ident)
            else:
                if (
                    set(ident) <= set(string.ascii_letters + string.digits + "_")
                    and not ident[0].isdigit()
                ):
                    return "{}.{}".format(cls._abbrev, ident)
        if not PY3 and not isinstance(ident, str):
            try:
                ident = str(ident)
            except UnicodeEncodeError:
                pass
        return "{}[{!r}]".format(cls._abbrev, ident)


class UserBrowser(RecordBrowser):
    """Easy access to records of user accounts.

    Usage:
    >>> u.admin
    res.users[1]
    >>> u[1]
    res.users[1]

    >>> u.adm<TAB> completes to u.admin

    >>> record.sudo(u.testemployee1)  # View a record as testemployee1
    """

    _model = "res.users"
    _field = "login"
    _listing = "SELECT login FROM res_users WHERE active"
    _abbrev = "u"


class EmployeeBrowser(RecordBrowser):
    """Like UserBrowser, but for employees. Based on user logins."""

    _model = "hr.employee"
    _field = "user_id.login"
    _listing = """
    SELECT u.login
    FROM hr_employee e
    INNER JOIN resource_resource r
        ON e.resource_id = r.id
    INNER JOIN res_users u
        ON r.user_id = u.id
    WHERE r.active
    """
    _abbrev = "emp"


class DataBrowser(object):
    """Easy access to data records by their XML IDs.

    Usage:
    >>> ref.base.user_root
    res.users[1]
    >>> ref('base.user_root')
    res.users[1]

    The attribute access has tab completion.
    """

    def __getattr__(self, attr):
        # type: (t.Text) -> DataModuleBrowser
        if not sql("SELECT id FROM ir_model_data WHERE module = %s LIMIT 1", attr):
            raise AttributeError("No module '{}'".format(attr))
        browser = DataModuleBrowser(attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return sql("SELECT DISTINCT module FROM ir_model_data")

    def __call__(self, query):
        # type: (t.Text) -> odoo.models.BaseModel
        return env.ref(query)

    def __eq__(self, other):
        # type: (object) -> bool
        return self.__class__ is other.__class__


class DataModuleBrowser(object):
    """Access data records within a module. Created by DataBrowser."""

    def __init__(self, module):
        # type: (t.Text) -> None
        self._module = module

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            record = env.ref("{}.{}".format(self._module, attr))
        except ValueError as err:
            raise AttributeError(err)
        except AttributeError as err:
            if err.args == ("environments",):
                # Threading issue, try to keep autocomplete working
                # See RecordBrowser.__getattr__
                model = sql(
                    "SELECT model FROM ir_model_data WHERE module = %s AND name = %s",
                    self._module,
                    attr,
                )[0]
                return env[model]
            raise
        setattr(self, attr, record)
        return record

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return sql("SELECT name FROM ir_model_data WHERE module = %s", self._module)


def _is_record(obj):
    # type: (object) -> bool
    """Return whether an object is an Odoo record."""
    return isinstance(obj, odoo.models.BaseModel) and hasattr(obj, "_ids")


class AddonBrowser(object):
    def __getattr__(self, attr):
        # type: (t.Text) -> Addon
        if not sql("SELECT name FROM ir_module_module WHERE name = %s", attr):
            raise AttributeError("No module '{}'".format(attr))
        addon = Addon(attr)
        setattr(self, attr, addon)
        return addon

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return sql("SELECT name FROM ir_module_module")

    def __iter__(self):
        # type: () -> t.Iterator[Addon]
        for name in sql("SELECT name FROM ir_module_module"):
            yield Addon(name)


class Addon(object):
    def __init__(self, module):
        # type: (t.Text) -> None
        self._module = module
        self._record = None  # type: t.Optional[odoo.models.IrModuleModule]

    @property
    def manifest(self):
        # type: () -> _AttributableDict
        manifest = odoo.modules.module.load_information_from_description_file(
            self._module
        )
        if not manifest:
            raise RuntimeError("Module {!r} not found".format(self._module))
        return _AttributableDict(manifest)

    @property
    def record(self):
        # type: () -> odoo.models.IrModuleModule
        if self._record is None:
            self._record = env[u"ir.module.module"].search(
                [("name", "=", self._module)]
            )
        return self._record

    @property
    def models(self):
        # type: () -> t.List[ModelProxy]
        # TODO: return AddonModelBrowser with PartialModels that show the
        # fields (and methods?) added in the addon
        return [
            ModelProxy(name, nocomplete=True)
            for name in (
                env[u"ir.model"]
                .browse(
                    env[u"ir.model.data"]
                    .search([("model", "=", "ir.model"), ("module", "=", self._module)])
                    .mapped("res_id")
                )
                .mapped("model")
            )
        ]

    @property
    def path(self):
        # type: () -> t.Text
        mod_path = odoo.modules.module.get_module_path(self._module)
        if not mod_path:
            raise RuntimeError("Can't find path of module {!r}".format(self._module))
        return mod_path

    @property
    def ref(self):
        # type: () -> DataModuleBrowser
        return DataModuleBrowser(self._module)

    def grep_(self, *args, **kwargs):
        """grep through the addon's directory. See ModelProxy.grep_ for options."""
        argv = _build_grep_argv(args, kwargs)
        if argv[0] == "grep":
            argv[1:1] = ["-r", "--exclude-dir=.git"]
        argv.append(self.path)
        subprocess.Popen(argv).wait()

    def __repr__(self):
        # type: () -> str
        return "{}({!r})".format(self.__class__.__name__, self._module)

    def __str__(self):
        # type: () -> str
        # TODO: integrate with displayhooks (odoo_repr?)
        defined_models = (
            env[u"ir.model"]
            .browse(
                env[u"ir.model.data"]
                .search([("model", "=", "ir.model"), ("module", "=", self._module)])
                .mapped("res_id")
            )
            .mapped("model")
        )

        state = self.record.state
        if (
            state == "installed"
            and self.record.installed_version != self.manifest.version
        ):
            state += " (out of date)"

        if state == "installed":
            state = color.green.bold(state.capitalize())
        elif state in ("uninstallable", "uninstalled"):
            state = color.red.bold(state.capitalize())
        else:
            state = color.yellow.bold(state.capitalize())

        description = self.manifest.description
        if not PY3:
            try:
                description = description.decode("utf8").encode(
                    "ascii", errors="replace"
                )
            except UnicodeDecodeError:
                pass

        return "\n".join(
            [
                "{} {} by {}".format(
                    color.module(self._module),
                    self.manifest.version,
                    self.manifest.author,
                ),
                self.path,
                state,
                color.display_name(self.manifest.name),
                self.manifest.summary,
                "Depends: {}".format(
                    ", ".join(map(color.module, self.manifest.depends))
                ),
                "Defines: {}".format(", ".join(map(color.model, defined_models,))),
                "",
                # rst2ansi might be better here
                # (https://pypi.org/project/rst2ansi/)
                color.highlight(description, "rst"),
            ]
        )

    def _repr_pretty_(self, printer, _cycle):
        if printer.indentation == 0:
            printer.text(str(self))
        else:
            printer.text(repr(self))


class RecordDef(collections.namedtuple("RecordDef", ("module", "fname", "elem"))):
    __slots__ = ()

    def to_source(self):
        # type: () -> Source
        return Source(module=self.module, fname=self.fname, lnum=self.elem.sourceline)


_xml_records = None  # type: t.Optional[t.DefaultDict[t.Text, t.List[RecordDef]]]


def xml_records():
    # type: () -> t.DefaultDict[t.Text, t.List[RecordDef]]
    import lxml.etree

    global _xml_records

    if _xml_records is not None:
        return _xml_records

    _xml_records = collections.defaultdict(list)
    for module, demo in sql(
        "SELECT name, demo FROM ir_module_module WHERE state = 'installed'"
    ):
        manifest = odoo.modules.module.load_information_from_description_file(module)
        path = odoo.modules.module.get_module_path(module)
        if not path:
            continue
        data_files = list(manifest.get("data", ()))
        if demo:
            data_files.extend(manifest.get("demo", ()))
        for fname in data_files:
            if not fname.endswith(".xml"):
                continue
            fname = os.path.join(path, fname)
            if not os.path.isfile(fname):
                continue
            tree = lxml.etree.parse(fname)
            for tag in RECORD_TAGS:
                for record in tree.iterfind("//" + tag):
                    if "id" not in record.attrib:
                        continue
                    rec_id = record.attrib["id"]
                    if "." not in rec_id:
                        rec_id = module + "." + rec_id
                    _xml_records[rec_id].append(
                        RecordDef(module=module, fname=fname, elem=record)
                    )
    return _xml_records


def _BaseModel_source_(record, location=None, context=False):
    # type: (odoo.models.BaseModel, t.Optional[t.Text], bool) -> None
    import lxml.etree

    for rec in record:
        for rec_id in _xml_ids(rec):
            for definition in xml_records()[".".join(rec_id)]:
                if location is not None and definition.module != location:
                    continue
                elem = definition.elem.getroottree() if context else definition.elem
                print(_format_source(definition.to_source()))
                src = lxml.etree.tostring(elem, encoding="unicode")
                # In perverse cases dedenting may change the meaning
                src = textwrap.dedent(" " * 80 + src).strip()
                print(color.highlight(src, "xml"))


def grep_(*args, **kwargs):
    """grep through all installed addons. See ModelProxy.grep_ for options."""
    argv = _build_grep_argv(args, kwargs)
    if argv[0] == "grep":
        argv[1:1] = ["-r", "--exclude-dir=.git"]
    argv.extend(
        filter(
            None,
            map(
                odoo.modules.module.get_module_path,
                sql("SELECT name FROM ir_module_module WHERE state = 'installed'"),
            ),
        )
    )
    subprocess.Popen(argv).wait()


class _AttributableDict(dict):
    def __getattr__(self, attr):
        # type: (t.Text) -> t.Any
        try:
            val = self[attr]
        except KeyError:
            raise AttributeError(attr)
        if isinstance(val, dict):
            val = self.__class__(val)
        return val

    def __dir__(self):
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = set()
        listing.update(self.keys())
        return sorted(listing)


def fingerprint(record):
    if len(record) != 1:
        raise ValueError("To get fingerprints of multiple records, use `fingerprints`.")

    def fieldprint(field):
        value = getattr(record, field.name)
        if field.type == "selection":
            return value
        return bool(value)

    return tuple(
        (name, fieldprint(field)) for name, field in sorted(record._fields.items())
    )


def fingerprints(records):
    return frozenset(map(fingerprint, records))


def inhomogenities(records):
    prints = list(map(dict, fingerprints(records)))
    for field in sorted(records._fields):
        values = {prnt[field] for prnt in prints}
        if len(values) != 1:
            print("{}:\t{!r}".format(field, values))


def differences(a, b, loose=False):
    if a._name != b._name:
        raise TypeError("Can only compare records of same model")

    def fmtvalset(valset):
        if len(valset) == 1:
            return next(iter(valset))
        return valset

    aprints = list(map(dict, fingerprints(a)))
    bprints = list(map(dict, fingerprints(b)))
    for field in sorted(a._fields):
        avals = {prnt[field] for prnt in aprints}
        bvals = {prnt[field] for prnt in bprints}
        if not (avals & bvals) or (loose and avals != bvals):
            print("{}:\t{!r} vs {!r}".format(field, fmtvalset(avals), fmtvalset(bvals)))


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


def translate(text):
    # type: (t.Text) -> None
    translations = env[u"ir.translation"].search(
        ["|", ("src", "=", text), ("value", "=", text)]
    )
    if not translations:
        text = "%" + text + "%"
        translations = env[u"ir.translation"].search(
            ["|", ("src", "ilike", text), ("value", "ilike", text)]
        )
    odoo_print(translations)


try:
    odoo.models.BaseModel._repr_pretty_ = _BaseModel_repr_pretty_  # type: ignore
    odoo.models.BaseModel.edit_ = edit  # type: ignore
    odoo.models.BaseModel.print_ = odoo_print  # type: ignore
    odoo.models.BaseModel.search_ = _BaseModel_search_  # type: ignore
    odoo.models.BaseModel.create_ = _BaseModel_create_  # type: ignore
    odoo.models.BaseModel.filtered_ = _BaseModel_filtered_  # type: ignore
    odoo.models.BaseModel.source_ = _BaseModel_source_  # type: ignore
    odoo.fields.Field._repr_pretty_ = _Field_repr_pretty_  # type: ignore
    odoo.fields.Field.edit_ = edit  # type: ignore
except AttributeError:
    pass
