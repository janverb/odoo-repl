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

from __future__ import print_function
from __future__ import unicode_literals

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
import pdb
import pprint
import random
import re
import string
import subprocess
import sys
import textwrap
import types

from datetime import datetime

PY3 = sys.version_info >= (3, 0)

if PY3:
    import builtins
else:
    import __builtin__ as builtins

if PY3:
    import urllib.parse as urlparse
else:
    import urlparse

MYPY = False
if MYPY:
    import typing as t

if PY3:
    Text = (str,)
    TextLike = (str, bytes)
else:
    Text = (str, type(""))
    TextLike = Text

env = None  # type: t.Any
odoo = None  # type: t.Any

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
    "__last_update",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
    "id",
}


def _ensure_import():
    # type: () -> None
    global odoo
    if odoo is not None:
        return
    try:
        import openerp as odoo
    except ImportError:
        import odoo


def parse_config(argv):
    """Set up odoo.tools.config from command line arguments."""
    _ensure_import()
    logging.getLogger().handlers = []
    odoo.netsvc._logger_init = False
    odoo.tools.config.parse_config(argv)


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
    # TODO: because of set_trace() it's now more likely that this is called
    # multiple times, perhaps it should do less redundant work on global state
    global env
    global edit_bg

    _ensure_import()

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

    if db is None or isinstance(db, Text):
        db_name = db or odoo.tools.config["db_name"]
        if not db_name:
            if env is None:
                raise ValueError(
                    "Can't determine database name. Run with `-d dbname` "
                    "or pass it as the first argument to odoo_repl.enable()."
                )
            db_name = env.cr.dbname
        env = odoo.api.Environment(
            odoo.sql_db.db_connect(db_name).cursor(), odoo.SUPERUSER_ID, {}
        )
    elif isinstance(db, odoo.sql_db.Cursor):
        env = odoo.api.Environment(db, odoo.SUPERUSER_ID, {})
    elif isinstance(db, odoo.api.Environment):
        env = db
    else:
        raise TypeError(db)

    atexit.register(env.cr.close)

    edit_bg = bg_editor

    if sys.version_info < (3, 0):
        readline_init(os.path.expanduser("~/.python2_history"))

    sys.displayhook = displayhook
    odoo.models.BaseModel._repr_pretty_ = _BaseModel_repr_pretty_
    odoo.models.BaseModel.edit_ = edit
    odoo.models.BaseModel.print_ = odoo_print
    odoo.models.BaseModel.search_ = _BaseModel_search_
    odoo.models.BaseModel.create_ = _BaseModel_create_
    odoo.models.BaseModel.filtered_ = _BaseModel_filtered_
    odoo.fields.Field._repr_pretty_ = _Field_repr_pretty_
    odoo.fields.Field.edit_ = edit

    # Whenever this would be useful you should probably just use OPdb directly
    # But maybe there are cases in which it's hard to switch out pdb
    # TODO: It should probably run iff odoo_repl.enable() is called from pdb

    # pdb.Pdb.displayhook = OPdb.displayhook

    to_install = {
        "self": env.user,
        "odoo": odoo,
        "openerp": odoo,
        "browse": browse,
        "sql": sql,
        "env": EnvProxy(),
        "u": UserBrowser(),
        "emp": EmployeeBrowser(),
        "cfg": ConfigBrowser(),
        "ref": DataBrowser(),
        "view": ViewBrowser(),
        "addons": AddonBrowser(),
    }
    for name, obj in to_install.items():
        if hasattr(builtins, name):
            continue
        if hasattr(__main__, name):
            if getattr(__main__, name) != obj:
                print("Not installing {} due to name conflict".format(name))
            continue
        setattr(__main__, name, obj)

    for part in __main__.env._base_parts():
        if not hasattr(__main__, part) and not hasattr(builtins, part):
            setattr(__main__, part, ModelProxy(part))

    if not color:
        disable_color()


def disable_color():
    """Disable colored output for model and record summaries."""
    global red, green, yellow, blue, purple, cyan, USE_COLOR
    red = green = yellow = blue = purple = cyan = "{}".format
    field_colors.clear()
    USE_COLOR = False


def readline_init(history=None):
    """Set up readline history and completion. Unnecessary in Python 3."""
    import readline
    import rlcompleter as _rlcompleter  # noqa: F401

    readline.parse_and_bind("tab: complete")
    if readline.get_current_history_length() == 0 and history is not None:
        try:
            readline.read_history_file(history)
        except IOError:
            pass
        atexit.register(lambda: readline.write_history_file(history))


