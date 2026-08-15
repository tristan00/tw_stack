from __future__ import annotations




class CrossEntityError(Exception):
    pass


class Raw:

    __slots__ = ("v", "eid", "path")

    def __init__(self, v, eid, path):
        self.v = float(v)
        self.eid = eid
        self.path = path

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


    def __float__(self):
        return self.v

    def __repr__(self):
        return "Raw(%.4g, %s, %s)" % (self.v, self.eid, self.path)


class Reader:

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
    assert abs(float((x0 * 1.0) + (y0 * 0.0)) - 561.0) < 1e-9
    print("guard selftest OK: cross-entity arithmetic raises, scaling does not")


if __name__ == "__main__":
    _selftest()
