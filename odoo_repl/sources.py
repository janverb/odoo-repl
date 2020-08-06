"""Functions for finding source code."""

import collections
import inspect
import linecache
import os
import platform
import re

import odoo_repl

from odoo_repl import color
from odoo_repl import config
from odoo_repl import util
from odoo_repl.imports import odoo, t, MYPY, PY3, Field, BaseModel

if MYPY:
    Sourceable = t.Union[BaseModel, odoo.fields.Field, odoo_repl.methods.MethodProxy]

RE_FIELD = re.compile(
    r"""
    ^\s*             # leading whitespace from the start of the line
    ['"]?(\w+)['"]?  # field name, quoted if key in a _columns dict
    \s*[:=]\s*       # : for an old-style dict, = for new-style assignment
    fields2?\.       # assume "from odoo import fields"
                     # rarely, `as fields2` is used when old and new
                     # style fields are mixed
    (\w+)\(          # a single attribute deep, to avoid
                     # "date = fields.date.today()" false positive
    """,
    re.VERBOSE,
)

# Copied from odoo.tools.convert.xml_import.__init__ (Odoo 8)
# There may be false positives, I didn't check them all
RECORD_TAGS = [
    "record",
    "delete",
    "menuitem",
    "template",
    "workflow",
    "report",
    "ir_set",
    "act_window",
    "url",
    "assert",
]


if MYPY:
    _Source = t.NamedTuple(
        "Source", [("module", t.Text), ("fname", t.Text), ("lnum", t.Optional[int])]
    )
else:
    _Source = collections.namedtuple("Source", ("module", "fname", "lnum"))


class Source(_Source):
    __slots__ = ()

    @classmethod
    def from_cls(cls, src_cls):
        # type: (t.Type[BaseModel]) -> Source
        return cls(
            util.module(src_cls),
            getsourcefile(src_cls),
            inspect.getsourcelines(src_cls)[1],
        )


def getsourcefile(thing):
    # type: (t.Any) -> t.Text
    try:
        return inspect.getsourcefile(thing) or "???"
    except ValueError:
        return "???"


def find_source(thing):
    # type: (Sourceable) -> t.List[Source]
    if isinstance(thing, BaseModel) and hasattr(thing, "_ids"):
        if not thing._ids:
            return find_model_source(util.unwrap(thing))
        else:
            return find_record_source(thing)
    elif isinstance(thing, odoo.fields.Field):
        return find_field_source(thing)
    elif isinstance(thing, odoo_repl.methods.MethodProxy):
        return find_method_source(thing)
    else:
        raise TypeError(thing)


def format_source(source):
    # type: (Source) -> t.Text
    module, fname, lnum = source
    if config.clickable_filenames:
        # TODO: is including the hostname desirable?
        # GNOME doesn't seem to support remote file:// URIs anyway, and if we
        # somehow use the wrong hostname it'll mess things up.
        uri = "file://{hostname}{fname}".format(fname=fname, hostname=platform.node())
        fname = color.linkify(fname, uri)
    if lnum is not None:
        return "{}: {}:{}".format(color.module(module), fname, lnum)
    else:
        return "{}: {}".format(color.module(module), fname)


def format_sources(sourcelist):
    # type: (t.Iterable[Source]) -> t.List[t.Text]
    return [format_source(source) for source in sourcelist]


def find_model_source(model):
    # type: (BaseModel) -> t.List[Source]
    # Note: This does not include inherited models with other names.
    # We should probably show those in some way.
    return [
        Source.from_cls(cls)
        for cls in type(model).__bases__
        if cls.__module__ not in {"odoo.api", "openerp.api"}
        and (
            (getattr(cls, "_name", None) or getattr(cls, "_inherit", None))
            == model._name
        )
    ]


def find_record_source(record):
    # type: (BaseModel) -> t.List[Source]
    return [
        Source(defin.module, defin.fname, defin.elem.sourceline)
        for rec in record
        # We want the "oldest" sources at the end, to match finders
        # that go by MRO.
        # So we reverse xml_ids, because that one puts inheriting views
        # (newer) at the end.
        for rec_id in reversed(util.xml_ids(rec))
        for defin in xml_records[rec_id]
    ]


def find_field_source(field):
    # type: (Field) -> t.List[Source]
    res = []
    for cls in type(util.env[field.model_name]).__mro__:
        if field.name in getattr(cls, "_columns", ()) or field.name in vars(cls):
            if cls.__module__ in {"odoo.api", "openerp.api"}:
                continue
            fname = getsourcefile(cls)
            lines, lnum = inspect.getsourcelines(cls)
            for line in lines:
                match = RE_FIELD.match(line)
                if match and match.group(1) == field.name:
                    break
                lnum += 1
            else:
                lnum = None  # type: ignore
            res.append(Source(util.module(cls), fname, lnum))
    return res


