# -*- coding: utf-8 -*-
# TODO:
# - FieldProxy to be able to follow e.g. res.users.log_ids.create_date
# - group by implementing module in model_repr
# - .write_()
# - rename without "odoo" (trademark? CONTRIBUTING.rst#821naming)
# - don't treat mixins as base
# - things like constrainers as attributes on field(proxy)
# - unify .source_() and .edit_() more so you can e.g. do .source_(-1)
# - show .search in field_repr/as attr on FieldProxy
# - at least document optional pygments dependency
# - put shuf_() on BaseModel
# - toggle to start pdb on log message (error/warning/specific message)
# - grep_ on XML records, for completeness
# - use stdlib xml instead of lxml

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

import atexit
import collections
import functools
import importlib
import inspect
import keyword
import logging
import os
import pprint
import random
import re
import string
import subprocess
import sys
import threading
import types

from odoo_repl import access
from odoo_repl import addons
from odoo_repl import color
from odoo_repl import forensics
from odoo_repl import grep
from odoo_repl import opdb
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import (
    PY3,
    abc,
    odoo,
    t,
    Text,
    builtins,
    Field,
    StringIO,
)
from odoo_repl.opdb import set_trace, post_mortem, pm

__all__ = ("odoo_repr", "enable", "set_trace", "post_mortem", "pm", "forensics", "opdb")

edit_bg = False


FIELD_BLACKLIST = {
    # These are on all models
    "__last_update",
    "display_name",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
    "id",
}

FIELD_VALUE_BLACKLIST = {
    # Showing these by default feels icky
    "password",
    "password_crypt",
}


def parse_config(argv):
    # type: (t.List[t.Text]) -> None
    """Set up odoo.tools.config from command line arguments."""
    logging.getLogger().handlers = []
    odoo.netsvc._logger_init = False
    odoo.tools.config.parse_config(argv)


def create_namespace(
    db,  # type: t.Union[None, t.Text, odoo.sql_db.Cursor, odoo.api.Environment]
):
    # type: (...) -> t.Tuple[odoo.api.Environment, t.Dict[str, t.Any]]
    global xml_thread
    if db is None or isinstance(db, Text):
        db_name = db or odoo.tools.config["db_name"]
        if not db_name:
            raise ValueError(
                "Can't determine database name. Run with `-d dbname` "
                "or pass it as the first argument to odoo_repl.enable()."
            )
        cursor = odoo.sql_db.db_connect(db_name).cursor()
        atexit.register(cursor.close)
        if not hasattr(odoo.api.Environment._local, "environments"):
            odoo.api.Environment._local.environments = odoo.api.Environments()
        env = odoo.api.Environment(cursor, odoo.SUPERUSER_ID, {})
    elif isinstance(db, odoo.sql_db.Cursor):
        env = odoo.api.Environment(db, odoo.SUPERUSER_ID, {})
    elif isinstance(db, odoo.api.Environment):
        env = db
    else:
        raise TypeError(db)

    envproxy = EnvProxy(env)
    util.env = env

    def grep_(*args, **kwargs):
        # type: (object, object) -> None
        """grep through all installed addons.

        See help(odoo_repl.grep) for more information.
        """
        argv = grep.build_grep_argv(args, kwargs, recursive=True)
        argv.extend(
            filter(
                None,
                map(
                    odoo.modules.module.get_module_path,
                    util.sql(
                        env,
                        "SELECT name FROM ir_module_module WHERE state = 'installed'",
                    ),
                ),
            )
        )
        subprocess.Popen(argv).wait()

    def translate(text):
        # type: (t.Text) -> None
        translations = env["ir.translation"].search(
            ["|", ("src", "=", text), ("value", "=", text)]
        )
        if not translations:
            text = "%" + text + "%"
            translations = env["ir.translation"].search(
                ["|", ("src", "ilike", text), ("value", "ilike", text)]
            )
        odoo_print(translations)

    namespace = {
        "self": env.user,
        "odoo": odoo,
        "openerp": odoo,
        "sql": functools.partial(util.sql, env),
        "grep_": grep_,
        "translate": translate,
        "env": envproxy,
        "u": UserBrowser(env),
        "emp": EmployeeBrowser(env),
        "ref": DataBrowser(env),
        "addons": addons.AddonBrowser(env),
    }  # type: t.Dict[str, t.Any]
    namespace.update({part: ModelProxy(env, part) for part in envproxy._base_parts()})

    if not sources.xml_records:
        modules = util.sql(
            env, "SELECT name, demo FROM ir_module_module WHERE state = 'installed'",
        )
        xml_thread = threading.Thread(
            target=lambda: sources.populate_xml_records(modules)
        )
        xml_thread.daemon = True
        xml_thread.start()

    return env, namespace


