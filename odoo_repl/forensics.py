"""These functions are experimental, and may be changed or removed."""

from __future__ import print_function
from __future__ import unicode_literals

from odoo_repl.imports import t, MYPY, Text, Field, BaseModel

if MYPY:
    Fingerprint = t.Tuple[t.Tuple[t.Text, object], ...]


def fingerprint(record):
    # type: (BaseModel) -> Fingerprint
    if len(record) != 1:
        raise ValueError("To get fingerprints of multiple records, use `fingerprints`.")

    def fieldprint(field):
        # type: (Field) -> t.Union[bool, t.Text]
        value = getattr(record, field.name)
        if field.type == "selection":
            assert isinstance(value, (Text, bool))
            return value
        return bool(value)

    return tuple(
        (name, fieldprint(field)) for name, field in sorted(record._fields.items())
    )


def fingerprints(records):
    # type: (BaseModel) -> t.FrozenSet[Fingerprint]
    return frozenset(map(fingerprint, records))


def dictfprints(records):
    # type: (BaseModel) -> t.List[t.Dict[t.Text, object]]
    return [dict(fprint) for fprint in fingerprints(records)]


def inhomogenities(records):
    # type: (BaseModel) -> None
    prints = dictfprints(records)
    for field in sorted(records._fields):
        values = {prnt[field] for prnt in prints}
        if len(values) != 1:
            print("{}:\t{!r}".format(field, values))


def differences(a, b, loose=False):
    # type: (BaseModel, BaseModel, bool) -> None
    if a._name != b._name:
        raise TypeError("Can only compare records of same model")

    def fmtvalset(valset):
        # type: (t.Set[object]) -> object
        if len(valset) == 1:
            return next(iter(valset))
        return valset

    aprints = dictfprints(a)
    bprints = dictfprints(b)
    for field in sorted(a._fields):
        avals = {prnt[field] for prnt in aprints}
        bvals = {prnt[field] for prnt in bprints}
        if not (avals & bvals) or (loose and avals != bvals):
            print("{}:\t{!r} vs {!r}".format(field, fmtvalset(avals), fmtvalset(bvals)))
