"""Functions for finding and displaying access rules."""

# TODO:
# - display overrides of _check_access_rule, etc.

from __future__ import unicode_literals

import pprint

from odoo_repl import color
from odoo_repl import sources
from odoo_repl import util
from odoo_repl.imports import odoo, t


def access_for_model(
    env,  # type: odoo.api.Environment
    model_record,  # type: odoo.models.IrModel
    user=None,  # type: t.Optional[odoo.models.ResUsers]
):
    # type: (...) -> odoo.models.IrModelAccess
    return (
        env["ir.model.access"]
        .search([("model_id", "=", model_record.id)])
        .filtered(
            lambda acc: not (
                user is not None
                and acc.group_id
                and not user.has_group(*acc.group_id.get_xml_id().values())
            )
        )
    )


def rules_for_model(
    env,  # type: odoo.api.Environment
    model_record,  # type: odoo.models.IrModel
    user=None,  # type: t.Optional[odoo.models.ResUsers]
):
    # type: (...) -> odoo.models.IrRule
    return (
        env["ir.rule"]
        .search([("model_id", "=", model_record.id)])
        .filtered(
            lambda rule: not (
                user is not None
                and rule.groups
                and not any(
                    user.has_group(*group.get_xml_id().values())
                    for group in rule.groups
                )
            )
        )
    )


def rule_repr(rule):
    # type: (odoo.models.IrRule) -> t.Text
    parts = []
    parts.append(color.record_header(rule))
    parts.append(color.display_name(rule.display_name))
    groups = ", ".join(
        color.record(group.name) + util.xml_id_tag(group) for group in rule.groups
    )
    if not groups:
        parts.append(
            color.green.bold("Everyone") if rule["global"] else color.red.bold("No-one")
        )
    else:
        parts.append(groups)
    parts.append(_crud_format(rule))
    if rule.domain_force not in {None, False, "[]", "[(1, '=', 1)]", '[(1, "=", 1)]'}:
        assert rule.domain_force
        parts.append(color.highlight(_domain_format(rule.env, rule.domain_force)))
    parts.extend(sources.format_sources(sources.find_source(rule)))
    return "\n".join(parts)


def access_repr(access):
    # type: (odoo.models.IrModelAccess) -> t.Text
    parts = []
    parts.append(color.record_header(access))
    parts.append(color.display_name(access.display_name))
    parts.append(
        color.record(access.group_id.name) + util.xml_id_tag(access.group_id)
        if access.group_id
        else color.green.bold("Everyone")
    )
    parts.append(_crud_format(access))
    return "\n".join(parts)


def _domain_format(env, domain):
    # type: (odoo.api.Environment, t.Text) -> t.Text
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
        # type: (t.Text) -> None
        self._path = path

    def __repr__(self):
        # type: () -> str
        return str(self._path)

    def __getattr__(self, attr):
        # type: (str) -> _Expressionizer
        return self.__class__("{}.{}".format(self._path, attr))

    def __getitem__(self, ind):
        # type: (object) -> _Expressionizer
        return self.__class__("{}[{!r}]".format(self._path, ind))

    def __iter__(self):
        # type: () -> t.NoReturn
        raise TypeError

    def __call__(self, *args, **kwargs):
        # type: (object, object) -> _Expressionizer
        argfmt = [repr(arg) for arg in args]  # type: t.List[t.Text]
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