# Terminal escape codes for coloring text
red = "\x1b[1m\x1b[31m{}\x1b[30m\x1b[m".format
green = "\x1b[1m\x1b[32m{}\x1b[30m\x1b[m".format
yellow = "\x1b[1m\x1b[33m{}\x1b[30m\x1b[m".format
blue = "\x1b[1m\x1b[34m{}\x1b[30m\x1b[m".format
purple = "\x1b[1m\x1b[35m{}\x1b[30m\x1b[m".format
cyan = "\x1b[1m\x1b[36m{}\x1b[30m\x1b[m".format

USE_COLOR = True


def _color_repr(owner, field_name):
    """Return a color-coded representation of a record's field value."""
    if field_name == "company_id":
        # This one causes a caching-related hang for some reason
        return red("<blacklisted field>")
    try:
        obj = getattr(owner, field_name)
    except Exception as err:
        return red(repr(err))
    field_type = owner._fields[field_name].type
    if obj is False and field_type != "boolean" or obj is None:
        return red(repr(obj))
    elif isinstance(obj, bool):
        # False shows up as green if it's a Boolean, and red if it's a
        # default value, so red values always mean "missing"
        return green(repr(obj))
    elif _is_record(obj):
        if len(obj._ids) == 0:
            return red("{}[]".format(obj._name))
        if len(obj._ids) > 10:
            return cyan(
                "{} \N{multiplication sign} {}".format(obj._name, len(obj._ids))
            )
        try:
            if obj._name == "res.users":
                return ", ".join(
                    cyan(UserBrowser._repr_for_value(user.login))
                    if user.login and user.active
                    else cyan("res.users[{}]".format(user.id))
                    for user in obj
                )
            elif obj._name == "hr.employee":
                return ", ".join(
                    cyan(EmployeeBrowser._repr_for_value(em.user_id.login))
                    if (
                        em.active
                        and em.user_id
                        and em.user_id.login
                        and len(em.user_id.employee_ids) == 1
                    )
                    else cyan("hr.employee[{}]".format(em.id))
                    for em in obj
                )
        except Exception:
            pass
        return cyan("{}[{}]".format(obj._name, _ids_repr(obj._ids)))
    elif isinstance(obj, TextLike):
        if len(obj) > 120:
            return blue(repr(obj)[:120] + "...")
        return blue(repr(obj))
    elif isinstance(obj, datetime):
        # Blue for consistency with versions where they're strings
        return blue(str(obj))
    elif isinstance(obj, (int, float)):
        return purple(repr(obj))
    else:
        return repr(obj)


field_colors = {
    "one2many": cyan,
    "many2one": cyan,
    "many2many": cyan,
    "char": blue,
    "text": blue,
    "binary": blue,
    "datetime": blue,
    "date": blue,
    "integer": purple,
    "float": purple,
    "id": purple,
    "boolean": green,
}


def field_color(field):
    """Color a field type, if appropriate."""
    if field.relational:
        return "{}: {}".format(green(field.type), cyan(field.comodel_name))
    if field.type in field_colors:
        return field_colors[field.type](field.type)
    return green(field.type)


def _unwrap(obj):
    if isinstance(obj, (ModelProxy, MethodProxy, FieldProxy)):
        obj = obj._real
    return obj


def odoo_repr(obj):
    if isinstance(obj, ModelProxy):
        return model_repr(obj)
    elif isinstance(obj, MethodProxy):
        return method_repr(obj)
    elif isinstance(obj, FieldProxy):
        return field_repr(obj)
    elif _is_record(obj):
        return record_repr(obj)
    elif _is_field(obj):
        return field_repr(obj)
    else:
        return repr(obj)


def odoo_print(obj, **kwargs):
    if _is_record(obj) and len(obj) > 1:
        print("\n\n".join(record_repr(record) for record in obj), **kwargs)
    else:
        print(odoo_repr(obj), **kwargs)


def _fmt_properties(field):
    return "".join(
        attr[0] if getattr(field, attr, False) else " "
        for attr in ["required", "store", "default"]
    )


def model_repr(obj):
    """Summarize a model's fields."""
    if isinstance(obj, ModelProxy) and obj._real is None:
        return repr(obj)
    obj = _unwrap(obj)

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    parts.append(yellow(obj._name))
    if getattr(obj, "_description", False):
        parts.append(obj._description)
    if getattr(obj, "_inherits", False):
        for model_name, field_name in obj._inherits.items():
            parts.append(
                "Inherits from {} through {}".format(
                    cyan(model_name), green(field_name)
                )
            )
    delegated = []
    for field in fields:
        if field in FIELD_BLACKLIST:
            continue
        if getattr(obj._fields[field], "related", False):
            delegated.append(obj._fields[field])
            continue
        parts.append(
            blue(_fmt_properties(obj._fields[field]))
            + " {}: ".format(green(field))
            # Like str.ljust, but not confused about colors
            + (max_len - len(field)) * " "
            + field_color(obj._fields[field])
            + " ({})".format(obj._fields[field].string)
        )
    if delegated:
        buckets = collections.defaultdict(
            list
        )  # type: t.DefaultDict[t.Tuple[str, ...], t.List[str]]
        for field in delegated:
            buckets[tuple(field.related[:-1])].append(
                green(field.name)
                if field.related[-1] == field.name
                else "{} (.{})".format(green(field.name), field.related[-1])
            )
        parts.append("")
        for related_field, field_names in buckets.items():
            # TODO: figure out name of model of real field
            parts.append(
                "Delegated to {}: {}".format(
                    yellow(".".join(related_field)), ", ".join(field_names)
                )
            )
    parts.append("")
    parts.extend(_format_source(_find_source(obj)))
    return "\n".join(parts)


