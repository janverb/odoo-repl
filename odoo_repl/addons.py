# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import subprocess

import odoo_repl

from odoo_repl import color
from odoo_repl import grep
from odoo_repl import util
from odoo_repl.imports import odoo, t, PY3


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
    def models(self):
        # type: () -> t.List[odoo_repl.ModelProxy]
        # TODO: return AddonModelBrowser with PartialModels that show the
        # fields (and methods?) added in the addon
        return [
            odoo_repl.ModelProxy(self._env, name, nocomplete=True)
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

    @property
    def ref(self):
        # type: () -> odoo_repl.DataModuleBrowser
        return odoo_repl.DataModuleBrowser(self._env, self._module)

    def grep_(self, *args, **kwargs):
        # type: (object, object) -> None
        """grep through the addon's directory.

        See help(odoo_repl.grep) for more information.
        """
        argv = grep.build_grep_argv(args, kwargs, recursive=True)
        argv.append(self.path)
        subprocess.Popen(argv).wait()

    def _get_depends(self):
        # type: () -> odoo.models.IrModuleModule
        return (
            self._env["ir.module.module.dependency"]
            .search([("module_id", "=", self.record.id)])
            .mapped("depend_id")
        )

    def _get_rdepends(self):
        # type: () -> t.Tuple[odoo.models.IrModuleModule, odoo.models.IrModuleModule]
        dependency = self._env["ir.module.module.dependency"]
        direct = dependency.search([("name", "=", self._module)]).mapped("module_id")
        indirect = self._env["ir.module.module"]
        latest = direct
        while latest:
            new = dependency.search([("name", "in", latest.mapped("name"))]).mapped(
                "module_id"
            )
            new -= direct
            new -= indirect
            indirect |= latest
            latest = new
        indirect -= direct
        return direct, indirect

    def __repr__(self):
        # type: () -> str
        return str("{}({!r})".format(self.__class__.__name__, self._module))

    def __str__(self):
        # type: () -> str
        # TODO: integrate with displayhooks (odoo_repr?)
        defined_models = (
            self._env["ir.model"]
            .browse(
                self._env["ir.model.data"]
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
        elif not state:
            state = color.yellow.bold("???")
        elif state in ("uninstallable", "uninstalled"):
            state = color.red.bold(state.capitalize())
        else:
            state = color.yellow.bold(state.capitalize())

        description = self.manifest.description  # type: str
        if not PY3:
            try:
                description = description.decode("utf8").encode(
                    "ascii", errors="replace"
                )
            except UnicodeDecodeError:
                pass

        direct, indirect = self._get_rdepends()

        parts = []
        parts.append(
            "{} {} by {}".format(
                color.module(self._module), self.manifest.version, self.manifest.author,
            )
        )
        parts.append(self.path)
        parts.append(state)
        parts.append(color.display_name(self.manifest.name))
        parts.append(self.manifest.summary)
        if self.manifest.depends:
            parts.append(
                "Depends: {}".format(
                    ", ".join(map(color.module, self.manifest.depends))
                )
            )
        if direct:
            parts.append("Dependents: {}".format(", ".join(map(_color_state, direct))))
        if indirect:
            parts.append(
                "Indirect dependents: {}".format(", ".join(map(_color_state, indirect)))
            )
        if defined_models:
            parts.append(
                "Defines: {}".format(", ".join(map(color.model, defined_models)))
            )
        if description:
            parts.append("")
            # rst2ansi might be better here
            # (https://pypi.org/project/rst2ansi/)
            parts.append(color.highlight(description, "rst"))

        return str("\n".join(parts))

    def _repr_pretty_(self, printer, _cycle):
        # type: (t.Any, t.Any) -> None
        if printer.indentation == 0:
            printer.text(str(self))
        else:
            printer.text(repr(self))


def _color_state(module):
    # type: (odoo.models.IrModuleModule) -> t.Text
    if module.state == "installed":
        return color.module(module.name)
    elif module.state == "uninstalled":
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
