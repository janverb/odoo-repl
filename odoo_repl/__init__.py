# -*- coding: utf-8 -*-
# TODO:
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
import functools
import importlib
import logging
import os
import random
import subprocess
import sys
import threading
import types

from odoo_repl import access  # noqa: F401
from odoo_repl import addons
from odoo_repl import color
from odoo_repl import config
from odoo_repl import fields
from odoo_repl import fzf
from odoo_repl import gitsources
from odoo_repl import grep
from odoo_repl import methods
from odoo_repl import models
from odoo_repl import shorthand
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import PY3, odoo, BaseModel, t, Text, builtins, StringIO

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
    if not hasattr(odoo, "tools"):
        raise RuntimeError(
            "Odoo is not imported. You should run this from an Odoo shell."
        )
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
    namespace.update(
        {part: models.ModelProxy(env, part) for part in envproxy._base_parts()}
    )

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
):
    # type: (...) -> None
    """Enable all the bells and whistles.

    :param db: Either an Odoo environment object, an Odoo cursor, a database
               name, or ``None`` to guess the database to use.
    :param module: Either a module, the name of a module, or ``None`` to
                   install into the module of the caller.
    """
    if module is None:
        frame = sys._getframe().f_back
        assert frame is not None
        target_ns = frame.f_globals
    elif isinstance(module, Text):
        target_ns = vars(importlib.import_module(module))
    else:
        target_ns = vars(module)

    env_, to_install = create_namespace(db)

    atexit.register(env_.cr.close)

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


def _color_repr(owner, field_name):
    # type: (BaseModel, t.Text) -> t.Text
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
    if isinstance(obj, models.ModelProxy):
        return models.model_repr(obj)
    elif isinstance(obj, methods.MethodProxy):
        return methods.method_repr(obj)
    elif isinstance(obj, fields.FieldProxy):
        return fields.field_repr(obj._real, env=obj._env)
    elif isinstance(obj, BaseModel):
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


def _record_header(obj):
    # type: (BaseModel) -> t.Text
    header = color.header(color.basic_render_record(obj)) + util.xml_id_tag(obj)
    if obj.env.uid != 1:
        header += " (as {})".format(color.render_user(obj.env.user))
    return header