def _xml_id_tag(obj):
    return "".join(
        " (ref.{}.{})".format(data_record.module, data_record.name)
        for data_record in env["ir.model.data"].search(
            [("model", "=", obj._name), ("res_id", "=", obj.id)]
        )
        if data_record.module != "__export__"
    )


def _record_header(obj):
    header = yellow("{}[{!r}]".format(obj._name, obj.id)) + _xml_id_tag(obj)
    # .get_external_id() returns at most one result per record
    if obj.env.uid != 1:
        header += " (as {})".format(UserBrowser._repr_for_value(obj.env.user.login))
    return header


def _ids_repr(idlist):
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
            fragments.append("NewId \N{multiplication sign} {}".format(news))
    return ", ".join(fragments)


def record_repr(obj):
    """Display all of a record's fields."""
    obj = _unwrap(obj)

    if len(obj) == 0:
        return "{}[]".format(obj._name)
    elif len(obj) > 1:
        return "{}[{}]".format(obj._name, _ids_repr(obj._ids))

    if obj.env.cr.closed:
        return "{}[{}] (closed cursor)".format(obj._name, _ids_repr(obj._ids))

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    parts.append(_record_header(obj))

    if not obj.exists():
        parts.append(red("Missing"))
        return "\n".join(parts)

    for field in fields:
        if field in FIELD_BLACKLIST:
            continue
        parts.append(
            "{}: ".format(green(field))
            + (max_len - len(field)) * " "
            + _color_repr(obj, field)
        )
    return "\n".join(parts)


def _find_computer(field):
    if field.compute is not None:
        func = field.compute
        if hasattr(func, "__func__"):
            func = func.__func__
        if isinstance(func, Text):
            func = getattr(env[field.model_name], func)
        return func
    elif type(getattr(field, "column", None)).__name__ == "function":
        return field.column._fnct
    return None


def _decipher_lambda(func):
    """Try to retrieve a lambda's source code. Very nasty."""
    source = inspect.getsource(func)
    source = re.sub(r" *\n *", " ", source).strip()
    source = re.search("lambda [^:]*:.*", source).group().strip()
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
    if not field.default:
        return None
    model = env[field.model_name]
    if hasattr(model, "_defaults") and not callable(model._defaults[field.name]):
        default = model._defaults[field.name]
    else:
        default = field.default

    try:
        # Very nasty but works some of the time
        # Hopefully something better exists
        if (
            callable(default)
            and default.__module__ in {"odoo.fields", "openerp.fields"}
            and default.__name__ == "<lambda>"
            and "value" in default.__code__.co_freevars
        ):
            default = default.__closure__[
                default.__code__.co_freevars.index("value")
            ].cell_contents
    except Exception:
        pass

    return default


