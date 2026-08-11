from __future__ import annotations

"""Runtime enforcement of the no-derived-features rule.

The v2 model was a CatBoost feature block wearing a graph. When told to remove the
CatBoost features, my first rebuild attempt kept `reach_max`, the eight reach rays and
the ego->target bearing by *relocating* them onto a node and an edge, and wrote a comment
explaining why that was principled. It was not. The rule is about the information, not
the variable name.

So the rule is a type here, not a promise.

Every scalar that enters the graph must be a `Raw`, and a `Raw` carries the id of the
entity it was read from. Arithmetic between two `Raw`s that came from *different*
entities raises `CrossEntityError`. Scaling, clipping and offsetting by plain numbers is
fine -- that is normalisation, not feature engineering.

    units  = Raw(8.0,  "char:17", "world.armies[].units")
    tunits = Raw(12.0, "char:99", "world.hostiles[].units")
    units / (tunits + 1.0)     -> CrossEntityError
    units / 20.0               -> Raw(0.4, "char:17", ...)

`strength_ratio` is therefore unwritable. So is a distance between two positions, and so
is a reachability envelope compared against a target. If the network wants those it can
derive them: the attacker's units sit on one node, the defender's on another, and there
is an edge between them.
"""

import math


class CrossEntityError(Exception):
    """Raised when a value combines two entities. That value is a banned feature."""


class Raw:
    """A scalar tagged with the single entity and record path it was read from."""

    __slots__ = ("v", "eid", "path")

    def __init__(self, v, eid, path):
        self.v = float(v)
        self.eid = eid
        self.path = path

    # -- combination rules -------------------------------------------------
    def _merge(self, other, op):
        if isinstance(other, Raw):
            if other.eid != self.eid:
                raise CrossEntityError(
                    "refusing to combine %s (%s) with %s (%s) via %s -- a value derived "
                    "from two entities is a banned feature; put both on their own nodes "
                    "and let message passing relate them"
                    % (self.path, self.eid, other.path, other.eid, op))
            return other.v, "%s%s%s" % (self.path, op, other.path)
        return float(other), self.path

    def __add__(self, o):
        b, p = self._merge(o, "+")
        return Raw(self.v + b, self.eid, p)

    def __sub__(self, o):
        b, p = self._merge(o, "-")
        return Raw(self.v - b, self.eid, p)

    def __mul__(self, o):
        b, p = self._merge(o, "*")
        return Raw(self.v * b, self.eid, p)

    def __truediv__(self, o):
        b, p = self._merge(o, "/")
        return Raw(self.v / b if b else 0.0, self.eid, p)

    __radd__ = __add__
    __rmul__ = __mul__

    def __rsub__(self, o):
        b, p = self._merge(o, "-")
        return Raw(b - self.v, self.eid, p)

    def __rtruediv__(self, o):
        b, p = self._merge(o, "/")
        return Raw(b / self.v if self.v else 0.0, self.eid, p)

    def __neg__(self):
        return Raw(-self.v, self.eid, self.path)

    def clip(self, lo, hi):
        return Raw(max(lo, min(hi, self.v)), self.eid, self.path)

    def slog(self):
        """Signed log compression. Monotone in one value -- not a combination."""
        return Raw(math.copysign(math.log1p(abs(self.v)), self.v), self.eid, self.path)

    def __float__(self):
        return self.v

    def __repr__(self):
        return "Raw(%.4g, %s, %s)" % (self.v, self.eid, self.path)


class Reader:
    """Reads fields off one record object, tagging everything with that entity's id."""

    __slots__ = ("row", "eid", "base")

    def __init__(self, row, eid, base):
        self.row = row or {}
        self.eid = eid
        self.base = base

    def num(self, key, default=0.0):
        v = self.row.get(key)
        if isinstance(v, bool):
            v = 1.0 if v else 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = float(default)
        if f != f:
            f = float(default)
        return Raw(f, self.eid, "%s.%s" % (self.base, key))

    def flag(self, key):
        v = self.row.get(key)
        return Raw(1.0 if (v is True or v == 1 or v == 1.0) else 0.0,
                   self.eid, "%s.%s" % (self.base, key))

    def present(self, key):
        return Raw(1.0 if self.row.get(key) is not None else 0.0,
                   self.eid, "%s.%s?" % (self.base, key))

    def const(self, value, name):
        """A value decided by the builder, not read from the record (e.g. a type flag)."""
        return Raw(value, self.eid, "%s#%s" % (self.base, name))

    def raw(self, key):
        return self.row.get(key)


def _selftest():
    a = Raw(8.0, "char:17", "armies[].units")
    b = Raw(12.0, "char:99", "hostiles[].units")
    assert abs(float(a / 20.0) - 0.4) < 1e-9, "scaling by a constant must be allowed"
    assert abs(float((a + 2.0) - 1.0) - 9.0) < 1e-9
    try:
        a / (b + 1.0)
        raise AssertionError("selftest FAILED: strength_ratio was writable")
    except CrossEntityError:
        pass
    x0, y0 = Raw(561.0, "char:17", "x"), Raw(32.0, "char:17", "y")
    x1 = Raw(1140.0, "char:99", "x")
    try:
        x1 - x0
        raise AssertionError("selftest FAILED: a cross-entity distance was writable")
    except CrossEntityError:
        pass
    # geometry within one entity is fine -- it is that entity's own state
    assert abs(float((x0 * 1.0) + (y0 * 0.0)) - 561.0) < 1e-9
    print("guard selftest OK: cross-entity arithmetic raises, scaling does not")


if __name__ == "__main__":
    # No interpreter guard here on purpose: this file imports nothing but `math`, so it
    # behaves identically under any python and has no half to silently skip.
    _selftest()
