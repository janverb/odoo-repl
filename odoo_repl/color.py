# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import textwrap

from datetime import datetime, date

import odoo_repl

from odoo_repl.imports import odoo, t, TextLike, MYPY

enabled = True


class Color:
    def __init__(self, prefix, affix="\x1b[30m"):
        # type: (t.Text, t.Text) -> None
        self.prefix = prefix
        self.affix = affix

    def __call__(self, text):
        # type: (t.Text) -> t.Text
        if not enabled:
            return text
        return "\x1b[{}m{}{}\x1b[m".format(self.prefix, text, self.affix)

    def bold(self, text):
        # type: (t.Text) -> t.Text
        return self(bold(text))


plain = Color("", "")
bold = Color("1", "")
red = Color("31")
green = Color("32")
yellow = Color("33")
blue = Color("34")
purple = Color("35")
cyan = Color("36")

header = yellow.bold
field = green.bold
module = purple.bold
record = cyan.bold
model = cyan.bold
method = yellow.bold
display_name = bold
permission = purple.bold
decorator = blue.bold

string = blue.bold
number = purple.bold
missing = red.bold
boolean = green.bold

field_colors = {
    "char": blue.bold,
    "text": blue.bold,
    "binary": blue.bold,
    "selection": blue.bold,
    "datetime": blue.bold,
    "date": blue.bold,
    "integer": purple.bold,
    "float": purple.bold,
    "id": purple.bold,
    "boolean": green.bold,
}  # type: t.Dict[t.Text, t.Callable[[t.Text], t.Text]]
field_default = green.bold


def color_field(field_obj):
    # type: (odoo.fields.Field) -> t.Text
    f_type = field_colors.get(field_obj.type, field_default)(field_obj.type)
    if field_obj.relational:
        return "{}: {}".format(f_type, record(field_obj.comodel_name))
    return f_type


def _render_record(obj):
    # type: (odoo.models.BaseModel) -> t.Text
    if not hasattr(obj, "_ids") or not obj._ids:
        return missing("{}[]".format(obj._name))
    if len(obj._ids) > 10:
        return record("{} × {}".format(obj._name, len(obj._ids)))
    try:
        if obj._name == "res.users":
            if MYPY:
                assert isinstance(obj, odoo.models.ResUsers)
            return ", ".join(
                record(odoo_repl.UserBrowser._repr_for_value(user.login))
                if user.login and user.active
                else record("res.users[{}]".format(user.id))
                for user in obj
            )
        elif obj._name == "hr.employee":
            if MYPY:
                assert isinstance(obj, odoo.models.HrEmployee)
            return ", ".join(
                record(odoo_repl.EmployeeBrowser._repr_for_value(em.user_id.login))
                if (
                    em.active
                    and em.user_id
                    and em.user_id.active
                    and em.user_id.login
                    and em.user_id.employee_ids == em
                )
                else record("hr.employee[{}]".format(em.id))
                for em in obj
            )
    except Exception:
        pass
    return record("{}[{}]".format(obj._name, odoo_repl._ids_repr(obj._ids)))


def color_value(obj, field_type):
    # type: (object, t.Text) -> t.Text
    """Color a field value depending on its type and its field's type."""
    if obj is False and field_type != "boolean" or obj is None:
        return missing(repr(obj))
    elif isinstance(obj, bool):
        # False shows up as green if it's a Boolean, and red if it's a
        # default value, so red values always mean "missing"
        return boolean(repr(obj))
    elif isinstance(obj, odoo.models.BaseModel):
        return _render_record(obj)
    elif isinstance(obj, TextLike):
        rep = repr(obj)  # type: t.Text
        if len(rep) > 120:
            rep = rep[:120] + "..."
        return string(rep)
    elif isinstance(obj, (datetime, date)):
        # For consistency with versions where they're strings
        return string(str(obj))
    elif isinstance(obj, (int, float)):
        return number(repr(obj))
    else:
        return repr(obj)


def highlight(src, syntax="python"):
    # type: (t.Text, t.Text) -> t.Text
    """Apply syntax highlighting. Only available if pygments is installed."""
    src = textwrap.dedent(src).strip()
    if not enabled:
        return src
    try:
        from pygments import highlight as pyg_highlight
        from pygments.lexers import PythonLexer, XmlLexer, RstLexer
        from pygments.formatters.terminal import TerminalFormatter
    except ImportError:
        return src
    else:
        if syntax == "python":
            lexer = PythonLexer()
        elif syntax == "xml":
            lexer = XmlLexer()
        elif syntax == "rst":
            lexer = RstLexer()
        else:
            raise ValueError("Unknown syntax {!r}".format(syntax))
        return pyg_highlight(src, lexer, TerminalFormatter())  # type: ignore
