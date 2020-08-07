from collections import defaultdict
from subprocess import Popen, PIPE

from odoo_repl import util
from odoo_repl.imports import AnyModel, BaseModel, t, Unicode


def _fzf(vals, flags=()):
    # type: (t.Iterable[t.Text], t.Iterable[str]) -> t.Optional[t.List[t.Text]]
    argv = ["fzf", "--read0", "--print0"]
    argv.extend(flags)
    encoded = b"\0".join(val.encode("utf8") for val in vals)
    proc = Popen(argv, stdin=PIPE, stdout=PIPE)
    output, _ = proc.communicate(input=encoded)
    if proc.returncode != 0:
        # A noisy traceback isn't appropriate here
        return None
    results = output.decode("utf8").split("\0")
    assert not results[-1]
    results.pop()
    return results


def fzf(vals):
    # type: (t.Iterable[t.Text]) -> t.Optional[t.List[t.Text]]
    """Call fzf to narrow down a list of strings."""
    return _fzf(vals, ["-m"])


def fzf_single(vals):
    # type: (t.Iterable[t.Text]) -> t.Optional[t.Text]
    """Call fzf to narrow down a list of strings to a single choice."""
    result = _fzf(vals)
    if result is None:
        return None
    return result[0]


@util.patch(BaseModel, "fzf_")
def fzf_field(model, field="display_name"):
    # type: (AnyModel, str) -> t.Optional[AnyModel]
    """Narrow down a recordset based on a field, by default display_name."""
    f_obj = model._fields.get(field)
    if model._auto and f_obj and f_obj.store and not f_obj.relational:
        return fzf_stored_field(model, field)
    if len(model) == 0:
        model = model.search([])
    values = model.mapped(field)  # type: t.Union[BaseModel, t.Sequence[object]]
    do_display_name = False
    if isinstance(values, BaseModel):
        do_display_name = True
        values = values.mapped("display_name")
    result = fzf(sorted(set(map(Unicode, values))))
    if result is None:
        return None
    res_set = set(result)
    # TODO: this doesn't work if field is dotted
    # Using mapped() instead of indexing has potential but has its own nasty
    # edges with regards to multiple values
    filterer = (
        (lambda rec: bool(set(rec[field].mapped("display_name")) & res_set))
        if do_display_name
        else (lambda rec: Unicode(rec[field]) in res_set)
    )
    return model.filtered(filterer)


def fzf_stored_field(model, field):
    # type: (AnyModel, str) -> t.Optional[AnyModel]
    """A faster version of fzf_field for stored fields."""
    # Field should never be untrusted, but just in case
    field = '"{}"'.format(field.replace('"', '""'))
    query = "SELECT id, {} FROM {}".format(field, model._table)
    params = []
    if model:
        query += " WHERE id IN %s"
        params.append(tuple(model.ids))
    with util.savepoint(model.env.cr):
        model.env.cr.execute(query, params)
        by_value = defaultdict(list)
        for id_, val in model.env.cr.fetchall():
            by_value[Unicode(val)].append(id_)
    result = fzf(sorted(by_value))
    if result is None:
        return None
    return model.browse(id_ for value in result for id_ in by_value[value])


@util.patch(BaseModel, "xfzf_")
def fzf_xml_id(model):
    # type: (AnyModel) -> t.Optional[AnyModel]
    """Select records by their XML IDs."""
    query = """SELECT module, name, res_id FROM ir_model_data
    WHERE model = %s"""
    params = [model._name]  # type: t.List[object]
    if model:
        query += " AND res_id IN %s"
        params.append(tuple(model.ids))
    with util.savepoint(model.env.cr):
        model.env.cr.execute(query, params)
        xml_ids = {
            u"{}.{}".format(module, name): id_
            for module, name, id_ in model.env.cr.fetchall()
        }
    result = fzf(sorted(xml_ids))
    if result is None:
        return None
    return model.browse(xml_ids[ident] for ident in result)