xml_thread = None  # type: t.Optional[threading.Thread]


def enable(
    db=None,  # type: t.Union[None, t.Text, odoo.sql_db.Cursor, odoo.api.Environment]
    module=None,  # type: t.Union[None, t.Text, types.ModuleType]
    with_color=True,  # type: bool
    bg_editor=False,  # type: bool
):
    # type: (...) -> None
    """Enable all the bells and whistles.

    :param db: Either an Odoo environment object, an Odoo cursor, a database
               name, or ``None`` to guess the database to use.
    :param module: Either a module, the name of a module, or ``None`` to
                   install into the module of the caller.
    :param bool with_color: Enable colored output.
    :param bool bg_editor: Don't wait for text editors invoked by ``.edit()``
                           to finish.
    """
    global edit_bg

    if module is None:
        target_ns = sys._getframe().f_back.f_globals
    elif isinstance(module, Text):
        target_ns = vars(importlib.import_module(module))
    else:
        target_ns = vars(module)

    env_, to_install = create_namespace(db)

    atexit.register(env_.cr.close)

    edit_bg = bg_editor

    sys.displayhook = displayhook

    # Whenever this would be useful you should probably just use OPdb directly
    # But maybe there are cases in which it's hard to switch out pdb
    # TODO: It should probably run iff odoo_repl.enable() is called from pdb

    # pdb.Pdb.displayhook = OPdb.displayhook

    for name, obj in to_install.items():
        if not hasattr(builtins, name) and (
            name not in target_ns or type(target_ns[name]) is type(obj)
        ):
            target_ns[name] = obj

    if not with_color:
        color.enabled = False


def _color_repr(owner, field_name):
    # type: (odoo.models.BaseModel, t.Text) -> t.Text
    """Return a color-coded representation of a record's field value."""
    if hasattr(owner.env, "prefetch"):  # Not all Odoo versions
        # The prefetch cache may be filled up by previous calls, see record_repr
        owner.env.prefetch.clear()
    try:
        obj = getattr(owner, field_name)  # type: object
    except Exception as err:
        return color.missing(repr(err))
    field_type = owner._fields[field_name].type
    return color.color_value(obj, field_type)


def odoo_repr(obj):
    # type: (object) -> t.Text
    if isinstance(obj, ModelProxy):
        return model_repr(obj)
    elif isinstance(obj, MethodProxy):
        return method_repr(obj)
    elif isinstance(obj, FieldProxy):
        return field_repr(obj._env, obj._real)
    elif isinstance(obj, odoo.models.BaseModel):
        return record_repr(obj)
    elif isinstance(obj, addons.Addon):
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
    # type: (Field) -> t.Text
    return "".join(
        attr[0] if getattr(field, attr, False) else " "
        for attr in ["required", "store", "default"]
    ) + ("c" if _has_computer(field) else " ")


def model_repr(obj):
    # type: (t.Union[ModelProxy, odoo.models.BaseModel]) -> t.Text
    """Summarize a model's fields."""
    if isinstance(obj, ModelProxy) and obj._real is None:
        return repr(obj)
    obj = util.unwrap(obj)

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
    parts.extend(sources.format_sources(sources.find_source(obj)))
    return "\n".join(parts)