def field_repr(field):
    """List detailed information about a field."""
    # TODO:
    # - .groups, .copy, .states, .inverse, .column[12]
    field = _unwrap(field)
    model = env[field.model_name]
    record = env["ir.model.fields"].search(
        [("model", "=", field.model_name), ("name", "=", field.name)]
    )
    parts = []
    parts.append(
        "{} {} on {}".format(
            blue(record.ttype), yellow(record.name), cyan(record.model)
        )
    )
    if record.relation:
        parts[-1] += " to {}".format(cyan(record.relation))

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

    if getattr(field, "related", False):
        parts.append("Delegated to {}".format(yellow(".".join(field.related))))
    elif getattr(field, "column", False) and type(field.column).__name__ == "related":
        parts.append("Delegated to {}".format(yellow(".".join(field.column.arg))))
    else:
        func = _find_computer(field)
        if getattr(func, "__name__", None) == "<lambda>":
            try:
                func = _decipher_lambda(func)
            except Exception:
                pass
        if callable(func):
            func = getattr(func, "__name__", func)
        if func:
            parts.append("Computed by {}".format(blue(func)))

    if getattr(model, "_constraint_methods", False):
        for constrainer in model._constraint_methods:
            if field.name in constrainer._constrains:
                parts.append(
                    "Constrained by {}".format(
                        blue(getattr(constrainer, "__name__", constrainer))
                    )
                )

    if getattr(field, "inverse_fields", False):
        parts.append(
            "Inverted by {}".format(
                ", ".join(yellow(inv.name) for inv in field.inverse_fields)
            )
        )

    if field.default:
        default = _find_field_default(field)

        show_literal = False

        if getattr(default, "__module__", None) in {"odoo.fields", "openerp.fields"}:
            default = purple("(Unknown)")
            show_literal = True

        try:
            if getattr(default, "__name__", None) == "<lambda>":
                source = _decipher_lambda(default)
                default = purple(source)
                show_literal = True
        except Exception:
            pass

        if show_literal:
            parts.append("Default value: {}".format(default))
        else:
            parts.append("Default value: {!r}".format(default))

    if record.ttype == "selection":
        parts.append(pprint.pformat(field.selection))

    sources = _find_source(field)
    parts.extend(_format_source(sources))

    if not sources and record.modules:
        parts.append(
            "Defined in module {}".format(
                ", ".join(green(module) for module in record.modules.split(", "))
            )
        )

    return "\n".join(parts)


def _find_decorators(method):
    if hasattr(method, "_constrains"):
        yield blue("@api.constrains") + "({})".format(
            ", ".join(map(repr, method._constrains))
        )
    if hasattr(method, "_depends"):
        if callable(method._depends):
            yield blue("@api.depends") + "({!r})".format(method._depends)
        else:
            yield blue("@api.depends") + "({})".format(
                ", ".join(map(repr, method._depends))
            )
    if hasattr(method, "_onchange"):
        yield blue("@api.onchange") + "({})".format(
            ", ".join(map(repr, method._onchange))
        )
    if getattr(method, "_api", False):
        api = method._api
        yield blue("@api.{}".format(api.__name__ if callable(api) else api))
    if not hasattr(method, "__self__"):
        yield blue("@staticmethod")
    elif isinstance(method.__self__, type):
        yield blue("@classmethod")


def _unpack_function(func):
    while hasattr(func, "_orig"):
        func = func._orig
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    if hasattr(func, "__func__"):
        func = func.__func__
    return func


def _func_signature(func):
    if PY3:
        return str(inspect.signature(func))
    return inspect.formatargspec(*inspect.getargspec(func))


def method_repr(method):
    sources = _find_method_source(method)
    model = method.model
    name = method.name

    method = method._real
    decorators = list(_find_decorators(method))
    method = _unpack_function(method)

    signature = _func_signature(method)
    doc = inspect.getdoc(method)
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
            model=cyan(model._name), name=yellow(name), signature=signature
        )
    )
    if doc:
        parts.append(doc)
    parts.append("")
    parts.extend(_format_source(sources))
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


def _format_source(sources):
    return [
        "{}: {}:{}".format(green(module), fname, lnum)
        if lnum is not None
        else "{}: {}".format(green(module), fname)
        for module, fname, lnum in sources
    ]


def _find_source(thing):
    if _is_record(thing):
        return _find_model_source(_unwrap(thing))
    elif _is_field(thing):
        return _find_field_source(thing)
    elif isinstance(thing, MethodProxy):
        return _find_method_source(thing)
    else:
        raise TypeError(thing)


def _find_model_source(model):
    return [
        (cls._module, inspect.getsourcefile(cls), inspect.getsourcelines(cls)[1])
        for cls in type(model).__bases__
        if cls.__module__ not in {"odoo.api", "openerp.api"}
    ]


def _find_field_source(field):
    res = []
    for cls in type(env[field.model_name]).__bases__:
        if (
            hasattr(cls, "_columns") and field.name in cls._columns
        ) or field.name in vars(cls):
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
                lnum = None
            res.append((cls._module, fname, lnum))
    return res


def _find_method_source(method):
    return [
        (
            getattr(cls, "_module", cls.__name__),
            inspect.getsourcefile(cls),
            inspect.getsourcelines(_unpack_function(getattr(cls, method.name)))[1],
        )
        for cls in type(method.model).mro()[1:]
        # if cls._name != "base"
        if method.name in vars(cls)
    ]


def _BaseModel_repr_pretty_(self, printer, _cycle):
    if printer.indentation == 0 and hasattr(self, "_ids"):
        printer.text(record_repr(self))
    else:
        printer.text(repr(self))


def _Field_repr_pretty_(self, printer, _cycle):
    if printer.indentation == 0 and hasattr(self, "model_name"):
        printer.text(field_repr(self))
    elif not hasattr(self, "model_name"):
        printer.text("<Undisplayable field>")  # Work around bug
    else:
        printer.text(repr(self))


