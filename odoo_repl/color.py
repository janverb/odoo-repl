# -*- coding: utf-8 -*-
"""Functionality for special terminal output, as well as general formatting."""

from __future__ import unicode_literals

import textwrap

from datetime import datetime, date

from odoo_repl import config
from odoo_repl import shorthand
from odoo_repl import util
from odoo_repl.imports import odoo, t, TextLike, Field, BaseModel, cast


class Color:
    def __init__(self, prefix, affix="\x1b[30m"):
        # type: (t.Text, t.Text) -> None
        self.prefix = prefix
        self.affix = affix

    def __call__(self, text):
        # type: (t.Text) -> t.Text
        if not config.color:
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
subheader = bold

string = blue.bold
number = purple.bold
missing = red.bold
boolean = green.bold

menu_lead = blue.bold
menu = purple.bold

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


def render_user(obj, link=True):
    # type: (odoo.models.ResUsers, bool) -> t.Text
    def render_single(user):
        # type: (odoo.models.ResUsers) -> t.Text
        text = shorthand.UserBrowser._repr_for_value(user.login)
        text = (record if user.active else missing)(text)
        if link:
            text = linkify_record(text, user)
        return text

    return ", ".join(map(render_single, obj))


def render_employee(obj, link=True):
    # type: (odoo.models.HrEmployee, bool) -> t.Text
    def render_single(employee):
        # type: (odoo.models.HrEmployee) -> t.Text
        if employee.user_id and employee.user_id.employee_ids == employee:
            text = shorthand.EmployeeBrowser._repr_for_value(employee.user_id.login)
        else:
            text = basic_render_record(employee, link=link)
        text = (record if employee.active else missing)(text)
        if link:
            text = linkify_record(text, employee)
        return text

    return ", ".join(map(render_single, obj))


def make_affix(obj):
    # type: (BaseModel) -> t.Optional[t.Text]
    affix = None
    xml_ids = util.xml_ids(obj)
    if xml_ids:
        return xml_ids[0].to_ref()
    try:
        name = obj.display_name
        default_name = "{},{}".format(obj._name, obj.id)
        if name and name != default_name:
            affix = repr(name)
            if affix.startswith("u"):
                # Unicode string literal, distracting
                affix = affix[1:]
            return affix
    except Exception:
        pass
    return None


def basic_render_record(obj, link=True):
    # type: (BaseModel, bool) -> t.Text
    """Build a model[id] style record representation.

    This doesn't apply coloring but does apply linking if appropriate.
    """
    if len(obj._ids) == 1 and isinstance(obj.id, int) and link:
        return linkify_record("{}[{}]".format(obj._name, obj.id), obj)

    if len(obj._ids) > 200:
        return "{} × {}".format(obj._name, len(obj._ids))

    fragments = []  # type: t.List[t.Text]
    news = 0
    for ident in obj._ids:
        if isinstance(ident, int):
            if link and config.clickable_records:
                url = util.generate_url(model=obj._name, id=ident)
                fragments.append(linkify(str(ident), url))
            else:
                fragments.append(str(ident))
        else:
            news += 1
    if news:
        if news == 1:
            fragments.append("NewId")
        else:
            fragments.append("NewId × {}".format(news))
    return "{}[{}]".format(obj._name, ", ".join(fragments))


def render_record(obj, link=True):
    # type: (BaseModel, bool) -> t.Text
    # TODO: It might be nice to color inactive records with missing.
    # But what about multi-records?
    if not hasattr(obj, "_ids") or not obj._ids:
        return missing("{}[]".format(obj._name))
    if len(obj._ids) > 10:
        return record("{} × {}".format(obj._name, len(obj._ids)))
    try:
        if obj._name == "res.users":
            return render_user(cast("odoo.models.ResUsers", obj), link=link)
        elif obj._name == "hr.employee":
            return render_employee(cast("odoo.models.HrEmployee", obj), link=link)
    except Exception:
        pass
    text = record(basic_render_record(obj, link=link))
    if len(obj._ids) == 1:
        affix = make_affix(obj)
        if affix is not None:
            text += " ({})".format(affix)
    return text


def record_header(obj):
    # type: (BaseModel) -> t.Text
    rep = header(basic_render_record(obj)) + util.xml_id_tag(obj)
    if obj.env.uid != 1:
        rep += " (as {})".format(render_user(obj.env.user))
    return rep


def color_value(obj, field_type):
    # type: (object, t.Text) -> t.Text
    """Color a field value depending on its type and its field's type."""
    if obj is False and field_type != "boolean" or obj is None:
        return missing(repr(obj))
    elif isinstance(obj, bool):
        # False shows up as green if it's a Boolean, and red if it's a
        # default value, so red values always mean "missing"
        return boolean(repr(obj))
    elif isinstance(obj, BaseModel):
        return render_record(obj)
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
    if not config.color:
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
        return pyg_highlight(src, lexer, TerminalFormatter()).strip()  # type: ignore


def format_date(date_obj):
    # type: (t.Union[datetime, t.Text]) -> t.Text
    if isinstance(date_obj, datetime):
        date_obj = date_obj.strftime(odoo.fields.DATETIME_FORMAT)
    return blue.bold(date_obj)


def linkify(text, uri):
    # type: (t.Text, t.Text) -> t.Text
    """Add terminal escape codes to turn text into a clickable link.

    https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda

    This lives in color.py because that's where the other terminal stuff is.
    """
    return "\x1b]8;;{uri}\x1b\\{text}\x1b]8;;\x1b\\".format(text=text, uri=uri)


def linkify_url(text, **params):
    # type: (t.Text, object) -> t.Text
    if not config.clickable_records:
        return text
    return linkify(text, util.generate_url(**params))


def linkify_record(text, obj):
    # type: (t.Text, BaseModel) -> t.Text
    if not config.clickable_records or len(obj) != 1:
        return text
    return linkify(text, util.link_for_record(obj))
