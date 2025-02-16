"""Test Home Assistant json utility functions."""
from datetime import datetime
from functools import partial
from json import JSONEncoder, dumps
import math
import os
from tempfile import mkdtemp
from unittest.mock import Mock, patch

import pytest

from homeassistant.core import Event, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.json import JSONEncoder as DefaultHASSJSONEncoder
from homeassistant.util.json import (
    SerializationError,
    find_paths_unserializable_data,
    load_json,
    save_json,
)

# Test data that can be saved as JSON
TEST_JSON_A = {"a": 1, "B": "two"}
TEST_JSON_B = {"a": "one", "B": 2}
# Test data that cannot be loaded as JSON
TEST_BAD_SERIALIED = "THIS IS NOT JSON\n"
TMP_DIR = None


@pytest.fixture(autouse=True)
def setup_and_teardown():
    """Clean up after tests."""
    global TMP_DIR
    TMP_DIR = mkdtemp()

    yield

    for fname in os.listdir(TMP_DIR):
        os.remove(os.path.join(TMP_DIR, fname))
    os.rmdir(TMP_DIR)


def _path_for(leaf_name):
    return os.path.join(TMP_DIR, f"{leaf_name}.json")


def test_save_and_load() -> None:
    """Test saving and loading back."""
    fname = _path_for("test1")
    save_json(fname, TEST_JSON_A)
    data = load_json(fname)
    assert data == TEST_JSON_A


def test_save_and_load_int_keys() -> None:
    """Test saving and loading back stringifies the keys."""
    fname = _path_for("test1")
    save_json(fname, {1: "a", 2: "b"})
    data = load_json(fname)
    assert data == {"1": "a", "2": "b"}


def test_save_and_load_private() -> None:
    """Test we can load private files and that they are protected."""
    fname = _path_for("test2")
    save_json(fname, TEST_JSON_A, private=True)
    data = load_json(fname)
    assert data == TEST_JSON_A
    stats = os.stat(fname)
    assert stats.st_mode & 0o77 == 0


@pytest.mark.parametrize("atomic_writes", [True, False])
def test_overwrite_and_reload(atomic_writes):
    """Test that we can overwrite an existing file and read back."""
    fname = _path_for("test3")
    save_json(fname, TEST_JSON_A, atomic_writes=atomic_writes)
    save_json(fname, TEST_JSON_B, atomic_writes=atomic_writes)
    data = load_json(fname)
    assert data == TEST_JSON_B


def test_save_bad_data() -> None:
    """Test error from trying to save unserializable data."""

    class CannotSerializeMe:
        """Cannot serialize this."""

    with pytest.raises(SerializationError) as excinfo:
        save_json("test4", {"hello": CannotSerializeMe()})

    assert "Failed to serialize to JSON: test4. Bad data at $.hello=" in str(
        excinfo.value
    )


def test_load_bad_data() -> None:
    """Test error from trying to load unserialisable data."""
    fname = _path_for("test5")
    with open(fname, "w") as fh:
        fh.write(TEST_BAD_SERIALIED)
    with pytest.raises(HomeAssistantError):
        load_json(fname)


def test_custom_encoder() -> None:
    """Test serializing with a custom encoder."""

    class MockJSONEncoder(JSONEncoder):
        """Mock JSON encoder."""

        def default(self, o):
            """Mock JSON encode method."""
            return "9"

    fname = _path_for("test6")
    save_json(fname, Mock(), encoder=MockJSONEncoder)
    data = load_json(fname)
    assert data == "9"


def test_default_encoder_is_passed() -> None:
    """Test we use orjson if they pass in the default encoder."""
    fname = _path_for("test6")
    with patch(
        "homeassistant.util.json.orjson.dumps", return_value=b"{}"
    ) as mock_orjson_dumps:
        save_json(fname, {"any": 1}, encoder=DefaultHASSJSONEncoder)
    assert len(mock_orjson_dumps.mock_calls) == 1
    # Patch json.dumps to make sure we are using the orjson path
    with patch("homeassistant.util.json.json.dumps", side_effect=Exception):
        save_json(fname, {"any": {1}}, encoder=DefaultHASSJSONEncoder)
    data = load_json(fname)
    assert data == {"any": [1]}


def test_find_unserializable_data() -> None:
    """Find unserializeable data."""
    assert find_paths_unserializable_data(1) == {}
    assert find_paths_unserializable_data([1, 2]) == {}
    assert find_paths_unserializable_data({"something": "yo"}) == {}

    assert find_paths_unserializable_data({"something": set()}) == {
        "$.something": set()
    }
    assert find_paths_unserializable_data({"something": [1, set()]}) == {
        "$.something[1]": set()
    }
    assert find_paths_unserializable_data([1, {"bla": set(), "blub": set()}]) == {
        "$[1].bla": set(),
        "$[1].blub": set(),
    }
    assert find_paths_unserializable_data({("A",): 1}) == {"$<key: ('A',)>": ("A",)}
    assert math.isnan(
        find_paths_unserializable_data(
            float("nan"), dump=partial(dumps, allow_nan=False)
        )["$"]
    )

    # Test custom encoder + State support.

    class MockJSONEncoder(JSONEncoder):
        """Mock JSON encoder."""

        def default(self, o):
            """Mock JSON encode method."""
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    bad_data = object()

    assert find_paths_unserializable_data(
        [State("mock_domain.mock_entity", "on", {"bad": bad_data})],
        dump=partial(dumps, cls=MockJSONEncoder),
    ) == {"$[0](State: mock_domain.mock_entity).attributes.bad": bad_data}

    assert find_paths_unserializable_data(
        [Event("bad_event", {"bad_attribute": bad_data})],
        dump=partial(dumps, cls=MockJSONEncoder),
    ) == {"$[0](Event: bad_event).data.bad_attribute": bad_data}

    class BadData:
        def __init__(self):
            self.bla = bad_data

        def as_dict(self):
            return {"bla": self.bla}

    assert find_paths_unserializable_data(
        BadData(),
        dump=partial(dumps, cls=MockJSONEncoder),
    ) == {"$(BadData).bla": bad_data}