def find_field_module(field):
    # type: (Field) -> t.Optional[t.Text]
    # In Odoo 10+ (or 9+?) fields have a ._module attribute, but it points to
    # the latest module to define the field, not the first, so we can't use it
    for cls in reversed(type(util.env[field.model_name]).__mro__):
        if field.name in getattr(cls, "_columns", ()) or field.name in vars(cls):
            module = getattr(cls, "_module", None)  # type: t.Optional[t.Text]
            if module:
                return module
    return None


def find_field_modules(field):
    # type: (Field) -> t.Set[t.Text]
    modules = set()
    for cls in reversed(type(util.env[field.model_name]).__mro__):
        if field.name in getattr(cls, "_columns", ()) or field.name in vars(cls):
            module = getattr(cls, "_module", None)  # type: t.Optional[t.Text]
            if module and cls.__module__ not in {"odoo.api", "openerp.api"}:
                modules.add(module)
    return modules


def find_method_source(method):
    # type: (odoo_repl.methods.MethodProxy) -> t.List[Source]
    res = []
    for cls in type(method.model).__mro__[1:]:
        if method.name in vars(cls):
            func = util.unpack_function(vars(cls)[method.name])
            res.append(
                Source(
                    util.module(cls),
                    getsourcefile(func),
                    inspect.getsourcelines(func)[1],
                )
            )
    return res


def extract_field_source(fname, lnum):
    # type: (t.Text, int) -> t.Text
    pieces = []
    depth = 0
    for line in iter(lambda: linecache.getline(fname, lnum), ""):
        for ind, char in enumerate(line):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    pieces.append(line[: ind + 1])
                    return "".join(pieces)
        pieces.append(line)
        lnum += 1
    return "".join(pieces)


if MYPY:
    from lxml.etree import _ElementTree

    _RecordDef = t.NamedTuple(
        "Employee", [("module", t.Text), ("fname", t.Text), ("elem", _ElementTree)]
    )
else:
    _RecordDef = collections.namedtuple("RecordDef", ("module", "fname", "elem"))


class RecordDef(_RecordDef):
    __slots__ = ()

    def to_source(self):
        # type: () -> Source
        return Source(module=self.module, fname=self.fname, lnum=self.elem.sourceline)


xml_records = collections.defaultdict(
    list
)  # type: t.DefaultDict[util.XmlId, t.List[RecordDef]]


def populate_xml_records(modules):
    # type: (t.Iterable[t.Tuple[t.Text, bool]]) -> None
    import lxml.etree

    if xml_records:
        # There is a race condition here but it seems hard enough to trigger
        return

    for module, demo in modules:
        path = odoo.modules.module.get_module_path(module, display_warning=False)
        if not path:
            continue
        manifest = odoo.modules.module.load_information_from_description_file(
            module, mod_path=path
        )
        data_files = list(manifest.get("data", ()))
        if demo:
            data_files.extend(manifest.get("demo", ()))
        for fname in data_files:
            if not fname.endswith(".xml"):
                continue
            fname = os.path.join(path, fname)
            if not os.path.isfile(fname):
                continue
            try:
                tree = lxml.etree.parse(fname)
            except Exception:  # Syntax error, for example
                continue
            for tag in RECORD_TAGS:
                for record in tree.iterfind("//" + tag):
                    if "id" not in record.attrib:
                        continue
                    rec_id = record.attrib["id"]
                    if "." not in rec_id:
                        ident = util.XmlId(module, rec_id)
                    else:
                        ident = util.XmlId(*rec_id.split("."))
                    xml_records[ident].append(
                        RecordDef(module=module, fname=fname, elem=record)
                    )


def _cleandoc(doc):
    # type: (t.Union[str, t.Text]) -> t.Text
    doc = inspect.cleandoc(doc)  # type: ignore
    if not PY3 and isinstance(doc, str):
        # Sometimes people put unicode in non-unicode docstrings
        # unicode.join does not like non-ascii strs so this has to be early
        try:
            # everybody's source code is UTF-8-compatible, right?
            doc = doc.decode("utf8")
        except UnicodeDecodeError:
            # Let's just hope for the best
            pass
    return doc


def find_docs(things):
    # type: (t.Iterable[t.Tuple[str, object]]) -> t.Iterable[t.Tuple[str, t.Text]]
    for name, thing in things:
        if isinstance(thing, (classmethod, staticmethod)):
            # ir.http.binary_content in Odoo 12 is a classmethod
            thing = thing.__func__
        doc = getattr(thing, "__doc__", None)
        if doc:
            doc = _cleandoc(doc)
            yield name, doc


def format_docs(docs):
    # type: (t.Iterable[t.Tuple[str, t.Text]]) -> t.Iterable[t.Text]
    docs = list(docs)
    for module, doc in docs:
        doc = color.highlight(doc, "rst")
        if len(docs) == 1:
            yield doc
        elif "\n" in doc:
            yield "{}:".format(color.module(module))
            yield doc
        else:
            yield "{}: {}".format(color.module(module), doc)
