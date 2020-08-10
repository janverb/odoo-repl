from __future__ import print_function

import random
import subprocess
import sys

import odoo_repl
from odoo_repl.imports import t, BaseModel, TextLike, odoo
from odoo_repl import color
from odoo_repl import grep
from odoo_repl import sources
from odoo_repl import util


def record_repr(obj):
    # type: (BaseModel) -> t.Text
    """Display all of a record's fields."""
    obj = util.unwrap(obj)

    if not hasattr(obj, "_ids"):
        return repr(obj)
    elif not obj:
        return u"{}[]".format(obj._name)
    elif len(obj) > 1:
        return color.basic_render_record(obj)

    if obj.env.cr.closed:
        return color.basic_render_record(obj) + " (closed cursor)"

    field_names = sorted(
        field
        for field in obj._fields
        if field not in odoo_repl.models.FIELD_BLACKLIST
        and not obj._fields[field].related
    )
    max_len = max(len(f) for f in field_names) if field_names else 0
    parts = []

    parts.append(color.record_header(obj))
    name = obj.sudo().display_name
    default_name = "{},{}".format(obj._name, obj.id)
    if name and name != default_name:
        parts.append(color.display_name(name))

    if not obj.exists():
        parts.append(color.missing("Missing"))
        return "\n".join(parts)

    # Odoo precomputes a field for up to 200 records at a time.
    # This can be a problem if we're only interested in one of them.
    # So we do our best to disable it.

    # For Odoo 8, we do everything in a separate env where the ID cache is
    # empty. We make a separate env by changing the context. This has the added
    # advantage of informing models that they're running in odoo_repl, in case
    # they care. In _color_repr we clear the cache in case it got filled.

    # For Odoo 10-13, we slice the record. Odoo tries to be smart and narrows
    # the prefetch cache if we slice while keeping it when iterating.

    # I don't know what Odoo 9 does but I hope it's one of the above.

    # TODO: When .print_()ing a recordset we do want prefetching.

    if isinstance(obj.id, odoo.models.NewId):
        # Cache gets wonky if we start a new environment
        no_prefetch_obj = obj
    else:
        no_prefetch_obj = obj.with_context(odoo_repl=True)[:]

    for field in field_names:
        parts.append(
            "{}: ".format(color.field(field))
            + (max_len - len(field)) * " "
            + _color_repr(no_prefetch_obj, field)
        )

    history_lines = _get_create_write_history(obj.sudo())
    if history_lines:
        parts.append("")
        parts.extend(history_lines)

    src = sources.find_source(obj)
    if src:
        parts.append("")
        parts.extend(sources.format_sources(src))

    return "\n".join(parts)


def _color_repr(owner, field_name):
    # type: (BaseModel, t.Text) -> t.Text
    """Return a color-coded representation of a record's field value."""
    if hasattr(owner.env, "prefetch"):  # Not all Odoo versions
        # The prefetch cache may be filled up by previous calls, see record_repr
        owner.env.prefetch.clear()
    try:
        obj = getattr(owner, field_name)  # type: object
    except Exception as err:
        return color.missing(type(err).__name__)
    # We don't want to show passwords by default.
    # But if it's not a string then it's either a missing value (which is fine
    # to reveal) or a field that doesn't contain a password at all.
    if obj and isinstance(obj, TextLike) and "pass" in field_name:
        return color.missing("<censored>")
    field_type = owner._fields[field_name].type
    return color.color_value(obj, field_type)


def _get_create_write_history(obj):
    # type: (BaseModel) -> t.List[str]
    if "create_date" not in obj._fields:
        return []
    history_lines = []
    obj = obj.sudo()
    if obj.create_date:
        create_msg = "Created on {}".format(color.format_date(obj.create_date))
        if obj.create_uid and obj.create_uid.id != 1:
            create_msg += " by {}".format(color.render_user(obj.create_uid))
        history_lines.append(create_msg)
    if obj.write_date and obj.write_date != obj.create_date:
        write_msg = "Written on {}".format(color.format_date(obj.write_date))
        if obj.write_uid and obj.write_uid.id != 1:
            write_msg += " by {}".format(color.render_user(obj.write_uid))
        history_lines.append(write_msg)
    return history_lines


