import pytest
import mongomock.collection
import pymongo.collection
from datetime import datetime
from maggma.core import StoreError
from maggma.stores import MongoStore, MemoryStore, JSONStore
from maggma.validators import JSONSchemaValidator


@pytest.fixture
def mongostore():
    store = MongoStore("maggma_test", "test")
    store.connect()
    yield store
    store._collection.drop()


@pytest.fixture
def memorystore():
    store = MemoryStore()
    store.connect()
    return store


@pytest.fixture
def jsonstore(test_dir):

    files = []
    for f in ["a.json", "b.json"]:
        files.append(test_dir / "test_set" / f)

    return JSONStore(files)


def test_mongostore_connect():
    mongostore = MongoStore("maggma_test", "test")
    assert mongostore._collection is None
    mongostore.connect()
    assert isinstance(mongostore._collection, pymongo.collection.Collection)


def test_mongostore_query(mongostore):
    mongostore._collection.insert_one({"a": 1, "b": 2, "c": 3})
    assert mongostore.query_one(properties=["a"])["a"] == 1
    assert mongostore.query_one(properties=["a"])["a"] == 1
    assert mongostore.query_one(properties=["b"])["b"] == 2
    assert mongostore.query_one(properties=["c"])["c"] == 3


def test_mongostore_distinct(mongostore):
    mongostore._collection.insert_one({"a": 1, "b": 2, "c": 3})
    mongostore._collection.insert_one({"a": 4, "d": 5, "e": 6, "g": {"h": 1}})
    assert set(mongostore.distinct("a")) == {1, 4}

    # Test list distinct functionality
    mongostore._collection.insert_one({"a": 4, "d": 6, "e": 7})
    mongostore._collection.insert_one({"a": 4, "d": 6, "g": {"h": 2}})
    ad_distinct = mongostore.distinct(["a", "d"])
    assert len(ad_distinct) == 3
    assert {"a": 4, "d": 6} in ad_distinct
    assert {"a": 1} in ad_distinct
    assert len(mongostore.distinct(["d", "e"], {"a": 4})) == 3
    all_exist = mongostore.distinct(["a", "b"], all_exist=True)
    assert len(all_exist) == 1
    all_exist2 = mongostore.distinct(["a", "e"], all_exist=True, criteria={"d": 6})
    assert len(all_exist2) == 1

    # Test distinct subdocument functionality
    ghs = mongostore.distinct("g.h")
    assert set(ghs) == {1, 2, None}
    ghs_ds = mongostore.distinct(["d", "g.h"], all_exist=True)
    assert {s["g"]["h"] for s in ghs_ds} == {1, 2}
    assert {s["d"] for s in ghs_ds}, {5, 6}


def test_mongostore_update(mongostore):
    mongostore.update({"e": 6, "d": 4}, key="e")
    assert (
        mongostore.query_one(criteria={"d": {"$exists": 1}}, properties=["d"])["d"] == 4
    )

    mongostore.update([{"e": 7, "d": 8, "f": 9}], key=["d", "f"])
    assert mongostore.query_one(criteria={"d": 8, "f": 9}, properties=["e"])["e"] == 7

    mongostore.update([{"e": 11, "d": 8, "f": 9}], key=["d", "f"])
    assert mongostore.query_one(criteria={"d": 8, "f": 9}, properties=["e"])["e"] == 11

    test_schema = {
        "type": "object",
        "properties": {"e": {"type": "integer"}},
        "required": ["e"],
    }
    mongostore.validator = JSONSchemaValidator(schema=test_schema)
    mongostore.update({"e": 100, "d": 3}, key="e")

    # Non strict update
    mongostore.update({"e": "abc", "d": 3}, key="e")


def test_mongostore_groupby(mongostore):
    mongostore.update(
        [
            {"e": 7, "d": 9, "f": 9},
            {"e": 7, "d": 9, "f": 10},
            {"e": 8, "d": 9, "f": 11},
            {"e": 9, "d": 10, "f": 12},
        ],
        key="f",
    )
    data = list(mongostore.groupby("d"))
    assert len(data) == 2
    grouped_by_9 = [g[1] for g in data if g[0]["d"] == 9][0]
    assert len(grouped_by_9) == 3
    grouped_by_10 = [g[1] for g in data if g[0]["d"] == 10][0]
    assert len(grouped_by_10) == 1

    data = list(mongostore.groupby(["e", "d"]))
    assert len(data) == 3


