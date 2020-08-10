import random

from odoo_repl.imports import t, BaseModel, Text
from odoo_repl import util


def search(
    model,  # type: BaseModel
    args,  # type: t.Sequence[object]
    field_vals,  # type: t.Dict[str, t.Any]
):
    # type: (...) -> BaseModel
    # if count=True, this returns an int, but that may not be worth annotating
    # TODO:
    # - inspect fields
    # - handle 2many relations
    offset = field_vals.pop("offset", 0)  # type: int
    limit = field_vals.pop("limit", None)  # type: t.Optional[int]
    order = field_vals.pop("order", "id")  # type: t.Optional[t.Text]
    count = field_vals.pop("count", False)  # type: bool
    shuf = field_vals.pop("shuf", None)  # type: t.Optional[int]
    if shuf and not (args or field_vals or offset or limit or count):
        # Doing a search seeds the cache with IDs, which tanks performance
        # Odoo will compute fields on many records at once even though you
        # won't use them
        query = "SELECT id FROM {}".format(model._table)
        if "active" in model._fields and not model._fields["active"].related:
            # TODO: handle related active fields
            query += " WHERE active = true"
        all_ids = util.sql(model.env, query)
        shuf = min(shuf, len(all_ids))
        return model.browse(random.sample(all_ids, shuf))
    clauses = _parse_search_query(args, field_vals)
    result = model.search(clauses, offset=offset, limit=limit, order=order, count=count)
    if shuf:
        shuf = min(shuf, len(result))
        return result.browse(random.sample(result._ids, shuf))
    return result


def _parse_search_query(
    args,  # type: t.Sequence[object]
    field_vals,  # type: t.Mapping[str, object]
):
    # type: (...) -> t.List[t.Tuple[str, str, object]]
    clauses = []
    state = "OUT"
    curr = None  # type: t.Optional[t.List[t.Any]]
    for arg in args:
        if state == "OUT":
            if isinstance(arg, list):
                clauses.extend(arg)
            elif isinstance(arg, tuple):
                clauses.append(arg)
            else:
                assert curr is None
                state = "IN"
                if isinstance(arg, Text):
                    curr = arg.split(None, 2)
                else:
                    curr = [arg]
        elif state == "IN":
            assert curr is not None
            curr.append(arg)

        if curr and len(curr) >= 3:
            clauses.append(tuple(curr))
            state = "OUT"
            curr = None

    if state == "IN":
        assert isinstance(curr, list)
        raise ValueError(
            "Couldn't divide into leaves: {!r}".format(clauses + [tuple(curr)])
        )
    clauses.extend((k, "=", v) for k, v in field_vals.items())

    def to_id(thing):
        # type: (object) -> t.Any
        if isinstance(thing, tuple):
            return tuple(map(to_id, thing))
        elif isinstance(thing, list):
            return list(map(to_id, thing))
        elif isinstance(thing, BaseModel):
            if len(thing) == 1:
                return thing.id
            return thing.ids
        return thing

    clauses = to_id(clauses)

    return clauses
