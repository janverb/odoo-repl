# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals

import subprocess

import odoo_repl

from odoo_repl import color
from odoo_repl import gitsources
from odoo_repl import grep
from odoo_repl import methods
from odoo_repl import models
from odoo_repl import records
from odoo_repl import shorthand
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import odoo, t, PY3, Text


class AddonBrowser(object):
    def __init__(self, env):
        # type: (odoo.api.Environment) -> None
        self._env = env

    def __getattr__(self, attr):
        # type: (t.Text) -> Addon
        if not util.sql(
            self._env, "SELECT name FROM ir_module_module WHERE name = %s", attr
        ):
            raise AttributeError("No module '{}'".format(attr))
        addon = Addon(self._env, attr)
        setattr(self, attr, addon)
        return addon

    def __dir__(self):
        # type: () -> t.List[t.Text]
        return util.sql(self._env, "SELECT name FROM ir_module_module")

    def __iter__(self):
        # type: () -> t.Iterator[Addon]
        for name in util.sql(self._env, "SELECT name FROM ir_module_module"):
            yield Addon(self._env, name)


class Addon(object):
    def __init__(self, env, module):
        # type: (odoo.api.Environment, t.Text) -> None
        self._env = env
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

        def unicodify(thing):
            # type: (object) -> t.Any
            if isinstance(thing, dict):
                return {key: unicodify(value) for key, value in thing.items()}
            elif isinstance(thing, list):
                return [unicodify(item) for item in thing]
            elif isinstance(thing, bytes):
                return thing.decode("utf8")
            else:
                return thing

        if not PY3:
            manifest = unicodify(manifest)

        return _AttributableDict(manifest)

    @property
    def record(self):
        # type: () -> odoo.models.IrModuleModule
        if self._record is None:
            self._record = self._env["ir.module.module"].search(
                [("name", "=", self._module)]
            )
        return self._record

    @property
    def state(self):
        # type: () -> t.Text
        return self.record.state or "???"

    @property
    def models(self):
        # type: () -> t.List[odoo_repl.models.ModelProxy]
        # XXX
        # TODO: return AddonModelBrowser with PartialModels that show the
        # fields (and methods?) added in the addon
        return [
            odoo_repl.models.ModelProxy(self._env, name, nocomplete=True)
            for name in (
                self._env["ir.model"]
                .browse(
                    self._env["ir.model.data"]
                    .search([("model", "=", "ir.model"), ("module", "=", self._module)])
                    .mapped("res_id")
                )
                .mapped("model")
            )
        ]

    @property
    def path(self):
        # type: () -> t.Text
        mod_path = odoo.modules.module.get_module_path(
            self._module, display_warning=False
        )
        if not mod_path:
            raise RuntimeError("Can't find path of module {!r}".format(self._module))
        return mod_path

    def gitsource_(self):
        # type: () -> None
        """Print a link to the git host that matches the local version."""
        print(gitsources.format_source(sources.Source(self._module, self.path, None)))

    @property
    def ref(self):
        # type: () -> shorthand.DataModuleBrowser
        return shorthand.DataModuleBrowser(self._env, self._module)

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through the addon's directory.

        See help(odoo_repl.grep) for more information.
        """
        argv = grep.build_grep_argv(args, kwargs, recursive=True)
        argv.append(self.path)
        subprocess.Popen(argv).wait()

    def open_(self):
        # type: () -> None
        records.open_(self.record)

    def edit_(self):
        # type: () -> None
        odoo_repl._edit(self.path)

    def _get_depends(self):
        # type: () -> t.Tuple[odoo.models.IrModuleModule, odoo.models.IrModuleModule]
        direct = (
            self._env["ir.module.module.dependency"]
            .search([("module_id", "=", self.record.id)])
            .mapped("depend_id")
        )
        indirect = self._env["ir.module.module"]
        latest = direct
        while latest:
            new = (
                self._env["ir.module.module.dependency"]
                .search([("module_id", "in", latest.ids)])
                .mapped("depend_id")
            )
            new -= direct
            new -= indirect
            indirect |= latest
            latest = new
        indirect -= direct
        return direct, indirect

    def _get_rdepends(self):
        # type: () -> t.Tuple[odoo.models.IrModuleModule, odoo.models.IrModuleModule]
        direct = (
            self._env["ir.module.module.dependency"]
            .search([("name", "=", self._module)])
            .mapped("module_id")
        )
        indirect = self._env["ir.module.module"]
        latest = direct
        while latest:
            new = (
                self._env["ir.module.module.dependency"]
                .search([("name", "in", latest.mapped("name"))])
                .mapped("module_id")
            )
            new -= direct
            new -= indirect
            indirect |= latest
            latest = new
        indirect -= direct
        return direct, indirect

    def depends(self, other):
        # type: (t.Text) -> bool
        return other in self._get_depends()[1].mapped("name")

    def __repr__(self):
        # type: () -> str
        return str("{}({!r})".format(self.__class__.__name__, self._module))

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0:
            printer.text(addon_repr(self))
        else:
            printer.text(repr(self))

    def definitions_(self):
        # type: () -> None
        """High-level summary of the module's contents."""
        # There's duplication here with models.model_repr and
        # ModelProxy.methods_() and so on

        if self.state in {"uninstalled", "uninstallable"}:
            print(color.missing("Module not installed"))
            return

        model_names = sorted(
            set(
                self._env["ir.model"]
                .browse(
                    self._env["ir.model.data"]
                    .search([("model", "=", "ir.model"), ("module", "=", self._module)])
                    .mapped("res_id")
                )
                .mapped("model")
            )
        )
        data = sorted(
            self._env["ir.model.data"].search(
                [
                    ("module", "=", self._module),
                    ("model", "not in", ("ir.model", "ir.model.fields")),
                ]
            ),
            key=lambda rec: (rec.model, rec.name),
        )

        for model_name in model_names:
            try:
                model = self._env[model_name]
            except KeyError:
                continue

            classes = [
                cls
                for cls in type(model).__mro__
                if getattr(cls, "_module", None) == self._module
            ]
            if not classes:
                continue

            print(color.model(model_name))

            fields = sorted(
                (
                    field
                    for field in model._fields.values()
                    if field.name not in models.FIELD_BLACKLIST
                    if self._module in sources.find_field_modules(field)
                ),
                key=lambda field: field.name,
            )
            if fields:
                max_len = max(len(field.name) for field in fields)
                for field in fields:
                    print(models.format_single_field(field, max_len=max_len))

            for cls in classes:
                meths = [
                    (name, attr)
                    for name, attr in sorted(vars(cls).items())
                    if util.loosely_callable(attr) and name != "pool"
                ]
                for name, meth in meths:
                    print(
                        color.method(name)
                        + methods._func_signature(util.unpack_function(meth))
                    )

            print()

        if data:
            print(color.subheader("Records:"))
        for rec in data:
            print(
                color.record("{}[{}]".format(rec.model, rec.res_id)),
                "({})".format(rec.name),
            )


