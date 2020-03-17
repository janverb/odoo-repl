from subprocess import Popen, PIPE

from odoo_repl.imports import AnyModel, BaseModel, t, Unicode


def fzf(vals):
    # type: (t.Iterable[t.Text]) -> t.Optional[t.List[t.Text]]
    """Call fzf to narrow down a list of strings."""
    encoded = b"\0".join(val.encode("utf8") for val in vals)
    proc = Popen(["fzf", "--read0", "--print0"], stdin=PIPE, stdout=PIPE)
    assert proc.stdin
    assert proc.stdout
    proc.stdin.write(encoded)
    proc.stdin.close()
    return_code = proc.wait()
    if return_code != 0:
        return None
    return proc.stdout.read().decode("utf8").strip("\0").split("\0")


def fzf_field(model, field="display_name"):
    # type: (AnyModel, str) -> t.Optional[AnyModel]
    """Narrow down a recordset based on a field, by default display_name."""
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
        (lambda rec: rec[field].display_name in res_set)
        if do_display_name
        else (lambda rec: Unicode(rec[field]) in res_set)
    )
    return model.filtered(filterer)
