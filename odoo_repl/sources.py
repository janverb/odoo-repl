"""Functions for finding source code."""

import collections
import inspect
import os
import re

import odoo_repl

from odoo_repl import color
from odoo_repl import util
from odoo_repl.imports import odoo, t, MYPY

if MYPY:
    Sourceable = t.Union[
        odoo.models.BaseModel, odoo.fields.Field, odoo_repl.MethodProxy
    ]

RE_FIELD = re.compile(
    r"""
    ^\s*             # leading whitespace from the start of the line
    ['"]?(\w+)['"]?  # field name, quoted if key in a _columns dict
    \s*[:=]\s*       # : for an old-style dict, = for new-style assignment
    fields\.         # assume "from odoo import fields"
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
    "function",
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
        # type: (t.Type) -> Source
        return cls(
            util.module(src_cls),
            inspect.getsourcefile(src_cls) or "???",
            inspect.getsourcelines(src_cls)[1],
        )


def find_source(thing):
    # type: (Sourceable) -> t.List[Source]
    if isinstance(thing, odoo.models.BaseModel) and hasattr(thing, "_ids"):
        if not thing._ids:
            return find_model_source(util.unwrap(thing))
        else:
            return find_record_source(thing)
    elif isinstance(thing, odoo.fields.Field):
        return find_field_source(thing)
    elif isinstance(thing, odoo_repl.MethodProxy):
        return find_method_source(thing)
    else:
        raise TypeError(thing)


def format_source(source):
    # type: (Source) -> t.Text
    module, fname, lnum = source
    if lnum is not None:
        return "{}: {}:{}".format(color.module(module), fname, lnum)
    else:
        return "{}: {}".format(color.module(module), fname)


def format_sources(sources):
    # type: (t.Iterable[Source]) -> t.List[t.Text]
    return [format_source(source) for source in sources]


def find_model_source(model):
    # type: (odoo.models.BaseModel) -> t.List[Source]
    return [
        Source.from_cls(cls)
        for cls in type(model).__bases__
        if cls.__module__ not in {"odoo.api", "openerp.api"}
    ]


def find_record_source(record):
    # type: (odoo.models.BaseModel) -> t.List[Source]
    return [
        Source(defin.module, defin.fname, defin.elem.sourceline)
        for rec in record
        for rec_id in util.xml_ids(rec)
        for defin in xml_records()[".".join(rec_id)]
    ]


def find_field_source(field):
    # type: (odoo.fields.Field) -> t.List[Source]
    res = []
    for cls in type(odoo_repl.env[field.model_name]).__bases__:
        if field.name in getattr(cls, "_columns", ()) or field.name in vars(cls):
            if cls.__module__ in {"odoo.api", "openerp.api"}:
                continue
            fname = inspect.getsourcefile(cls) or "???"
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


def find_method_source(method):
    # type: (odoo_repl.MethodProxy) -> t.List[Source]
    res = []
    for cls in type(method.model).mro()[1:]:
        if method.name in vars(cls):
            func = util.unpack_function(vars(cls)[method.name])
            res.append(
                Source(
                    util.module(cls),
                    inspect.getsourcefile(func) or "???",
                    inspect.getsourcelines(func)[1],
                )
            )
    return res


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


_xml_records = None  # type: t.Optional[t.DefaultDict[t.Text, t.List[RecordDef]]]


def xml_records():
    # type: () -> t.DefaultDict[t.Text, t.List[RecordDef]]
    import lxml.etree

    global _xml_records

    if _xml_records is not None:
        return _xml_records

    _xml_records = collections.defaultdict(list)
    for module, demo in odoo_repl.sql(
        "SELECT name, demo FROM ir_module_module WHERE state = 'installed'"
    ):
        manifest = odoo.modules.module.load_information_from_description_file(module)
        path = odoo.modules.module.get_module_path(module)
        if not path:
            continue
        data_files = list(manifest.get("data", ()))
        if demo:
            data_files.extend(manifest.get("demo", ()))
        for fname in data_files:
            if not fname.endswith(".xml"):
                continue
            fname = os.path.join(path, fname)
            if not os.path.isfile(fname):
                continue
            tree = lxml.etree.parse(fname)
            for tag in RECORD_TAGS:
                for record in tree.iterfind("//" + tag):
                    if "id" not in record.attrib:
                        continue
                    rec_id = record.attrib["id"]
                    if "." not in rec_id:
                        rec_id = module + "." + rec_id
                    _xml_records[rec_id].append(
                        RecordDef(module=module, fname=fname, elem=record)
                    )
    return _xml_records