def _xml_id_tag(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    return "".join(" ({})".format(xml_id.to_ref()) for xml_id in util.xml_ids(obj))


def _record_header(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    header = color.header("{}[{!r}]".format(obj._name, obj.id)) + _xml_id_tag(obj)
    if obj.env.uid != 1:
        header += " (as {})".format(UserBrowser._repr_for_value(obj.env.user.login))
    return header


def _ids_repr(idlist):
    # type: (t.Iterable[object]) -> t.Text
    fragments = []  # type: t.List[t.Text]
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
    obj = util.unwrap(obj)

    if not obj:
        return "{}[]".format(obj._name)
    elif len(obj) > 1:
        return "{}[{}]".format(obj._name, _ids_repr(obj._ids))

    if obj.env.cr.closed:
        return "{}[{}] (closed cursor)".format(obj._name, _ids_repr(obj._ids))

    fields = sorted(
        field
        for field in obj._fields
        if field not in FIELD_BLACKLIST
        and field not in FIELD_VALUE_BLACKLIST
        and not obj._fields[field].related
    )
    max_len = max(len(f) for f in fields) if fields else 0
    parts = []

    parts.append(_record_header(obj))
    name = obj.display_name
    default_name = "{},{}".format(obj._name, obj.id)
    if name and name != default_name:
        parts.append(color.display_name(name))

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

    history_lines = []
    if obj.create_date:
        create_msg = "Created on {}".format(color.format_date(obj.create_date))
        if obj.create_uid and obj.create_uid.id != 1:
            create_msg += " by {}".format(color.render_user(obj.create_uid))
        history_lines.append(create_msg)
    if obj.write_date and obj.write_date != obj.create_date:
        write_msg = "Written on {}".format(color.format_date(obj.write_date))
        if obj.write_uid and obj.write_uid.id != 1:
            write_msg += " by {}".format(color.render_user(obj.write_uid))
        history_lines.append(write_msg)
    if history_lines:
        parts.append("")
        parts.extend(history_lines)

    src = sources.find_source(obj)
    if src:
        parts.append("")
        parts.extend(sources.format_sources(src))

    return "\n".join(parts)


def _has_computer(field):
    # type: (Field) -> bool
    return (
        field.compute is not None
        or type(getattr(field, "column", None)).__name__ == "function"
    )


def _find_computer(env, field):
    # type: (odoo.api.Environment, Field) -> object
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


def _find_field_default(model, field):
    # type: (odoo.models.BaseModel, Field) -> object
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


def field_repr(env, field):
    # type: (odoo.api.Environment, t.Union[FieldProxy, Field]) -> t.Text
    """List detailed information about a field."""
    # TODO:
    # - .groups, .copy, .states, .inverse, .column[12]
    field = util.unwrap(field)
    model = env[field.model_name]
    record = env["ir.model.fields"].search(
        [("model", "=", field.model_name), ("name", "=", field.name)]
    )
    if len(record) > 1:
        # This is rare, but apparently valid
        # TODO: pick intelligently based on MRO
        record = record[-1]
    elif not record:
        return color.missing("No ir.model.fields record found for field")
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
        func = _find_computer(env, field)
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
        default = _find_field_default(model, field)

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
        sel = pprint.pformat(field.selection)  # type: t.Text
        if isinstance(field.selection, list):
            sel = color.highlight(sel)
        parts.append(sel)

    src = sources.find_source(field)
    parts.extend(sources.format_sources(src))

    if not src and record.modules:
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


def _func_signature(func):
    # type: (t.Callable[..., t.Any]) -> t.Text
    if PY3:
        return str(inspect.signature(func))
    else:
        return inspect.formatargspec(*inspect.getargspec(func))


def method_repr(methodproxy):
    # type: (MethodProxy) -> t.Text
    src = sources.find_method_source(methodproxy)
    model = methodproxy.model
    name = methodproxy.name

    method = methodproxy._real
    decorators = list(_find_decorators(method))
    method = util.unpack_function(method)

    signature = _func_signature(method)
    doc = inspect.getdoc(method)  # type: t.Optional[t.Text]
    if not doc:
        # inspect.getdoc() can't deal with Odoo's unorthodox inheritance
        for cls in type(model).__mro__:
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
    parts.extend(sources.format_sources(src))
    return "\n".join(parts)


def edit(thing, index=-1, bg=None):
    # type: (sources.Sourceable, t.Union[int, t.Text], t.Optional[bool]) -> None
    """Open a model or field definition in an editor."""
    # TODO: editor kwarg and/or argparse flag
    if bg is None:
        bg = edit_bg
    src = sources.find_source(thing)
    if not src:
        raise RuntimeError("Can't find source file!")
    if isinstance(index, int):
        try:
            module, fname, lnum = src[index]
        except IndexError:
            raise RuntimeError("Can't find match #{}".format(index))
    elif isinstance(index, Text):
        for module, fname, lnum in src:
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
    argv.append(str(fname))
    if bg:
        # os.setpgrp avoids KeyboardInterrupt/SIGINT
        subprocess.Popen(argv, preexec_fn=os.setpgrp)
    else:
        subprocess.Popen(argv).wait()


def _BaseModel_repr_pretty_(self, printer, _cycle):
    # type: (odoo.models.BaseModel, t.Any, t.Any) -> None
    if printer.indentation == 0 and hasattr(self, "_ids"):
        printer.text(record_repr(self))
    else:
        printer.text(repr(self))


def displayhook(obj):
    # type: (object) -> None
    """A sys.displayhook replacement that pretty-prints models and records."""
    if obj is not None:
        rep = odoo_repr(obj)
        if not PY3 and isinstance(sys.stdout, StringIO):
            # Printing unicode causes issues in Pyrasite
            rep = rep.replace(u"×", "x")
            rep = rep.encode("ascii", errors="backslashreplace")
        print(rep)
        builtins._ = obj  # type: ignore


class EnvProxy(object):
    """A wrapper around an odoo.api.Environment object.

    Models returned by indexing will be wrapped in a ModelProxy for nicer
    behavior. Models can also be accessed as attributes, with tab completion.
    """

    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env
        self.ref = DataBrowser(env)

    def __getattr__(self, attr):
        # type: (str) -> t.Any
        if attr.startswith("__"):
            raise AttributeError
        if hasattr(self._env, attr):
            return getattr(self._env, attr)
        if attr in self._base_parts():
            return ModelProxy(self._env, attr)
        raise AttributeError

    def __dir__(self):
        # type: () -> t.List[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"_base_parts"}  # type: t.Set[t.Text]
        listing.update(self._base_parts())
        listing.update(attr for attr in dir(self._env) if not attr.startswith("__"))
        return sorted(listing)

    def _base_parts(self):
        # type: () -> t.List[str]
        return list({mod.split(".", 1)[0] for mod in self._env.registry})

    def __repr__(self):
        # type: () -> str
        return "{}({!r})".format(self.__class__.__name__, self._env)

    def __getitem__(self, ind):
        # type: (t.Text) -> ModelProxy
        if ind not in self._env.registry:
            raise KeyError("Model '{}' does not exist".format(ind))
        return ModelProxy(self._env, ind, nocomplete=True)

    def __iter__(self):
        # type: () -> t.Iterator[ModelProxy]
        for mod in self._env.registry:
            yield self[mod]

    def __eq__(self, other):
        # type: (object) -> bool
        return isinstance(other, self.__class__) and self._env == other._env

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        return list(self._env.registry)


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
    self,  # type: t.Union[odoo.models.BaseModel, ModelProxy]
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
    self = util.unwrap(self)
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
        all_ids = util.sql(self.env, query)
        shuf = min(shuf, len(all_ids))
        return self.browse(random.sample(all_ids, shuf))
    clauses = _parse_search_query(args, fields)
    result = self.search(clauses, offset=offset, limit=limit, order=order, count=count)
    if shuf:
        shuf = min(shuf, len(result))
        return result.browse(random.sample(result._ids, shuf))
    return result


def _BaseModel_filtered_(
    self,  # type: odoo.models.AnyModel
    func=None,  # type: t.Optional[t.Callable[[odoo.models.AnyModel], bool]]
    **fields  # type: object
):
    # type: (...) -> odoo.models.AnyModel
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

    def __init__(self, env, path, nocomplete=False):
        # type: (odoo.api.Environment, t.Text, bool) -> None
        self._env = env
        self._path = path
        self._real = env[path] if path in env.registry else None
        if nocomplete and self._real is None:
            raise ValueError("Model '{}' does not exist".format(self._path))
        self._nocomplete = nocomplete

    def __getattr__(self, attr):
        # type: (t.Text) -> t.Any
        if attr.startswith("__"):
            raise AttributeError
        if not self._nocomplete:
            new = self._path + "." + attr
            if new in self._env.registry:
                return self.__class__(self._env, new)
            if any(m.startswith(new + ".") for m in self._env.registry):
                return self.__class__(self._env, new)
        if self._real is None:
            raise AttributeError("Model '{}' does not exist".format(new))
        if attr in self._real._fields:
            return FieldProxy(self._env, self._real._fields[attr])
        thing = getattr(self._real, attr)  # type: object
        if callable(thing) and hasattr(type(self._real), attr):
            thing = MethodProxy(thing, self._real, attr)
        return thing

    def __dir__(self):
        # type: () -> t.List[t.Text]
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
            for mod in self._env.registry
            if mod.startswith(self._path + ".")
        )
        return sorted(listing)

    def __iter__(self):
        # type: () -> t.Iterator[FieldProxy]
        assert self._real is not None
        for field in sorted(self._real._fields.values(), key=lambda f: f.name):
            yield FieldProxy(self._env, field)

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
        # type: () -> str
        if self._real is not None:
            return "{}[]".format(self._path)
        return "<{}({})>".format(self.__class__.__name__, self._path)

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if self._real is not None and printer.indentation == 0:
            printer.text(model_repr(self._real))
        else:
            printer.text(repr(self))

    def __getitem__(self, ind):
        # type: (t.Union[t.Iterable[int], t.Text, int]) -> t.Any
        if self._real is None:
            raise KeyError("Model '{}' does not exist".format(self._path))
        if not ind:
            return self._real
        if isinstance(ind, Text):
            if ind in self._real._fields:
                return FieldProxy(self._env, self._real._fields[ind])
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
            util.sql(
                self._env,
                'SELECT id FROM "{}" WHERE id IN %s'.format(self._real._table),
                ind,
            )
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
        return list(self._real._fields) + dir(self._real)  # type: ignore

    def _ensure_real(self):
        # type: () -> None
        if self._real is None:
            raise TypeError("Model '{}' does not exist".format(self._path))

    def _all_ids_(self):
        # type: () -> t.List[int]
        """Get all record IDs in the database."""
        self._ensure_real()
        return util.sql(
            self._env, "SELECT id FROM {}".format(self._env[self._path]._table)
        )

    def mod_(self):
        # type: () -> odoo.models.IrModel
        """Get the ir.model record of the model."""
        self._ensure_real()
        return self._env["ir.model"].search([("model", "=", self._path)])

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
            if location is not None and util.module(cls) != location:
                continue
            if location is None and name != self._real._name:
                continue
            print(sources.format_source(sources.Source.from_cls(cls)))
            print(color.highlight(inspect.getsource(cls)))

    def rules_(self, user=None):
        # type: (t.Optional[odoo.models.ResUsers]) -> None
        # TODO: is it possible to collapse the rules into a single policy for a user?
        mod_id = self.mod_().id
        parts = []  # type: t.List[t.Text]
        parts.extend(
            access.access_repr(acc)
            for acc in access.access_for_model(self._env, mod_id, user)
        )
        parts.extend(
            access.rule_repr(rule)
            for rule in access.rules_for_model(self._env, mod_id, user)
        )
        print("\n\n".join(parts))

    def view_(
        self,
        user=None,  # type: t.Optional[t.Union[t.Text, int, odoo.models.ResUsers]]
        **kwargs  # type: t.Any
    ):
        # type: (...) -> None
        """Build up and print a view as a user.

        Takes the same arguments as ir.model.fields_view_get, notably
        view_id and view_type.
        """
        assert self._real is not None
        context = kwargs.pop("context", None)
        kwargs.setdefault("view_type", "form")
        model = self._real
        if user is not None:
            # TODO: handle viewing as group
            model = model.sudo(_to_user(self._env, user))
        if context is not None:
            model = model.with_context(context)
        form = model.fields_view_get(**kwargs)["arch"]
        try:
            import lxml.etree
        except ImportError:
            pass
        else:
            form = lxml.etree.tostring(
                lxml.etree.fromstring(
                    form, lxml.etree.XMLParser(remove_blank_text=True)
                ),
                pretty_print=True,
                encoding="unicode",
            )
        print(color.highlight(form, "xml"))

    def sql_(self):
        # type: () -> None
        """Display basic PostgreSQL information about stored fields."""
        # TODO: make more informative
        assert self._real is not None
        with util.savepoint(self._env.cr):
            self._env.cr.execute("SELECT * FROM {} LIMIT 0;".format(self._real._table))
            columns = self._env.cr.description
        print(self._real._table)
        for name in sorted(c.name for c in columns):
            print("  {}".format(name))

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through the combined source code of the model.

        See help(odoo_repl.grep) for more information.
        """
        assert self._real is not None
        # TODO: handle multiple classes in single file properly
        argv = grep.build_grep_argv(args, kwargs)
        seen = set()  # type: t.Set[t.Text]
        for src in sources.find_source(self._real):
            if src.fname not in seen:
                seen.add(src.fname)
                argv.append(src.fname)
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
                print(color.module(util.module(cls)))
                for name, meth in meths:
                    print(
                        color.method(name) + _func_signature(util.unpack_function(meth))
                    )

    _ = _BaseModel_search_


def _to_user(
    env,  # type: odoo.api.Environment
    user,  # type: t.Union[odoo.models.BaseModel, t.Text, int]
):
    # type: (...) -> odoo.models.ResUsers
    if isinstance(user, Text):
        login = user
        user = env["res.users"].search([("login", "=", login)])
        if len(user) != 1:
            raise ValueError("No user {!r}".format(login))
        return user
    elif isinstance(user, int):
        return env["res.users"].browse(user)
    if not isinstance(user, odoo.models.BaseModel):
        raise ValueError("Can't convert type of {!r} to user".format(user))
    if user._name == "res.users":
        return user  # type: ignore
    candidate = getattr(user, "user_id", user)
    if getattr(candidate, "_name", None) != "res.users":
        raise ValueError("{!r} is not a user".format(candidate))
    return candidate  # type: ignore


class MethodProxy(object):
    def __init__(self, method, model, name):
        # type: (t.Callable[..., t.Any], odoo.models.BaseModel, t.Text) -> None
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
        # type: () -> t.List[str]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"edit_", "source_", "grep_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        # type: () -> str
        return "{}({!r}, {!r}, {!r})".format(
            self.__class__.__name__, self._real, self.model, self.name
        )

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0:
            printer.text(method_repr(self))
        else:
            printer.text(repr(self))

    edit_ = edit

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        for cls in type(self.model).__mro__[1:]:
            module = util.module(cls)
            if location is not None and location != module:
                continue
            if self.name in vars(cls):
                func = util.unpack_function(vars(cls)[self.name])
                fname = inspect.getsourcefile(func) or "???"
                lines, lnum = inspect.getsourcelines(func)
                print(sources.format_source(sources.Source(module, fname, lnum)))
                print(color.highlight("".join(lines)))

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through all of the method's definitions, ignoring other file content.

        See ModelProxy.grep_ for options.

        The implementation is hacky. If you get weird results it's probably not
        your fault.
        """
        argv = grep.build_grep_argv(args, kwargs)
        first = True
        for cls in type(self.model).__mro__[1:]:
            if self.name in vars(cls):
                func = util.unpack_function(vars(cls)[self.name])
                try:
                    grep.partial_grep(argv, func)
                except grep.BadCommandline as err:
                    print(err, file=sys.stderr)
                    return
                except grep.NoResults:
                    continue
                else:
                    if not first:
                        print()
                    else:
                        first = False


class FieldProxy(object):
    def __init__(self, env, field):
        # type: (odoo.api.Environment, Field) -> None
        self._env = env
        self._real = field

    def __getattr__(self, attr):
        # type: (str) -> object
        if attr.startswith("__"):
            raise AttributeError
        return getattr(self._real, attr)

    def __dir__(self):
        # type: () -> t.List[str]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"source_"}
        listing.update(dir(self._real))
        return sorted(listing)

    def __repr__(self):
        # type: () -> str
        return repr(self._real)

    def _repr_pretty_(self, printer, cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0 and hasattr(self._real, "model_name"):
            printer.text(field_repr(self._env, self._real))
        elif not hasattr(self, "model_name"):
            printer.text("<Undisplayable field>")  # Work around bug
        else:
            printer.text(repr(self._real))

    def source_(self, location=None):
        # type: (t.Optional[t.Text]) -> None
        for source in sources.find_source(self._real):
            if location is not None and location != source.module:
                continue
            if source.lnum is None:
                print(
                    "This field is defined somewhere in {!r} "
                    "but we don't know where".format(source.fname)
                )
                continue
            print(sources.format_source(source))
            print(
                color.highlight(sources.extract_field_source(source.fname, source.lnum))
            )

    def _make_method_proxy_(self, func):
        # type: (object) ->  object
        if not callable(func):
            return func
        name = getattr(func, "__name__", False)
        if not name:
            return func
        model = self._env[self._real.model_name]
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
        return self._make_method_proxy_(_find_computer(self._env, self._real))

    @property
    def default(self):
        # type: () -> object
        if not self._real.default:
            raise AttributeError
        return self._make_method_proxy_(
            _find_field_default(self._env[self._real.model_name], self._real)
        )


class RecordBrowser(object):
    _model = NotImplemented  # type: str
    _field = NotImplemented  # type: str
    _listing = NotImplemented  # type: str
    _abbrev = NotImplemented  # type: str

    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            thing = self._env[self._model].search([(self._field, "=", attr)])
        except AttributeError as err:
            if err.args == ("environments",) and not attr.startswith("_"):
                # This happens when IPython runs completions in a separate thread
                # Returning an empty record means it can complete without making
                # queries, even across relations
                # When the line is actually executed __getattr__ will run again
                # We check for an underscore at the start to exclude both
                # dunder attributes and _ipython special methods
                # Even if a username does start with an underscore this is
                # acceptable because it only breaks completion
                return self._env[self._model]
            raise
        if not thing:
            raise AttributeError("Record '{}' not found".format(attr))
        return thing

    def __dir__(self):
        # type: () -> t.List[t.Text]
        if self._model not in self._env.registry:
            raise TypeError("Model '{}' is not installed".format(self._model))
        return [u"_model", u"_field", u"_listing", u"_abbrev"] + util.sql(
            self._env, self._listing
        )

    def __eq__(self, other):
        # type: (object) -> bool
        return isinstance(other, self.__class__) and self._env == other._env

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

    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env

    def __getattr__(self, attr):
        # type: (t.Text) -> DataModuleBrowser
        if not util.sql(
            self._env, "SELECT id FROM ir_model_data WHERE module = %s LIMIT 1", attr
        ):
            raise AttributeError("No module '{}'".format(attr))
        browser = DataModuleBrowser(self._env, attr)
        setattr(self, attr, browser)
        return browser

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return util.sql(self._env, "SELECT DISTINCT module FROM ir_model_data")

    def __call__(self, query):
        # type: (t.Text) -> odoo.models.BaseModel
        return self._env.ref(query)

    def __eq__(self, other):
        # type: (object) -> bool
        return isinstance(other, self.__class__) and self._env == other._env


class DataModuleBrowser(object):
    """Access data records within a module. Created by DataBrowser."""

    def __init__(self, env, module):
        # type: (odoo.api.Environment, t.Text) -> None
        self._env = env
        self._module = module

    def __getattr__(self, attr):
        # type: (t.Text) -> odoo.models.BaseModel
        try:
            record = self._env.ref("{}.{}".format(self._module, attr))
        except ValueError as err:
            raise AttributeError(err)
        except AttributeError as err:
            if err.args == ("environments",) and not attr.startswith("_"):
                # Threading issue, try to keep autocomplete working
                # See RecordBrowser.__getattr__
                model = util.sql(
                    self._env,
                    "SELECT model FROM ir_model_data WHERE module = %s AND name = %s",
                    self._module,
                    attr,
                )  # type: t.List[str]
                return self._env[model[0]]
            raise
        setattr(self, attr, record)
        return record

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return util.sql(
            self._env, "SELECT name FROM ir_model_data WHERE module = %s", self._module
        )


def _is_record(obj):
    # type: (object) -> bool
    """Return whether an object is an Odoo record."""
    return isinstance(obj, odoo.models.BaseModel) and hasattr(obj, "_ids")


def _BaseModel_source_(record, location=None, context=False):
    # type: (odoo.models.BaseModel, t.Optional[t.Text], bool) -> None
    import lxml.etree

    for rec in record:
        for rec_id in util.xml_ids(rec):
            for definition in sources.xml_records[rec_id]:
                if location is not None and definition.module != location:
                    continue
                elem = definition.elem.getroottree() if context else definition.elem
                print(sources.format_source(definition.to_source()))
                src = lxml.etree.tostring(elem, encoding="unicode")
                print(color.highlight(src, "xml"))


try:
    odoo.models.BaseModel._repr_pretty_ = _BaseModel_repr_pretty_  # type: ignore
    odoo.models.BaseModel.edit_ = edit  # type: ignore
    odoo.models.BaseModel.print_ = odoo_print  # type: ignore
    odoo.models.BaseModel.search_ = _BaseModel_search_  # type: ignore
    odoo.models.BaseModel.create_ = _BaseModel_create_  # type: ignore
    odoo.models.BaseModel.filtered_ = _BaseModel_filtered_  # type: ignore
    odoo.models.BaseModel.source_ = _BaseModel_source_  # type: ignore
    odoo.fields.Field.edit_ = edit  # type: ignore
except AttributeError:
    pass
