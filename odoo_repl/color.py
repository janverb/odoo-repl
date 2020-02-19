# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import textwrap

from datetime import datetime, date

import odoo_repl

from odoo_repl import shorthand
from odoo_repl import util
from odoo_repl.imports import odoo, t, TextLike, MYPY, Field

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
    # type: (Field) -> t.Text
    f_type = field_colors.get(field_obj.type, field_default)(field_obj.type)
    if field_obj.relational:
        return "{}: {}".format(f_type, record(field_obj.comodel_name))
    return f_type


def render_user(obj):
    # type: (odoo.models.ResUsers) -> t.Text
    return ", ".join(
        record(shorthand.UserBrowser._repr_for_value(user.login))
        if user.login and user.active
        else record("res.users[{}]".format(user.id))
        for user in obj
    )


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
            return render_user(obj)
        elif obj._name == "hr.employee":
            if MYPY:
                assert isinstance(obj, odoo.models.HrEmployee)
            return ", ".join(
                record(shorthand.EmployeeBrowser._repr_for_value(em.user_id.login))
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
    if len(obj._ids) == 1:
        affix = None
        xml_ids = util.xml_ids(obj)
        if xml_ids:
            affix = xml_ids[0].to_ref()
        else:
            try:
                name = obj.display_name
                default_name = "{},{}".format(obj._name, obj.id)
                if name and name != default_name:
                    affix = repr(name)
                    if affix.startswith("u"):
                        # Unicode string literal, distracting
                        affix = affix[1:]
            except Exception:
                pass
        if affix is not None:
            return record("{}[{}]".format(obj._name, obj.id)) + " ({})".format(affix)
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
    if syntax == "xml":
        src = " " * 80 + src
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


def format_date(date_obj):
    # type: (t.Union[datetime, t.Text]) -> t.Text
    if isinstance(date_obj, datetime):
        date_obj = date_obj.strftime(odoo.fields.DATETIME_FORMAT)
    return blue.bold(date_obj)
