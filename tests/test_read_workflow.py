import json
from datetime import timedelta

from mcp.shared.memory import create_connected_server_and_client_session

from zotero_mcp.server import build_server


async def test_deterministic_read_workflow_and_envelope_sizes(library, zot):
    content = "0123456789ABCDEFGHIJ"
    parent = {
        "key": "PAPER001",
        "version": 7,
        "library": {"type": "user", "id": 1},
        "links": {
            "self": {"href": "http://localhost/items/PAPER001"},
            "attachment": {"href": "http://localhost:23119/api/users/0/items/PDF00001"},
        },
        "data": {
            "itemType": "journalArticle",
            "title": "Deterministic workflow",
            "date": "2024",
            "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
        },
    }
    attachment = {
        "key": "PDF00001",
        "version": 2,
        "data": {
            "itemType": "attachment",
            "parentItem": "PAPER001",
            "contentType": "application/pdf",
            "title": "paper.pdf",
        },
    }
    zot.top.return_value = [parent]
    zot.item.side_effect = None
    zot.item.return_value = parent
    zot.children.return_value = [attachment]
    from pyzotero import ResourceNotFoundError

    zot.fulltext_item.side_effect = [
        ResourceNotFoundError("parent has no index"),
        {"content": content},
        {"content": content},
    ]

    async with create_connected_server_and_client_session(
        build_server(library), read_timeout_seconds=timedelta(seconds=10)
    ) as session:
        recent = await session.call_tool("zotero_get_recent", {"limit": 1})
        assert recent.isError is False
        page = recent.structuredContent
        summary = page["items"][0]
        read_key = summary["key"]
        assert read_key == "PAPER001"
        assert summary["candidate_attachment_key"] == "PDF00001"

        first = await session.call_tool(
            "zotero_get_item_fulltext",
            {"item_key": read_key, "offset": 0, "max_characters": 10},
        )
        assert first.isError is False
        attachment_key = first.structuredContent["attachment_key"]
        revision = first.structuredContent["text_revision"]
        assert attachment_key == "PDF00001"

        second = await session.call_tool(
            "zotero_get_item_fulltext",
            {
                "item_key": attachment_key,
                "offset": 10,
                "max_characters": 10,
                "text_revision": revision,
            },
        )
        assert second.isError is False
        assert first.structuredContent["content"] + second.structuredContent["content"] == content
        assert second.structuredContent["attachment_key"] == attachment_key

        business_json = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
        envelope = {
            "content": [
                {"type": block.type, "text": block.text}
                for block in recent.content
                if hasattr(block, "text")
            ],
            "structuredContent": page,
            "isError": recent.isError,
        }
        envelope_json = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        assert len(business_json) < 350
        assert len(envelope_json) < 900
