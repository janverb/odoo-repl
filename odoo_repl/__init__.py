# -*- coding: utf-8 -*-
# TODO:
# - access rights?
# - move ModelProxy.create()/.search() to MethodProxy
# - FieldProxy to be able to follow e.g. res.users.log_ids.create_date
# - refactor into submodules

from __future__ import print_function
from __future__ import unicode_literals

import collections
import importlib
import inspect
import logging
import os
import pprint
import random
import re
import subprocess
import sys

try:
    import __builtin__ as builtins
except ImportError:
    import builtins

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

from datetime import datetime
from functools import partial

PY3 = sys.version_info >= (3, 0)

Text = (str,) if PY3 else (str, unicode)
TextLike = (str, bytes) if PY3 else (str, unicode)

env = None
odoo = None

edit_bg = False


FIELD_BLACKLIST = {
    "__last_update",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
    "id",
}


def parse_config(argv):
    """Set up odoo.tools.config from command line arguments."""
    try:
        import openerp as odoo
    except ImportError:
        import odoo
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
    global env
    global odoo
    global edit_bg

    try:
        import openerp as odoo
    except ImportError:
        import odoo

    if module_name is None:
        try:
            module_name = sys._getframe().f_back.f_globals["__name__"]
        except Exception:
            pass
        if module_name in {None, "odoo_repl"}:
            print("Warning: can't determine module_name, assuming '__main__'")
            module_name = "__main__"

    if isinstance(module_name, Text):
        __main__ = importlib.import_module(module_name)
    else:
        __main__ = module_name

    if db is None or isinstance(db, Text):
        db_name = db or odoo.tools.config["db_name"]
        if not db_name:
            raise ValueError(
                "Can't determine database name. Run with `-d dbname`"
                "or pass it as the first argument to odoo_repl.enable()."
            )
        env = odoo.api.Environment(
            odoo.sql_db.db_connect(db_name).cursor(), odoo.SUPERUSER_ID, {}
        )
    elif isinstance(db, odoo.sql_db.Cursor):
        env = odoo.api.Environment(db, odoo.SUPERUSER_ID, {})
    elif isinstance(db, odoo.api.Environment):
        env = db
    else:
        raise TypeError(db)

    edit_bg = bg_editor

    if sys.version_info < (3, 0):
        readline_init(os.path.expanduser("~/.python2_history"))

    sys.displayhook = displayhook
    odoo.models.BaseModel._repr_pretty_ = _BaseModel_repr_pretty_
    odoo.models.BaseModel.edit_ = edit
    odoo.models.BaseModel.print_ = odoo_print
    odoo.fields.Field._repr_pretty_ = _Field_repr_pretty_
    odoo.fields.Field.edit_ = edit

    to_install = {
        "self": env.user,
        "odoo": odoo,
        "openerp": odoo,
        "browse": browse,
        "sql": sql,
        "env": EnvProxy(),
        "u": UserBrowser(),
        "cfg": ConfigBrowser(),
        "ref": DataBrowser(),
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
    global red, green, yellow, blue, purple, cyan
    red = green = yellow = blue = purple = cyan = lambda s: s
    field_colors.clear()


def readline_init(history=None):
    """Set up readline history and completion. Unnecessary in Python 3."""
    import atexit
    import readline
    import rlcompleter  # noqa: F401

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


def color_repr(owner, field_name):
    """Return a color-coded representation of an object."""
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
        if obj._name == "res.users":
            return ", ".join(cyan("u." + user.login) for user in obj)
        return cyan("{}{!r}".format(obj._name, list(obj._ids)))
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
    if isinstance(obj, (ModelProxy, MethodProxy)):
        obj = obj._real
    return obj


def odoo_repr(obj):
    if isinstance(obj, ModelProxy):
        return model_repr(obj)
    elif isinstance(obj, MethodProxy):
        return method_repr(obj)
    elif _is_record(obj):
        return record_repr(obj)
    elif _is_field(obj):
        return field_repr(obj)
    else:
        return repr(obj)


def odoo_print(obj):
    if _is_record(obj) and len(obj) > 1:
        print("\n\n".join(record_repr(record) for record in obj))
    else:
        print(odoo_repr(obj))


def model_repr(obj):
    """Summarize a model's fields."""
    if isinstance(obj, ModelProxy) and obj._real is None:
        return repr(obj)
    obj = _unwrap(obj)

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    parts.append(yellow(obj._name))
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
            "{}: ".format(green(field))
            # Like str.ljust, but not confused about colors
            + (max_len - len(field)) * " "
            + field_color(obj._fields[field])
            + " ({})".format(obj._fields[field].string)
        )
    if delegated:
        buckets = collections.defaultdict(list)
        for field in delegated:
            buckets[field.related[:-1]].append(
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


def record_repr(obj):
    """Display all of a record's fields."""
    obj = _unwrap(obj)

    if len(obj) == 0:
        return "{}[]".format(obj._name)
    elif len(obj) > 1:
        return "{}[{}]".format(obj._name, ", ".join(map(str, obj._ids)))

    fields = sorted(obj._fields)
    max_len = max(len(f) for f in fields)
    parts = []

    header = yellow("{}[{!r}]".format(obj._name, obj.id))
    # .get_external_id() returns at most one result per record
    for data_record in env["ir.model.data"].search(
        [("model", "=", obj._name), ("res_id", "=", obj.id)]
    ):
        header += " (ref.{}.{})".format(data_record.module, data_record.name)
    if obj.env.uid != 1:
        header += " (as u.{})".format(obj.env.user.login)
    parts.append(header)

    if not obj.exists():
        parts.append(red("Missing"))
        return "\n".join(parts)

    for field in fields:
        if field in FIELD_BLACKLIST:
            continue
        parts.append(
            "{}: ".format(green(field))
            + (max_len - len(field)) * " "
            + color_repr(obj, field)
        )
    return "\n".join(parts)


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
        parts[-1] += ": " + field.help

    if getattr(field, "related", False):
        parts.append("Delegated to {}".format(yellow(".".join(field.related))))
    elif field.compute is not None:
        func = field.compute
        if hasattr(func, "__func__"):
            func = func.__func__
        parts.append("Computed by {}".format(blue(func.__name__)))

    if getattr(field, "inverse_fields", False):
        parts.append(
            "Inverted by {}".format(
                ", ".join(yellow(inv.name) for inv in field.inverse_fields)
            )
        )

    if field.default:
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

        show_literal = False
        try:
            # Perhaps even nastier than the last one
            if getattr(default, "__name__", None) == "<lambda>":
                source = inspect.getsource(default).replace("\n", " ").strip()
                source = re.search("lambda [^:]*:(.*)", source).group(1).strip()
                try:
                    compile(source, "", "eval")
                except SyntaxError as err:
                    source = source[: err.offset - 1]
                    compile(source, "", "eval")
                default = purple(source)
                show_literal = True
        except Exception as err:
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


def method_repr(method):
    def find_decorators(method):
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

    sources = _find_method_source(method)
    model = method.model
    name = method.name
    model_class = type(model)
    method = method._real
    decorators = list(find_decorators(method))

    while hasattr(method, "_orig"):
        method = method._orig
    if hasattr(method, "__func__"):
        method = method.__func__

    signature = (
        str(inspect.signature(method))
        if PY3
        else inspect.formatargspec(*inspect.getargspec(method))
    )
    doc = inspect.getdoc(method)
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
            pat = re.compile(
                r"""^\s*['"]?{}['"]?\s*[:=]\s*fields\.""".format(field.name)
            )
            for line in lines:
                if pat.match(line):
                    break
                lnum += 1
            else:
                lnum = None
            res.append((cls._module, fname, lnum))
    return res


def _find_method_source(method):
    def unpack(meth):
        return getattr(meth, "_orig", meth)

    return [
        (
            getattr(cls, "_module", cls.__name__),
            inspect.getsourcefile(cls),
            inspect.getsourcelines(unpack(getattr(cls, method.name)))[1],
        )
        for cls in type(method.model).mro()[1:]
        # if cls._name != "base"
        if method.name in vars(cls)
    ]


def _BaseModel_repr_pretty_(self, printer, cycle):
    if printer.indentation == 0 and hasattr(self, "_ids"):
        printer.text(record_repr(self))
    else:
        printer.text(repr(self))


def _Field_repr_pretty_(self, printer, cycle):
    if printer.indentation == 0 and hasattr(self, "model_name"):
        printer.text(field_repr(self))
    elif not hasattr(self, "model_name"):
        printer.text("<Undisplayable field>")  # Work around bug
    else:
        printer.text(repr(self))


def oprint(obj):
    """Display all records in a set, even if there are a lot."""
    print("\n\n".join(record_repr(record) for record in obj))


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
        return ModelProxy(ind)

    def __eq__(self, other):
        return self.__class__ is other.__class__

    def _ipython_key_completions_(self):
        return env.registry.keys()


class ModelProxy(object):
    """A wrapper around an Odoo model.

    Records can be browsed with indexing syntax, other models can be used
    with tab-completed attribute access, there are added convenience methods,
    and instead of an ordinary repr a summary of the fields is shown.
    """

    def __init__(self, path):
        self._path = path
        self._real = env[path] if path in env.registry else None

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError
        new = self._path + "." + attr
        if new in env.registry:
            return self.__class__(new)
        if any(m.startswith(new + ".") for m in env.registry):
            return self.__class__(new)
        if self._real is None:
            raise AttributeError("Model '{}' does not exist".format(new))
        if attr in self._real._fields:
            return self._real._fields[attr]
        thing = getattr(self._real, attr)
        if callable(thing):
            thing = MethodProxy(thing, self._real, attr)
        return thing

    def __dir__(self):
        listing = set(super().__dir__()) if PY3 else set()
        if self._real is None:
            listing -= {"create", "search"}
        else:
            listing.update(
                attr for attr in dir(self._real) if not attr.startswith("__")
            )
            # https://github.com/odoo/odoo/blob/5cdfd53d/odoo/models.py#L341 adds a
            # bogus attribute that's annoying for tab completion
            listing -= {"<lambda>"}
        listing.update(
            mod[len(self._path) + 1 :].split(".", 1)[0]
            for mod in env.registry
            if mod.startswith(self._path + ".")
        )
        return sorted(listing)

    def __repr__(self):
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, cycle):
        if self._real is not None and printer.indentation == 0:
            printer.text(model_repr(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(self, ind):
        if self._real is None:
            return IndexError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if ind in self._real._fields:
            return self._real._fields[ind]
        if isinstance(ind, Text):
            thing = getattr(self._real, ind)
            if callable(thing):
                return MethodProxy(thing, self._real, ind)
            return thing
        if isinstance(ind, (list, set)):
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

    def search(self, args=(), offset=0, limit=None, order="id", count=False, **kwargs):
        """Perform a quick and dirty search.

        .search(x='test', y=<some record>) is roughly equivalent to
        .search([('x', '=', 'test'), ('y', '=', <some record>.id)]).
        .search() gets all records.
        """
        self._ensure_real()
        args = list(args)
        # TODO: inspect fields
        args.extend((k, "=", getattr(v, "id", v)) for k, v in kwargs.items())
        return self._real.search(
            args, offset=offset, limit=limit, order=order, count=count
        )

    def create(self, vals=(), **kwargs):
        """Create a new record, optionally with keyword arguments."""
        self._ensure_real()
        kwargs.update(vals)
        for key, value in kwargs.items():
            if key not in self._real._fields:
                raise TypeError("Field '{}' does not exist".format(key))
            if _is_record(value) or (
                isinstance(value, (list, tuple)) and value and _is_record(value[0])
            ):
                # TODO: typecheck model
                field_type = self._real._fields[key].type
                if field_type.endswith("2many"):
                    kwargs[key] = [(4, record.id) for record in value]
                elif field_type.endswith("2one"):
                    if len(value) > 1:
                        raise TypeError(
                            "Can't link multiple records for '{}'".format(key)
                        )
                    kwargs[key] = value.id
        return self._real.create(kwargs)

    def _all_ids_(self):
        """Get all record IDs in the database."""
        self._ensure_real()
        return sql("SELECT id FROM {}".format(env[self._path]._table))

    def _mod_(self):
        """Get the ir.model record of the model."""
        self._ensure_real()
        return env["ir.model"].search([("model", "=", self._path)])

    def _shuf_(self, num=1):
        """Return a random record, or multiple."""
        self._ensure_real()
        return self._real.browse(random.sample(self._all_ids_(), num))


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
        listing = set(super().__dir__()) if PY3 else {"edit_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        return "{}({!r}, {!r}, {!r})".format(
            self.__class__.__name__, self._real, self.model, self.name
        )

    def _repr_pretty_(self, printer, cycle):
        if printer.indentation == 0:
            printer.text(method_repr(self))
        else:
            printer.text(repr(self))

    edit_ = edit


def sql(query, *args):
    """Execute a SQL query and try to make the result nicer.

    Optimized for ease of use, at the cost of performance and boringness.
    """
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


class UserBrowser(object):
    """Easy access to records of user accounts.

    Usage:
    >>> u.admin
    res.users[1]
    >>> u[1]
    res.users[1]

    >>> u.adm<TAB> completes to u.admin

    >>> record.sudo(u.testemployee1)  # View a record as testemployee1
    """

    def __getattr__(self, attr):
        # IPython does completions in a separate thread.
        # Odoo doesn't like that. So completions on attributes of `u` fail.
        # We can solve that sometimes by remembering things we've completed
        # before.
        user = env["res.users"].search([("login", "=", attr)])
        if not user:
            raise AttributeError("User '{}' not found".format(attr))
        setattr(self, attr, user)
        return user

    def __dir__(self):
        return sql("SELECT login FROM res_users")

    def __eq__(self, other):
        return self.__class__ is other.__class__

    __getitem__ = __getattr__
    _ipython_key_completions_ = __dir__


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


def _is_record(obj):
    """Return whether an object is an Odoo record."""
    if odoo is None:
        return False
    return isinstance(obj, odoo.models.BaseModel) and hasattr(obj, "_ids")


def _is_field(obj):
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
