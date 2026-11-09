"""All tests use fake libraries or HTTP transports; real network access is forbidden."""

import copy
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zotero_mcp.client import Library


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not contact the network or the real Zotero library.")

    # Windows implements asyncio's internal socketpair using a loopback connect.
    # Permit only that synchronous stdlib operation; all other connects stay blocked.
    original_connect = socket.socket.connect
    original_pair = socket.socketpair
    internal = threading.local()

    def pair(*args, **kwargs):
        internal.socketpair = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            internal.socketpair = False

    def connect(sock, address):
        if getattr(internal, "socketpair", False):
            return original_connect(sock, address)
        return blocked()

    monkeypatch.setattr(socket, "socketpair", pair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def no_host_proxy(monkeypatch):
    # Host proxy settings never apply to fake transports, and httpx2 rejects
    # some real-world NO_PROXY entries such as "[::1]" when building a client.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


@pytest.fixture
def item():
    return {
        "key": "ABCD1234",
        "version": 7,
        "data": {
            "itemType": "journalArticle",
            "title": "中文文献",
            "tags": [{"tag": "keep", "type": 1}],
            "collections": ["COLL0001"],
            "creators": [{"creatorType": "author", "lastName": "张"}],
        },
    }


@pytest.fixture
def zot(item):
    zot = Mock(local=True, local_api_key="test-local-key", server_id="test-server")
    zot.item.side_effect = lambda item_key: {**copy.deepcopy(item), "key": item_key}
    zot.collection.return_value = {
        "key": "COLL0001",
        "version": 7,
        "data": {"name": "Reading", "parentCollection": "PARENT01"},
    }
    zot.create_items.return_value = {"successful": {}, "success": {"0": "NEW00001"}, "failed": {}}
    zot.create_collections.return_value = {"success": {"0": "NEWCOL01"}, "failed": {}}
    zot.update_item.return_value = True
    zot.delete_item.return_value = True
    zot.update_collection.return_value = True
    zot.delete_collection.return_value = True
    zot.items.return_value = [copy.deepcopy(item)]
    zot.top.return_value = [copy.deepcopy(item)]
    zot.children.return_value = []
    zot.item_types.return_value = [
        {"itemType": name, "localized": name}
        for name in ("attachment", "book", "journalArticle", "magazineArticle", "note")
    ]
    zot.everything.side_effect = lambda items: items
    zot.request = SimpleNamespace(text="@article{中文, title={中文文献}}")
    return zot


@pytest.fixture
def library(zot):
    library = Library(lambda: zot)
    yield library
    library.close()
