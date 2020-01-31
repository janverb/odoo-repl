# TODO:
# - typecheck .search()
# - typecheck multi/single records?
# - generate model info from Odoo runtime
import typing as t

from mypy.nodes import StrExpr, UnicodeExpr
from mypy.plugin import MethodContext, Plugin
from mypy.types import Type, Instance


class OdooPlugin(Plugin):
    def get_method_hook(
        self, fullname: str
    ) -> t.Optional[t.Callable[[MethodContext], Type]]:
        if fullname.startswith("odoo.models."):
            if fullname.endswith(".mapped"):
                return mapped_hook
        if fullname.startswith("odoo.api.Environment"):
            if fullname.endswith(".__getitem__"):
                return envget_hook
        return None


def mapped_hook(ctx: MethodContext) -> Type:
    if (
        not isinstance(ctx.type, Instance)
        or len(ctx.args) != 1
        or len(ctx.args[0]) != 1
        or not isinstance(ctx.args[0][0], (StrExpr, UnicodeExpr))
    ):
        return ctx.default_return_type
    field = ctx.args[0][0].value
    if not field or "." in field:
        return ctx.default_return_type
    field_value = ctx.type.type.get(field)
    if not field_value or not field_value.type:
        ctx.api.fail(
            "Unknown field {!r} on model {!r}".format(field, ctx.type.type.fullname),
            ctx.context,
        )
        return ctx.default_return_type
    field_type = field_value.type
    if isinstance(field_type, Instance):
        if field_type.type.fullname.startswith("odoo.models."):
            return field_type
    return ctx.api.named_generic_type("builtins.list", [field_type])


def envget_hook(ctx: MethodContext) -> Type:
    if not len(ctx.args) == 1 and len(ctx.args[0]) == 1:
        return ctx.default_return_type
    arg = ctx.args[0][0]
    if not isinstance(arg, (StrExpr, UnicodeExpr)):
        return ctx.default_return_type
    model = arg.value
    clsname = "".join(part.capitalize() for part in model.split("."))
    try:
        return ctx.api.named_type("odoo.models." + clsname)  # type: ignore
    except KeyError:
        ctx.api.fail("Unknown model {!r}".format(model), ctx.context)
        return ctx.default_return_type


def plugin(_version: str) -> t.Type[OdooPlugin]:
    return OdooPlugin