def test_mongostore_remove_docs(mongostore):
    mongostore._collection.insert_one({"a": 1, "b": 2, "c": 3})
    mongostore._collection.insert_one({"a": 4, "d": 5, "e": 6, "g": {"h": 1}})
    mongostore.remove_docs({"a": 1})
    assert len(list(mongostore.query({"a": 4}))) == 1
    assert len(list(mongostore.query({"a": 1}))) == 0


def test_mongostore_from_db_file(mongostore, db_json):
    ms = MongoStore.from_db_file(db_json)
    ms.connect()
    assert ms._collection.full_name == "maggma_tests.tmp"


def test_mongostore_from_collection(mongostore, db_json):
    ms = MongoStore.from_db_file(db_json)
    ms.connect()

    other_ms = MongoStore.from_collection(ms._collection)
    assert ms._collection.full_name == other_ms._collection.full_name
    assert ms.database == other_ms.database


def test_mongostore_name(mongostore):
    assert mongostore.name == "mongo://localhost/maggma_test/test"


def test_ensure_index(mongostore):
    assert mongostore.ensure_index("test_key")
    # TODO: How to check for exception?


def test_mongostore_last_updated(mongostore):
    assert mongostore.last_updated == datetime.min
    start_time = datetime.utcnow()
    mongostore._collection.insert_one({mongostore.key: 1, "a": 1})
    with pytest.raises(StoreError) as cm:
        mongostore.last_updated
    assert cm.match(mongostore.last_updated_field)
    mongostore.update(
        [{mongostore.key: 1, "a": 1, mongostore.last_updated_field: datetime.utcnow()}]
    )
    assert mongostore.last_updated > start_time


def test_mongostore_newer_in(mongostore):
    target = MongoStore("maggma_test", "test_target")
    target.connect()

    # make sure docs are newer in mongostore then target and check updated_keys

    target.update(
        [
            {mongostore.key: i, mongostore.last_updated_field: datetime.utcnow()}
            for i in range(10)
        ]
    )

    # Update docs in source
    mongostore.update(
        [
            {mongostore.key: i, mongostore.last_updated_field: datetime.utcnow()}
            for i in range(10)
        ]
    )

    assert len(target.newer_in(mongostore)) == 10
    assert len(target.newer_in(mongostore, exhaustive=True)) == 10
    assert len(mongostore.newer_in(target)) == 0

    target._collection.drop()


# Memory store tests
def test_memory_store_connect():
    memorystore = MemoryStore()
    assert memorystore._collection is None
    memorystore.connect()
    assert isinstance(memorystore._collection, mongomock.collection.Collection)


def test_groupby(memorystore):
    memorystore.update(
        [
            {"e": 7, "d": 9, "f": 9},
            {"e": 7, "d": 9, "f": 10},
            {"e": 8, "d": 9, "f": 11},
            {"e": 9, "d": 10, "f": 12},
        ],
        key="f",
    )
    data = list(memorystore.groupby("d"))
    assert len(data) == 2
    grouped_by_9 = [g[1] for g in data if g[0]["d"] == 9][0]
    assert len(grouped_by_9) == 3
    grouped_by_10 = [g[1] for g in data if g[0]["d"] == 10][0]
    assert len(grouped_by_10) == 1

    data = list(memorystore.groupby(["e", "d"]))
    assert len(data) == 3

    memorystore.update(
        [
            {"e": {"d": 9}, "f": 9},
            {"e": {"d": 9}, "f": 10},
            {"e": {"d": 9}, "f": 11},
            {"e": {"d": 10}, "f": 12},
        ],
        key="f",
    )
    data = list(memorystore.groupby("e.d"))
    assert len(data) == 2


def test_json_store_load(jsonstore, test_dir):
    jsonstore.connect()
    assert len(list(jsonstore.query())) == 20

    jsonstore = JSONStore(test_dir / "test_set" / "c.json.gz")
    jsonstore.connect()
    assert len(list(jsonstore.query())) == 20


def test_eq(mongostore, memorystore, jsonstore):
    assert mongostore == mongostore
    assert memorystore == memorystore
    assert jsonstore == jsonstore

    assert mongostore != memorystore
    assert mongostore != jsonstore
    assert memorystore != jsonstore