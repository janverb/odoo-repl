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
# - put shuf_() on BaseModel
# - toggle to start pdb on log message (error/warning/specific message)
# - grep_ on XML records, for completeness
# - test on Odoo 9 and 11
# - write more tests
# - better buildout integration
# - look for more places where .sudo() should be used (see util.xml_ids())

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
import logging
import os
import random
import subprocess
import sys
import threading
import types

from odoo_repl import access
from odoo_repl import addons
from odoo_repl import color
from odoo_repl import fields
from odoo_repl import forensics
from odoo_repl import grep
from odoo_repl import methods
from odoo_repl import opdb
from odoo_repl import shorthand
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
        mods = util.sql(
            env, "SELECT name FROM ir_module_module WHERE state = 'installed'",
        )
        paths = [
            odoo.modules.module.get_module_path(mod, display_warning=False)
            for mod in mods
        ]
        argv.extend(filter(None, paths))
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
        "u": shorthand.UserBrowser(env),
        "emp": shorthand.EmployeeBrowser(env),
        "ref": shorthand.DataBrowser(env),
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
        return color.missing(type(err).__name__)
    field_type = owner._fields[field_name].type
    return color.color_value(obj, field_type)


def odoo_repr(obj):
    # type: (object) -> t.Text
    if isinstance(obj, ModelProxy):
        return model_repr(obj)
    elif isinstance(obj, methods.MethodProxy):
        return methods.method_repr(obj)
    elif isinstance(obj, fields.FieldProxy):
        return fields.field_repr(obj._real, env=obj._env)
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


def _has_computer(field):
    # type: (Field) -> bool
    return (
        field.compute is not None
        or type(getattr(field, "column", None)).__name__ == "function"
    )


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

    field_names = []
    delegated = []
    for field in sorted(obj._fields):
        if field in FIELD_BLACKLIST:
            continue
        if getattr(obj._fields[field], "related", False):
            delegated.append(obj._fields[field])
            continue
        field_names.append(field)
    max_len = max(len(f) for f in field_names) if field_names else 0
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
    for field in field_names:
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
        header += " (as {})".format(color.render_user(obj.env.user))
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

    field_names = sorted(
        field
        for field in obj._fields
        if field not in FIELD_BLACKLIST
        and field not in FIELD_VALUE_BLACKLIST
        and not obj._fields[field].related
    )
    max_len = max(len(f) for f in field_names) if field_names else 0
    parts = []

    parts.append(_record_header(obj))
    name = obj.sudo().display_name
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
    for field in field_names:
        parts.append(
            "{}: ".format(color.field(field))
            + (max_len - len(field)) * " "
            + _color_repr(no_prefetch_obj, field)
        )

    history_lines = _get_create_write_history(obj.sudo())
    if history_lines:
        parts.append("")
        parts.extend(history_lines)

    src = sources.find_source(obj)
    if src:
        parts.append("")
        parts.extend(sources.format_sources(src))

    return "\n".join(parts)


def _get_create_write_history(obj):
    # type: (odoo.models.BaseModel) -> t.List[str]
    if "create_date" not in obj._fields:
        return []
    history_lines = []
    obj = obj.sudo()
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
    return history_lines


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
        # pylint: disable=subprocess-popen-preexec-fn
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
        self.ref = shorthand.DataBrowser(env)

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

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        return list(self._env.registry)


def _BaseModel_create_(
    self,  # type: odoo.models.BaseModel
    vals=None,  # type: t.Optional[t.Dict[str, t.Any]]
    **field_vals  # type: t.Any
):
    # type: (...) -> odoo.models.BaseModel
    """Create a new record, optionally with keyword arguments.

    .create_(x='test', y=<some record>) is typically equivalent to
    .create({"x": "test", "y": <some record>id}). 2many fields are also
    handled.

    If you make a typo in a field name you get a proper error.
    """
    if vals:
        field_vals.update(vals)
    for key, value in field_vals.items():
        if key not in self._fields:
            raise TypeError("Field '{}' does not exist".format(key))
        if _is_record(value) or (
            isinstance(value, (list, tuple)) and value and _is_record(value[0])
        ):
            # TODO: typecheck model
            field_type = self._fields[key].type
            if field_type.endswith("2many"):
                field_vals[key] = [(6, 0, value.ids)]
            elif field_type.endswith("2one"):
                if len(value) > 1:
                    raise TypeError("Can't link multiple records for '{}'".format(key))
                field_vals[key] = value.id
    return self.create(field_vals)


def _parse_search_query(
    args,  # type: t.Tuple[object, ...]
    field_vals,  # type: t.Mapping[str, object]
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
    clauses.extend((k, "=", getattr(v, "id", v)) for k, v in field_vals.items())

    return clauses


def _BaseModel_search_(
    self,  # type: t.Union[odoo.models.BaseModel, ModelProxy]
    *args,  # type: object
    **field_vals  # type: t.Any
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
    offset = field_vals.pop("offset", 0)  # type: int
    limit = field_vals.pop("limit", None)  # type: t.Optional[int]
    order = field_vals.pop("order", "id")  # type: t.Optional[t.Text]
    count = field_vals.pop("count", False)  # type: bool
    shuf = field_vals.pop("shuf", None)  # type: t.Optional[int]
    if shuf and not (args or field_vals or offset or limit or count):
        # Doing a search seeds the cache with IDs, which tanks performance
        # Odoo will compute fields on many records at once even though you
        # won't use them
        query = "SELECT id FROM {}".format(self._table)
        if "active" in self._fields:
            query += " WHERE active = true"
        all_ids = util.sql(self.env, query)
        shuf = min(shuf, len(all_ids))
        return self.browse(random.sample(all_ids, shuf))
    clauses = _parse_search_query(args, field_vals)
    result = self.search(clauses, offset=offset, limit=limit, order=order, count=count)
    if shuf:
        shuf = min(shuf, len(result))
        return result.browse(random.sample(result._ids, shuf))
    return result


def _BaseModel_filtered_(
    self,  # type: odoo.models.AnyModel
    func=None,  # type: t.Optional[t.Callable[[odoo.models.AnyModel], bool]]
    **field_vals  # type: object
):
    # type: (...) -> odoo.models.AnyModel
    """Filter based on field values in addition to the usual .filtered() features.

    .filtered_(state='done') is equivalent to
    .filtered(lambda x: x.state == 'done').
    """
    this = self
    if func:
        this = this.filtered(func)
    if field_vals:
        this = this.filtered(
            lambda record: all(
                getattr(record, field) == value for field, value in field_vals.items()
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
            return fields.FieldProxy(self._env, self._real._fields[attr])
        thing = getattr(self._real, attr)  # type: object
        if callable(thing) and hasattr(type(self._real), attr):
            thing = methods.MethodProxy(thing, self._real, attr)
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
        # type: () -> t.Iterator[fields.FieldProxy]
        assert self._real is not None
        for field in sorted(self._real._fields.values(), key=lambda f: f.name):
            yield fields.FieldProxy(self._env, field)

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
                return fields.FieldProxy(self._env, self._real._fields[ind])
            thing = getattr(self._real, ind)
            if callable(thing):
                return methods.MethodProxy(thing, self._real, ind)
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
        cr = self._env.cr._obj
        with util.savepoint(cr):
            cr.execute("SELECT * FROM {} LIMIT 0;".format(self._real._table))
            columns = cr.description
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
                        color.method(name)
                        + methods._func_signature(util.unpack_function(meth))
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