def displayhook(obj):
    """A sys.displayhook replacement that pretty-prints models and records."""
    if obj is not None:
        print(odoo_repr(obj))
        builtins._ = obj


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
        listing = set(super().__dir__()) if PY3 else {"_base_parts"}
        listing.update(self._base_parts())
        listing.update(attr for attr in dir(env) if not attr.startswith("__"))
        return sorted(listing)

    def _base_parts(self):
        # TODO: turn into function?
        return list({mod.split(".", 1)[0] for mod in env.registry})

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, env)

    def __getitem__(self, ind):
        if ind not in env.registry:
            raise IndexError("Model '{}' does not exist".format(ind))
        return ModelProxy(ind, nocomplete=True)

    def __iter__(self):
        for mod in env.registry:
            yield self[mod]

    def __eq__(self, other):
        return self.__class__ is other.__class__

    def _ipython_key_completions_(self):
        return env.registry.keys()


def _BaseModel_create_(self, vals=(), **fields):
    """Create a new record, optionally with keyword arguments.

    .create_(x='test', y=<some record>) is typically equivalent to
    .create({"x": "test", "y": <some record>id}). 2many fields are also
    handled.

    If you make a typo in a field name you get a proper error.
    """
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


def _BaseModel_search_(self, *args, **fields):
    """Perform a quick and dirty search.

    .search_(x='test', y=<some record>) is roughly equivalent to
    .search([('x', '=', 'test'), ('y', '=', <some record>.id)]).
    .search_() gets all records.
    """
    # TODO:
    # - inspect fields
    # - handle 2many relations
    offset = fields.pop("offset", 0)
    limit = fields.pop("limit", None)
    order = fields.pop("order", "id")
    count = fields.pop("count", False)
    shuf = fields.pop("shuf", None)
    clauses = []
    state = "OUT"
    curr = None
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
        self._path = path
        self._real = env[path] if path in env.registry else None
        if nocomplete and self._real is None:
            raise ValueError("Model '{}' does not exist".format(self._path))
        self._nocomplete = nocomplete

    def __getattr__(self, attr):
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
        if callable(thing):
            thing = MethodProxy(thing, self._real, attr)
        return thing

    def __dir__(self):
        real_methods = {"shuf_", "mod_", "source_", "rules_", "view_", "sql_"}
        listing = set(super().__dir__()) if PY3 else real_methods
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
        for field in sorted(self._real._fields.values(), key=lambda f: f.name):
            yield FieldProxy(field)

    def __repr__(self):
        if self._real is not None:
            return "{}[]".format(self._path)
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, _cycle):
        if self._real is not None and printer.indentation == 0:
            printer.text(model_repr(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(self, ind):
        if self._real is None:
            return IndexError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if isinstance(ind, Text):
            if ind in self._real._fields:
                return FieldProxy(self._real._fields[ind])
            thing = getattr(self._real, ind)
            if callable(thing):
                return MethodProxy(thing, self._real, ind)
            return thing
        if isinstance(ind, (list, set, types.GeneratorType)):
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
            raise IndexError(
                "Records {} do not exist".format(", ".join(map(str, missing)))
            )
        return self._real.browse(ind)

    def _ipython_key_completions_(self):
        self._ensure_real()
        return list(self._real._fields)

    def _ensure_real(self):
        if self._real is None:
            raise TypeError("Model '{}' does not exist".format(self._path))

    def _all_ids_(self):
        """Get all record IDs in the database."""
        self._ensure_real()
        return sql("SELECT id FROM {}".format(env[self._path]._table))

    def mod_(self):
        """Get the ir.model record of the model."""
        self._ensure_real()
        return env["ir.model"].search([("model", "=", self._path)])

    def shuf_(self, num=1):
        """Return a random record, or multiple."""
        self._ensure_real()
        return _BaseModel_search_(self._real, shuf=num)

    def source_(self, location=None):
        for cls in type(self._real).__bases__:
            if location is None or getattr(cls, "_module", None) == location:
                _print_source(inspect.getsource(cls))
                return
        raise RuntimeError("Could not find source code")

    def rules_(self, user=None):
        # TODO: is it possible to collapse the rules into a single policy for a user?
        print(
            "\n\n".join(
                [
                    _access_repr(access)
                    for access in env["ir.model.access"].search(
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
                    for rule in env["ir.rule"].search(
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

    def view_(self, user=None, **kwargs):
        """Build up a view as a user. Returns beautifulsoup of the XML.

        Takes the same arguments as ir.model.fields_view_get, notably
        view_id and view_type.
        """
        context = kwargs.pop("context", None)
        kwargs.setdefault("view_type", "form")
        model = self._real
        if user is not None:
            if isinstance(user, Text):
                login = user
                user = env["res.users"].search([("login", "=", login)])
                if len(user) != 1:
                    raise ValueError("No user {!r}".format(login))
            if _is_record(user) and user._name != "res.users":
                # TODO: handle viewing as group
                if hasattr(user, "user_id"):
                    user = user.user_id
                else:
                    raise ValueError("{!r} is not a user".format(user))
            model = model.sudo(user)
        if context is not None:
            model = model.with_context(context)
        form = model.fields_view_get(**kwargs)["arch"]
        return _PrettySoup._from_string(form)

    def sql_(self):
        """Display basic PostgreSQL information about stored fields."""
        # TODO: make more informative
        print(self._real._table)
        for name, field in sorted(self._real._fields.items()):
            if field.store:
                print("  {}".format(name))

    _ = _BaseModel_search_


class _PrettySoup(object):
    """A wrapper around beautifulsoup tag soup to make the repr pretty.

    See https://www.crummy.com/software/BeautifulSoup/bs4/doc/ for more useful
    things to do.
    """

    def __init__(self, soup):
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
        try:
            from pygments import highlight
            from pygments.lexers import XmlLexer
            from pygments.formatters.terminal import TerminalFormatter
        except ImportError:
            return src
        else:
            return highlight(src, XmlLexer(), TerminalFormatter())


def _rule_repr(rule):
    parts = []
    parts.append("{}: {}".format(_record_header(rule), rule.display_name))
    groups = ", ".join(cyan(group.name) + _xml_id_tag(group) for group in rule.groups)
    if not groups:
        parts.append(green("Everyone") if getattr(rule, "global") else red("No-one"))
    else:
        parts.append(groups)
    parts.append(_crud_format(rule))
    if rule.domain_force not in {False, "[]", "[(1, '=', 1)]", '[(1, "=", 1)]'}:
        parts.append(_highlight_source(_domain_format(rule.domain_force)))
    return "\n".join(parts)


def _access_repr(access):
    parts = []
    parts.append("{}: {}".format(_record_header(access), access.display_name))
    parts.append(
        cyan(access.group_id.name) + _xml_id_tag(access.group_id)
        if access.group_id
        else green("Everyone")
    )
    parts.append(_crud_format(access))
    return "\n".join(parts)


def _domain_format(domain):
    context = {
        key: _Expressionizer(key) for key in env["ir.rule"]._eval_context().keys()
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
        self._path = path

    def __repr__(self):
        return self._path

    def __getattr__(self, attr):
        return self.__class__("{}.{}".format(self._path, attr))

    def __getitem__(self, ind):
        return self.__class__("{}[{!r}]".format(self._path, ind))

    def __iter__(self):
        raise TypeError

    def __call__(self, *args, **kwargs):
        argfmt = [repr(arg) for arg in args]
        argfmt.extend("{}={!r}".format(key, value) for key, value in kwargs.items())
        return self.__class__("{}({})".format(self._path, ", ".join(argfmt)))


def _crud_format(rule):
    return ", ".join(
        purple(name) if perm else " " * len(name)
        for name, perm in [
            ("read", rule.perm_read),
            ("write", rule.perm_write),
            ("create", rule.perm_create),
            ("unlink", rule.perm_unlink),
        ]
    )


class MethodProxy(object):
    def __init__(self, method, model, name):
        self._real = method
        self.model = model
        self.name = name

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else {"edit_", "source_"}
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
        if location is None:
            _print_source(inspect.getsource(_unpack_function(self._real)))
            return
        for cls in type(self.model).mro()[1:]:
            if self.name in vars(cls) and getattr(cls, "_module", None) == location:
                _print_source(inspect.getsource(_unpack_function(vars(cls)[self.name])))
                return
        raise RuntimeError("Could not find source code")


class FieldProxy(object):
    def __init__(self, field):
        self._real = field

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else {"source_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        return repr(self._real)

    def _repr_pretty_(self, printer, cycle):
        _Field_repr_pretty_(self._real, printer, cycle)

    def source_(self, location=None):
        for module, fname, lnum in _find_source(self._real):
            if location and module != location:
                continue
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
                            _print_source("".join(pieces))
                            return
                pieces.append(line)
                lnum += 1
        raise RuntimeError("Could not find source code")

    def _make_method_proxy(self, func):
        if not callable(func):
            return func
        if not getattr(func, "__name__", False):
            return func
        model = env[self._real.model_name]
        if hasattr(model, func.__name__):
            func = func.__get__(model)
            return MethodProxy(func, model, func.__name__)
        return None

    @property
    def compute(self):
        return self._make_method_proxy(_find_computer(self._real))

    @property
    def default(self):
        if not self._real.default:
            raise AttributeError
        return self._make_method_proxy(_find_field_default(self._real))


def _print_source(src, **kwargs):
    """Print dedented and highlighted Python source code"""
    print(_highlight_source(src), **kwargs)


def _highlight_source(src):
    src = textwrap.dedent(src)
    if not USE_COLOR:
        return src
    try:
        from pygments import highlight
        from pygments.lexers import PythonLexer
        from pygments.formatters.terminal import TerminalFormatter
    except ImportError:
        pass
    else:
        src = highlight(src, PythonLexer(), TerminalFormatter())
    return src


def sql(query, *args):
    # type: (str, object) -> t.List[t.Any]
    """Execute a SQL query and try to make the result nicer.

    Optimized for ease of use, at the cost of performance and boringness.
    """
    with _savepoint():
        env.cr.execute(query, args)
        result = env.cr.fetchall()
    if result and len(result[0]) == 1:
        result = [row[0] for row in result]
    return result


def browse(url):
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
        if not isinstance(ident, str):
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
        if not sql("SELECT id FROM ir_model_data WHERE module = %s LIMIT 1", attr):
            raise AttributeError("No module '{}'".format(attr))
        browser = DataModuleBrowser(attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        return sql("SELECT DISTINCT module FROM ir_model_data")

    def __call__(self, query):
        return env.ref(query)

    def __eq__(self, other):
        return self.__class__ is other.__class__


class DataModuleBrowser(object):
    """Access data records within a module. Created by DataBrowser."""

    def __init__(self, module):
        # type: (str) -> None
        self._module = module

    def __getattr__(self, attr):
        try:
            record = env.ref("{}.{}".format(self._module, attr))
        except ValueError as err:
            raise AttributeError(err)
        setattr(self, attr, record)
        return record

    def __dir__(self):
        return sql("SELECT name FROM ir_model_data WHERE module = %s", self._module)


class ViewBrowser(DataBrowser):
    """Easy beautifulsoup-ified acess to views by their XML IDs."""

    # TODO: make this show important metadata, not just XML

    def __getattr__(self, attr):
        if not sql("SELECT id FROM ir_model_data WHERE module = %s LIMIT 1", attr):
            raise AttributeError("No module '{}'".format(attr))
        browser = ViewModuleBrowser(attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        return sql(
            "SELECT DISTINCT module FROM ir_model_data WHERE model = 'ir.ui.view'"
        )

    def __eq__(self, other):
        return self.__class__ is other.__class__


class ViewModuleBrowser(object):
    def __init__(self, module):
        # type: (str) -> None
        self._module = module

    def __getattr__(self, attr):
        try:
            record = env.ref("{}.{}".format(self._module, attr))
        except ValueError as err:
            raise AttributeError(err)
        if record._name != "ir.ui.view":
            raise AttributeError("{}.{} is not a view".format(self._module, attr))
        soup = _PrettySoup._from_string(record.arch)
        setattr(self, attr, soup)
        return soup

    def __dir__(self):
        return sql(
            "SELECT name FROM ir_model_data WHERE module = %s AND model = 'ir.ui.view'",
            self._module,
        )


def _is_record(obj):
    # type: (object) -> bool
    """Return whether an object is an Odoo record."""
    if odoo is None:
        return False
    return isinstance(obj, odoo.models.BaseModel) and hasattr(obj, "_ids")


def _is_field(obj):
    # type: (object) -> bool
    if odoo is None:
        return False
    return isinstance(obj, odoo.fields.Field)


class ConfigBrowser(object):
    """Access ir.config.parameter entries as attributes."""

    def __init__(self, path=""):
        self._path = path

    def __repr__(self):
        real = env["ir.config_parameter"].get_param(self._path)
        if real is False:
            return "<{}({})>".format(self.__class__.__name__, self._path)
        return repr(real)

    def __str__(self):
        return env["ir.config_parameter"].get_param(self._path)

    def __getattr__(self, attr):
        new = self._path + "." + attr if self._path else attr
        if env["ir.config_parameter"].search([("key", "=like", new + ".%")], limit=1):
            result = ConfigBrowser(new)
            setattr(self, attr, result)
            return result
        real = env["ir.config_parameter"].get_param(new)
        if real is not False:
            setattr(self, attr, real)
            return real
        raise AttributeError("No config parameter '{}'".format(attr))

    def __dir__(self):
        if not self._path:
            return env["ir.config_parameter"].search([]).mapped("key")
        return list(
            {
                result[len(self._path) + 1 :]
                for result in env["ir.config_parameter"]
                .search([("key", "=like", self._path + ".%")])
                .mapped("key")
            }
        )

    def __eq__(self, other):
        return self.__class__ is other.__class__ and self._path == other._path


class AddonBrowser(object):
    def __getattr__(self, attr):
        if not sql("SELECT name FROM ir_module_module WHERE name = %s", attr):
            raise AttributeError("No installed module '{}'".format(attr))
        addon = Addon(attr)
        setattr(self, attr, addon)
        return addon

    def __dir__(self):
        return sql("SELECT name FROM ir_module_module")


class Addon(object):
    def __init__(self, module):
        self.module = module
        self._record = None

    @property
    def manifest(self):
        return _AttributableDict(
            odoo.modules.module.load_information_from_description_file(self.module)
        )

    @property
    def record(self):
        if self._record is None:
            self._record = env["ir.module.module"].search([("name", "=", self.module)])
        return self._record

    @property
    def models(self):
        # TODO: return AddonModelBrowser with PartialModels that show the
        # fields (and methods?) added in the addon
        return [
            ModelProxy(name, nocomplete=True)
            for name in (
                env["ir.model"]
                .browse(
                    env["ir.model.data"]
                    .search([("model", "=", "ir.model"), ("module", "=", self.module)])
                    .mapped("res_id")
                )
                .mapped("model")
            )
        ]

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self.module)


class _AttributableDict(dict):
    def __getattr__(self, attr):
        try:
            val = self[attr]
        except KeyError:
            raise AttributeError
        if isinstance(val, dict):
            val = self.__class__(val)
        return val

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else set()
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
def _savepoint():
    # type: () -> t.Iterator[str]
    savepoint = "odoo_repl_savepoint_{}".format(next(_savepoint_count))
    env.cr.execute("SAVEPOINT {}".format(savepoint))
    try:
        yield savepoint
    except Exception:
        env.cr.execute("ROLLBACK TO SAVEPOINT {}".format(savepoint))
        raise
    else:
        env.cr.execute("RELEASE SAVEPOINT {}".format(savepoint))


class OPdb(pdb.Pdb, object):
    def __init__(
        self,
        completekey="tab",  # type: str
        stdin=None,  # type: t.Optional[t.IO[str]]
        stdout=None,  # type: t.Optional[t.IO[str]]
        skip=("odoo.api", "openerp.api"),  # type: t.Optional[t.Iterable[str]]
        **repl_args  # type: t.Any
    ):
        # type: (...) -> None
        super(OPdb, self).__init__(
            completekey=completekey, stdin=stdin, stdout=stdout, skip=skip
        )
        module = types.ModuleType(str("<opdb>"))  # py2 doesn't take unicode
        vars(module).clear()
        enable(module_name=module, **repl_args)
        self.repl_namespace = module
        self._real_curframe_locals = None  # type: t.Optional[t.Mapping]
        self._setup_framelocals()

    def displayhook(self, obj):
        # type: (object) -> None
        if obj is not None:
            if PY3:
                self.message(odoo_repr(obj))
            else:
                print(odoo_repr(obj), file=self.stdout)

    def setup(self, f, t):
        # type: (t.Optional[types.FrameType], t.Optional[types.TracebackType]) -> None
        global env
        super(OPdb, self).setup(f, t)
        # TODO: if there's an existing env it should be restored later
        if "self" in self.curframe_locals and isinstance(
            getattr(self.curframe_locals["self"], "env", None), odoo.api.Environment,
        ):
            env = self.curframe_locals["self"].env
        elif "cr" in self.curframe_locals and isinstance(
            self.curframe_locals["cr"], odoo.sql_db.Cursor
        ):
            env = odoo.api.Environment(
                self.curframe_locals["cr"], odoo.SUPERUSER_ID, {}
            )

    def _setup_framelocals(self):
        # type: () -> None
        if hasattr(self, "curframe_locals") and not isinstance(
            self.curframe_locals, collections.ChainMap
        ):
            self._real_curframe_locals = self.curframe_locals
            self.curframe_locals = collections.ChainMap(
                self.curframe_locals, vars(self.repl_namespace)
            )

    def precmd(self, line):
        # type: (str) -> str
        self._setup_framelocals()
        return super(OPdb, self).precmd(line)

    def do_sql(self, arg):
        # type: (str) -> None
        try:
            with _savepoint():
                env.cr.execute(arg)
                pprint.pprint(env.cr.fetchall())
        except Exception as err:
            # TODO: this might also be printed by the logging
            print(err)


def set_trace():
    # type: () -> None
    OPdb().set_trace(sys._getframe().f_back)


def post_mortem(traceback=None):
    # type: (types.TracebackType) -> None
    if traceback is None:
        traceback = sys.exc_info()[2]
        if traceback is None:
            raise ValueError(
                "A valid traceback must be passed if no exception is being handled"
            )
    debugger = OPdb()
    debugger.reset()
    debugger.interaction(None, traceback)


def pm():
    # type: () -> None
    post_mortem(sys.last_traceback)
