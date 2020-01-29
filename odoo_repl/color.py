from __future__ import unicode_literals

import textwrap

from odoo_repl.imports import odoo, t

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


def highlight(src, syntax="python"):
    # type: (t.Text, t.Text) -> t.Text
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
        return pyg_highlight(src, lexer, TerminalFormatter())
