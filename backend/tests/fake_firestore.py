"""A small in-memory stand-in for the parts of the Firestore client that FirestoreStore uses.

It is NOT Firestore. It exists so the store's own logic (transactions, de-duplication, batching, save/load round
trip) can be run without a Firebase project. To make it more than a happy-path mock it enforces rules that real
Firestore enforces and that are easy to get wrong:

  * inside a transaction every read must come before the first write
  * a batch holds at most 500 writes
  * a query that filters on equality and orders by another field needs a composite index, and the fake only
    accepts the ones declared in firebase/firestore.indexes.json
  * stored data is copied in and out, so code cannot rely on shared references

What it cannot tell you: real latency, real contention between concurrent transactions, security rules, quotas.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

INDEXES_FILE = Path(__file__).resolve().parents[2] / "firebase" / "firestore.indexes.json"


class FakeFirestoreError(Exception):
    pass


def _declared_indexes():
    data = json.loads(INDEXES_FILE.read_text(encoding="utf-8"))
    return [[(f["fieldPath"], f["order"]) for f in idx["fields"]] for idx in data["indexes"]]


class FakeSnapshot:
    def __init__(self, ref, data):
        self.reference, self._data = ref, data

    @property
    def exists(self):
        return self._data is not None

    @property
    def id(self):
        return self.reference.id

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocument:
    def __init__(self, client, path):
        self._c, self.path = client, path

    @property
    def id(self):
        return self.path[-1]

    def collection(self, name):
        return FakeCollection(self._c, self.path + (name,))

    def get(self, transaction=None):
        if transaction is not None:
            transaction.check_read()
        return FakeSnapshot(self, self._c.docs.get(self.path) and copy.deepcopy(self._c.docs[self.path]))

    def set(self, data, merge=False):
        self._c.apply("set", self, data, merge)

    def update(self, fields):
        self._c.apply("update", self, fields)

    def delete(self):
        self._c.apply("delete", self)


class FakeQuery:
    def __init__(self, client, path, filters=(), orders=(), max_rows=None):
        self._c, self.path = client, path
        self.filters, self.orders, self.max_rows = list(filters), list(orders), max_rows

    def _clone(self, **kw):
        base = dict(filters=self.filters, orders=self.orders, max_rows=self.max_rows)
        base.update(kw)
        return FakeQuery(self._c, self.path, **base)

    def where(self, field_path=None, op_string=None, value=None, *, filter=None):
        if filter is not None:
            field_path, op_string, value = filter.field_path, filter.op_string, filter.value
        if op_string not in ("==", "in"):
            raise FakeFirestoreError(f"operator {op_string!r} is not supported by the fake")
        return self._clone(filters=self.filters + [(field_path, op_string, value)])

    def order_by(self, field_path, direction="ASCENDING"):
        if direction not in ("ASCENDING", "DESCENDING"):
            raise FakeFirestoreError(f"bad direction {direction!r}")
        return self._clone(orders=self.orders + [(field_path, direction)])

    def limit(self, n):
        return self._clone(max_rows=n)

    def _check_index(self):
        ordered = [o for o in self.orders if o[0] != "__name__"]
        if not ordered:
            return  # equality-only queries and key-ordered scans need no composite index
        equality = [f[0] for f in self.filters if f[1] == "=="]
        needed_tail = ordered[-1]
        for idx in _declared_indexes():
            if len(idx) == len(equality) + 1 and {f for f, _ in idx[:-1]} == set(equality) and idx[-1] == needed_tail:
                return
        raise FakeFirestoreError(f"query on {self.path} (equality {equality}, order {ordered}) needs a composite index "
                                 "that is not declared in firebase/firestore.indexes.json")

    def stream(self, transaction=None):
        if transaction is not None:
            transaction.check_read()
        self._check_index()
        rows = [(p, d) for p, d in self._c.docs.items() if len(p) == len(self.path) + 1 and p[:-1] == self.path]
        for field, op, value in self.filters:
            rows = [(p, d) for p, d in rows if (d.get(field) == value if op == "==" else d.get(field) in value)]
        for field, direction in reversed(self.orders):
            key = (lambda r: r[0][-1]) if field == "__name__" else (lambda r, f=field: r[1].get(f))
            rows.sort(key=key, reverse=direction == "DESCENDING")
        if self.max_rows is not None:
            rows = rows[: self.max_rows]
        return iter([FakeSnapshot(FakeDocument(self._c, p), copy.deepcopy(d)) for p, d in rows])


class FakeCollection(FakeQuery):
    def document(self, doc_id):
        return FakeDocument(self._c, self.path + (doc_id,))


class FakeBatch:
    def __init__(self, client):
        self._c, self._ops = client, []

    def set(self, ref, data):
        self._ops.append(("set", ref, copy.deepcopy(data), False))

    def delete(self, ref):
        self._ops.append(("delete", ref))

    def commit(self):
        if len(self._ops) > 500:
            raise FakeFirestoreError(f"a batch may hold at most 500 writes, got {len(self._ops)}")
        for op in self._ops:
            self._c.apply(*op)
        self._ops = []


class FakeTransaction:
    def __init__(self, client):
        self._c, self._ops, self._wrote = client, [], False

    def check_read(self):
        if self._wrote:
            raise FakeFirestoreError("a transaction must do all of its reads before its first write")

    def set(self, ref, data):
        self._wrote = True
        self._ops.append(("set", ref, copy.deepcopy(data), False))

    def update(self, ref, fields):
        self._wrote = True
        self._ops.append(("update", ref, copy.deepcopy(fields)))

    def commit(self):
        for op in self._ops:
            self._c.apply(*op)
        self._ops = []


def fake_transactional(fn):
    """Stands in for google.cloud.firestore.transactional: run, then commit the buffered writes."""
    def wrapper(transaction, *args, **kwargs):
        result = fn(transaction, *args, **kwargs)
        transaction.commit()
        return result
    return wrapper


class FakeClient:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return FakeCollection(self, (name,))

    def transaction(self):
        return FakeTransaction(self)

    def batch(self):
        return FakeBatch(self)

    def apply(self, kind, ref, data=None, merge=False):
        path = ref.path
        if kind == "set":
            self.docs[path] = {**self.docs.get(path, {}), **copy.deepcopy(data)} if merge else copy.deepcopy(data)
        elif kind == "update":
            if path not in self.docs:
                raise FakeFirestoreError(f"no document to update at {path}")
            self.docs[path].update(copy.deepcopy(data))
        elif kind == "delete":
            self.docs.pop(path, None)
