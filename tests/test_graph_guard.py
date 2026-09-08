import operator

import pytest

from advisor.mapgraph.guard import CrossEntityError, Raw, Reader


@pytest.mark.parametrize("operation", [operator.add, operator.sub, operator.mul, operator.truediv])
def test_cross_entity_arithmetic_is_rejected(operation):
    with pytest.raises(CrossEntityError):
        operation(Raw(8, "char:1", "units"), Raw(2, "char:2", "units"))


def test_same_entity_arithmetic_keeps_provenance():
    value = Raw(8, "char:1", "units") / Raw(2, "char:1", "armies")
    assert float(value) == 4
    assert value.eid == "char:1"
    assert value.path == "units/armies"


@pytest.mark.parametrize("value", [None, "invalid", float("nan")])
def test_invalid_scalar_uses_explicit_default(value):
    result = Reader({"rank": value}, "char:1", "state").num("rank", default=3)
    assert float(result) == 3
    assert result.eid == "char:1"
    assert result.path == "state.rank"


def test_presence_distinguishes_zero_from_missing():
    reader = Reader({"zero": 0, "empty": None}, "char:1", "state")
    assert float(reader.present("zero")) == 1
    assert float(reader.present("empty")) == 0
    assert float(reader.present("absent")) == 0