def addon_repr(addon):
    # type: (Addon) -> t.Text
    # TODO: A lot of the most interesting information is at the top so you have
    # to scroll up
    # Ideas:
    # - Put it at the bottom instead
    # - Don't show the README by default

    try:
        addon.manifest
    except RuntimeError:
        return repr(addon)

    defined_models = (
        addon._env["ir.model"]
        .browse(
            addon._env["ir.model.data"]
            .search([("model", "=", "ir.model"), ("module", "=", addon._module)])
            .mapped("res_id")
        )
        .mapped("model")
    )

    state = addon.record.state
    if (
        state == "installed"
        and addon.record.installed_version != addon.manifest.version
    ):
        state += " (out of date)"

    if state == "installed":
        state = color.green.bold(state.capitalize())
    elif not state:
        state = color.yellow.bold("???")
    elif state in ("uninstallable", "uninstalled"):
        state = color.red.bold(state.capitalize())
    else:
        state = color.yellow.bold(state.capitalize())

    description = addon.manifest.description
    if isinstance(addon.manifest.author, Text):
        author = addon.manifest.author
    else:
        author = ", ".join(addon.manifest.author)

    parts = []
    parts.append(
        "{} {} by {}".format(
            color.module(addon._module), addon.manifest.version, author
        )
    )
    parts.append(util.link_for_record(addon.record))
    parts.append(addon.path)
    parts.append(state)
    parts.append(color.display_name(addon.manifest.name))
    parts.append(addon.manifest.summary)

    def format_depends(pretext, modules):
        # type: (t.Text, odoo.models.IrModuleModule) -> None
        if modules:
            names = map(_color_state, sorted(modules, key=lambda mod: mod.name))
            parts.append("{}: {}".format(pretext, ", ".join(names)))

    # TODO: Indirect dependencies are a bit noisy, when/how do we show them?
    direct, _indirect = addon._get_depends()
    format_depends("Depends", direct)
    # format_depends("Indirectly depends", indirect)
    r_direct, _r_indirect = addon._get_rdepends()
    format_depends("Dependents", r_direct)
    # format_depends("Indirect dependents", r_indirect)

    if defined_models:
        parts.append("Defines: {}".format(", ".join(map(color.model, defined_models))))
    if description:
        parts.append("")
        # rst2ansi might be better here
        # (https://pypi.org/project/rst2ansi/)
        parts.append(color.highlight(description, "rst"))

    return "\n".join(parts)


def _color_state(module):
    # type: (odoo.models.IrModuleModule) -> t.Text
    if module.state == "installed":
        return color.module(module.name)
    elif module.state in ("uninstalled", "uninstallable"):
        return color.red.bold(module.name)
    else:
        return color.yellow.bold(module.name)


class _AttributableDict(dict):  # type: ignore
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
        # type: () -> t.List[t.Text]
        if PY3:
            listing = set(super().__dir__())
        else:
            listing = set()
        listing.update(self.keys())
        return sorted(listing)