def record_repr(obj):
    # type: (BaseModel) -> t.Text
    """Display all of a record's fields."""
    obj = util.unwrap(obj)

    if not hasattr(obj, "_ids"):
        return repr(obj)
    elif not obj:
        return u"{}[]".format(obj._name)
    elif len(obj) > 1:
        return color.basic_render_record(obj)

    if obj.env.cr.closed:
        return color.basic_render_record(obj) + " (closed cursor)"

    field_names = sorted(
        field
        for field in obj._fields
        if field not in models.FIELD_BLACKLIST
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
    # So we do our best to disable it.

    # For Odoo 8, we do everything in a separate env where the ID cache is
    # empty. We make a separate env by changing the context. This has the added
    # advantage of informing models that they're running in odoo_repl, in case
    # they care. In _color_repr we clear the cache in case it got filled.

    # For Odoo 10-13, we slice the record. Odoo tries to be smart and narrows
    # the prefetch cache if we slice while keeping it when iterating.

    # I don't know what Odoo 9 does but I hope it's one of the above.

    # TODO: When .print_()ing a recordset we do want prefetching.

    no_prefetch_obj = obj.with_context(odoo_repl=True)[:]

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
    # type: (BaseModel) -> t.List[str]
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


def edit(thing, index=0, bg=None):
    # type: (sources.Sourceable, t.Union[int, t.Text], t.Optional[bool]) -> None
    """Open a model or field definition in an editor."""
    # TODO: editor kwarg and/or argparse flag
    if bg is None:
        bg = config.bg_editor
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
    argv = list(config.editor)
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
    # type: (BaseModel, t.Any, t.Any) -> None
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
            return models.ModelProxy(self._env, attr)
        raise AttributeError

    def __dir__(self):
        # type: () -> t.List[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = {"_base_parts", "fzf_"}  # type: t.Set[t.Text]
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
        # type: (t.Text) -> models.ModelProxy
        if ind not in self._env.registry:
            raise KeyError("Model '{}' does not exist".format(ind))
        return models.ModelProxy(self._env, ind, nocomplete=True)

    def __iter__(self):
        # type: () -> t.Iterator[models.ModelProxy]
        for mod in self._env.registry:
            yield self[mod]

    def _ipython_key_completions_(self):
        # type: () -> t.List[t.Text]
        return list(self._env.registry)

    def fzf_(self):
        # type: () -> t.Optional[models.ModelProxy]
        result = fzf.fzf(sorted(self._env.registry))
        if result:
            return self[result[0]]
        return None


def _BaseModel_create_(
    self,  # type: BaseModel
    vals=None,  # type: t.Optional[t.Dict[str, t.Any]]
    **field_vals  # type: t.Any
):
    # type: (...) -> BaseModel
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
    clauses.extend((k, "=", v) for k, v in field_vals.items())

    def to_id(thing):
        # type: (object) -> t.Any
        if isinstance(thing, tuple):
            return tuple(map(to_id, thing))
        elif isinstance(thing, list):
            return list(map(to_id, thing))
        elif isinstance(thing, BaseModel):
            if len(thing) == 1:
                return thing.id
            return thing.ids
        return thing

    clauses = to_id(clauses)

    return clauses


def _BaseModel_search_(
    self,  # type: t.Union[BaseModel, models.ModelProxy]
    *args,  # type: object
    **field_vals  # type: t.Any
):
    # type: (...) -> BaseModel
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


def _is_record(obj):
    # type: (object) -> bool
    """Return whether an object is an Odoo record."""
    return isinstance(obj, BaseModel) and hasattr(obj, "_ids")


def _BaseModel_source_(record, location=None, context=False):
    # type: (BaseModel, t.Optional[t.Text], bool) -> None
    import lxml.etree

    for rec in record:
        for rec_id in util.xml_ids(rec):
            for definition in sources.xml_records[rec_id]:
                if location is not None and definition.module != location:
                    continue
                elem = definition.elem.getroottree() if context else definition.elem
                print(sources.format_source(definition.to_source()))
                src = lxml.etree.tostring(elem, encoding="unicode")
                print(color.highlight(src, "xml"), end="\n\n")


try:
    BaseModel._repr_pretty_ = _BaseModel_repr_pretty_  # type: ignore
    BaseModel.edit_ = edit  # type: ignore
    BaseModel.print_ = odoo_print  # type: ignore
    BaseModel.search_ = _BaseModel_search_  # type: ignore
    BaseModel.create_ = _BaseModel_create_  # type: ignore
    BaseModel.filtered_ = _BaseModel_filtered_  # type: ignore
    BaseModel.source_ = _BaseModel_source_  # type: ignore
    BaseModel.gitsource_ = gitsources.gitsource_  # type: ignore
    BaseModel.fzf_ = fzf.fzf_field  # type: ignore
    BaseModel.xfzf_ = fzf.fzf_xml_id  # type: ignore
    odoo.fields.Field.edit_ = edit  # type: ignore
except AttributeError:
    pass


def set_trace():
    # type: () -> None
    from odoo_repl.opdb import get_debugger_cls

    get_debugger_cls()().set_trace(sys._getframe().f_back)


def post_mortem(traceback=None):
    # type: (t.Optional[types.TracebackType]) -> None
    from odoo_repl.opdb import get_debugger_cls

    if traceback is None:
        traceback = sys.exc_info()[2]
        if traceback is None:
            raise ValueError(
                "A valid traceback must be passed if no exception is being handled"
            )
    debugger = get_debugger_cls()()
    debugger.reset()
    debugger.interaction(None, traceback)


def pm():
    # type: () -> None
    post_mortem(sys.last_traceback)
