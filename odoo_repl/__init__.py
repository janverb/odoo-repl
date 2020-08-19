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
import subprocess
import sys
import threading
import types

from odoo_repl import addons
from odoo_repl import config
from odoo_repl import fields
from odoo_repl import fzf
from odoo_repl import gitsources  # noqa: F401
from odoo_repl import grep
from odoo_repl import methods
from odoo_repl import models
from odoo_repl import records
from odoo_repl import shorthand
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import PY3, odoo, BaseModel, t, Text, builtins, StringIO, Field


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
            # The `base` module is typically included inside the odoo module
            # and we don't want to search it twice
            # A more principled way to filter it out would be to check all
            # addons for being a subdirectory of `odoo`
            if mod != "base"
        ]
        paths.append(os.path.dirname(odoo.__file__))
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

    def open_():
        # type: () -> None
        subprocess.Popen(["xdg-open", util.generate_url()])

    namespace = {
        "self": env.user,
        "odoo": odoo,
        "openerp": odoo,
        "sql": functools.partial(util.sql, env),
        "grep_": grep_,
        "open_": open_,
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


def odoo_repr(obj):
    # type: (object) -> t.Text
    if isinstance(obj, models.ModelProxy):
        return models.model_repr(obj)
    elif isinstance(obj, methods.MethodProxy):
        return methods.method_repr(obj)
    elif isinstance(obj, fields.FieldProxy):
        return fields.field_repr(obj._real, env=obj._env)
    elif isinstance(obj, BaseModel):
        return records.record_repr(obj)
    elif isinstance(obj, addons.Addon):
        return addons.addon_repr(obj)
    else:
        return repr(obj)


@util.patch(BaseModel, "print_")
def odoo_print(obj, **kwargs):
    # type: (t.Any, t.Any) -> None
    if util.is_record(obj) and len(obj) > 1:
        first = True
        for record in obj:
            if not first:
                print()
            first = False
            print(records.record_repr(record), **kwargs)
    else:
        print(odoo_repr(obj), **kwargs)


def _edit(fname, lnum=None, bg=None):
    # type: (object, t.Optional[int], t.Optional[bool]) -> None
    if bg is None:
        bg = config.bg_editor
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


@util.patch(Field, "edit_")
@util.patch(BaseModel, "edit_")
def edit(thing, index=0, bg=None):
    # type: (sources.Sourceable, t.Union[int, t.Text], t.Optional[bool]) -> None
    """Open a model or field definition in an editor."""
    # TODO: editor kwarg and/or argparse flag
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
    return _edit(fname, lnum=lnum, bg=bg)


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
        result = fzf.fzf_single(
            "{} ({})".format(model._name, model._description)
            if model._description and model._description != model._name
            else model._name
            for model in self._env.registry.values()
        )
        if result:
            return self[result.split()[0]]
        return None


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