@util.patch(BaseModel)
def _repr_pretty_(self, printer, _cycle):
    # type: (BaseModel, t.Any, t.Any) -> None
    if printer.indentation == 0 and hasattr(self, "_ids"):
        printer.text(record_repr(self))
    else:
        printer.text(repr(self))


@util.patch(BaseModel)
def create_(
    self,  # type: BaseModel
    vals=None,  # type: t.Optional[t.Dict[str, t.Any]]
    **field_vals  # type: t.Any
):
    # type: (...) -> BaseModel
    """Create a new record, optionally with keyword arguments.

    .create_(x='test', y=<some record>) is typically equivalent to
    .create({"x": "test", "y": <some record>id}). 2many fields are also
    handled.

    If you make a typo in a field name you get a proper error.
    """
    if vals:
        field_vals.update(vals)
    for key, value in field_vals.items():
        if key not in self._fields:
            raise TypeError("Field '{}' does not exist".format(key))
        if util.is_record(value) or (
            isinstance(value, (list, tuple)) and value and util.is_record(value[0])
        ):
            # TODO: typecheck model
            field_type = self._fields[key].type
            if field_type.endswith("2many"):
                field_vals[key] = [(6, 0, value.ids)]
            elif field_type.endswith("2one"):
                if len(value) > 1:
                    raise TypeError("Can't link multiple records for '{}'".format(key))
                field_vals[key] = value.id
    return self.create(field_vals)


@util.patch(BaseModel)
def filtered_(
    self,  # type: odoo.models.AnyModel
    func=None,  # type: t.Optional[t.Callable[[odoo.models.AnyModel], bool]]
    **field_vals  # type: object
):
    # type: (...) -> odoo.models.AnyModel
    """Filter based on field values in addition to the usual .filtered() features.

    .filtered_(state='done') is equivalent to
    .filtered(lambda x: x.state == 'done').
    """
    this = self
    if func:
        this = this.filtered(func)
    if field_vals:
        this = this.filtered(
            lambda record: all(
                getattr(record, field) == value for field, value in field_vals.items()
            )
        )
    return this


@util.patch(BaseModel)
def source_(record, location=None, context=False):
    # type: (BaseModel, t.Optional[t.Text], bool) -> None
    import lxml.etree

    for rec in record:
        for rec_id in reversed(util.xml_ids(rec)):
            for definition in sources.xml_records[rec_id]:
                if location is not None and definition.module != location:
                    continue
                elem = definition.elem.getroottree() if context else definition.elem
                print(sources.format_source(definition.to_source()))
                src = lxml.etree.tostring(elem, encoding="unicode")
                print(color.highlight(src, "xml"), end="\n\n")


@util.patch(BaseModel)
def open_(self):
    # type: (BaseModel) -> None
    for record in self[:10]:
        subprocess.Popen(["xdg-open", util.link_for_record(record)])


@util.patch(BaseModel)
def grep_(record, *args, **kwargs):
    # type: (BaseModel, object, object) -> None
    """grep through all XML definitions of the record.

    See help(odoo_repl.grep) for more information.

    Note: because of technical limitations, formatting and line numbers don't
    always match up.
    """
    import lxml.etree

    argv = grep.build_grep_argv(args, kwargs)
    for rec in record:
        for rec_id in reversed(util.xml_ids(rec)):
            for definition in sources.xml_records[rec_id]:
                try:
                    grep.partial_grep(
                        argv,
                        lxml.etree.tostring(definition.elem, encoding="unicode"),
                        header=definition.fname,
                        lnum=definition.elem.sourceline,
                    )
                except grep.BadCommandline as err:
                    print(err, file=sys.stderr)
                    return
                except grep.NoResults:
                    continue
                else:
                    print()


@util.patch(BaseModel)
def shuf_(record, num=1):
    # type: (odoo.models.AnyModel, int) -> odoo.models.AnyModel
    num = min(len(record), num)
    return record.browse(random.sample(record._ids, num))
