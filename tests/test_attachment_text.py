"""Tests for original UTF-8 attachment reads and fixed Markdown uploads."""

from pathlib import Path

import httpx2 as http
import pytest

from zotero_mcp.errors import (
    ATTACHMENT_CONTENT_CONFLICT,
    INVALID_ARGUMENT,
)


def attachment(
    key: str = "ATTACH01",
    *,
    filename: str = "paper-chew-fulltext.md",
    content_type: str | None = "text/markdown",
) -> dict:
    return {
        "key": key,
        "data": {
            "itemType": "attachment",
            "parentItem": "ABCD1234",
            "filename": filename,
            "contentType": content_type,
        },
    }


def test_read_attachment_text_uses_original_bytes_and_pages(library, zot):
    zot.item.side_effect = None
    zot.item.return_value = attachment()
    raw = "# 标题\r\n𠮷正文".encode()
    zot.file.return_value = raw

    first = library.read_attachment_text("ATTACH01", max_characters=4)
    second = library.read_attachment_text(
        "ATTACH01",
        offset=first["next_offset"],
        max_characters=50,
    )

    assert first["source"] == "attachment-file"
    assert first["encoding"] == "utf-8"
    assert first["content"] + second["content"] == raw.decode("utf-8")
    assert first["byte_length"] == len(raw)
    assert first["content_sha256"] == second["content_sha256"]
    zot.fulltext_item.assert_not_called()
    assert zot.file.call_count == 2


@pytest.mark.parametrize("content_type", ["application/octet-stream", None])
def test_read_attachment_text_accepts_legacy_markdown_mime(content_type, library, zot):
    zot.item.side_effect = None
    zot.item.return_value = attachment(content_type=content_type)
    zot.file.return_value = b"# Legacy Markdown\n"

    result = library.read_attachment_text("ATTACH01")

    assert result["content"] == "# Legacy Markdown\n"
    assert result["content_type"] == "application/octet-stream"


def test_read_attachment_text_rejects_invalid_utf8_legacy_markdown(library, zot):
    zot.item.side_effect = None
    zot.item.return_value = attachment(content_type="application/octet-stream")
    zot.file.return_value = b"# invalid \xff\n"

    with pytest.raises(ValueError) as caught:
        library.read_attachment_text("ATTACH01")

    assert caught.value.code == INVALID_ARGUMENT


def test_read_attachment_text_rejects_pdf_before_file_read(library, zot):
    zot.item.side_effect = None
    zot.item.return_value = attachment(filename="paper.pdf", content_type="application/pdf")

    with pytest.raises(ValueError) as caught:
        library.read_attachment_text("ATTACH01")

    assert caught.value.code == INVALID_ARGUMENT
    zot.file.assert_not_called()


def test_read_attachment_text_rejects_binary_signature_with_legacy_markdown_metadata(library, zot):
    zot.item.side_effect = None
    zot.item.return_value = attachment(content_type="application/octet-stream")
    zot.file.return_value = b"%PDF-1.7\n"

    with pytest.raises(ValueError) as caught:
        library.read_attachment_text("ATTACH01")

    assert caught.value.code == INVALID_ARGUMENT


def test_read_attachment_text_handles_local_file_redirect_with_configured_client(
    library, zot, tmp_path
):
    zot.item.side_effect = None
    zot.item.return_value = attachment()
    zot.file.side_effect = NotImplementedError("Only local paths are allowed")
    zot.endpoint = "http://localhost:23119/api"
    zot.library_type = "users"
    zot.library_id = "0"
    zot._local_headers.return_value = {}
    target = tmp_path / "paper.md"
    target.write_bytes(b"# Redirected\\r\\n")
    zot.client.get.return_value = http.Response(
        302,
        headers={"Location": target.as_uri()},
    )

    result = library.read_attachment_text("ATTACH01")

    assert result["content"] == "# Redirected\\r\\n"
    assert result["source"] == "attachment-file"
    zot.client.get.assert_called_once_with(
        "http://localhost:23119/api/users/0/items/ATTACH01/file",
        headers={},
        follow_redirects=False,
        timeout=120,
    )
    zot.fulltext_item.assert_not_called()


def test_read_attachment_text_rejects_remote_file_redirect(library, zot, tmp_path):
    zot.item.side_effect = None
    zot.item.return_value = attachment()
    zot.file.side_effect = NotImplementedError("Only local paths are allowed")
    zot.endpoint = "http://localhost:23119/api"
    zot.library_type = "users"
    zot.library_id = "0"
    zot._local_headers.return_value = {}
    target = tmp_path / "paper.md"
    zot.client.get.return_value = http.Response(
        302,
        headers={"Location": "file://remote-host" + target.as_posix()},
    )

    with pytest.raises(ValueError) as caught:
        library.read_attachment_text("ATTACH01")

    assert caught.value.code == INVALID_ARGUMENT
    zot.fulltext_item.assert_not_called()


@pytest.mark.parametrize("guessed_type", ["application/octet-stream", None])
def test_attach_markdown_overrides_os_mime_guess(guessed_type, library, zot, monkeypatch):
    zot.children.return_value = []
    zot.everything.side_effect = lambda items: items
    zot.upload_attachments.return_value = {
        "success": [{"key": "NEW00001"}],
        "failure": [],
        "unchanged": [],
    }
    monkeypatch.setattr(
        "zotero_mcp.client.mimetypes.guess_type",
        lambda filename: (guessed_type, None),
    )

    result = library.attach_markdown("ABCD1234", "# Paper\n")

    assert result["ok"] is True
    assert zot.create_items.call_args.args[0][0]["contentType"] == "text/markdown"


def test_attach_markdown_writes_fixed_temp_file_and_reuses_attach_file(library, zot):
    zot.children.return_value = []
    zot.everything.side_effect = lambda items: items
    observed = {}

    def upload(attachments, parentid=None, basedir=None):
        source = Path(basedir, attachments[0]["filename"])
        observed["exists"] = source.is_file()
        observed["content"] = source.read_text(encoding="utf-8")
        return {"success": [{"key": attachments[0]["key"]}], "failure": [], "unchanged": []}

    zot.upload_attachments.side_effect = upload
    content = "# Paper\n\n正文"

    result = library.attach_markdown("ABCD1234", content)

    assert result["ok"] is True
    assert result["key"] == "NEW00001"
    assert "filename" not in result
    assert "content_sha256" not in result
    assert observed == {"exists": True, "content": content}
    metadata = zot.create_items.call_args.args[0][0]
    assert metadata["filename"] == "paper-chew-fulltext.md"
    assert metadata["contentType"] == "text/markdown"


def test_attach_markdown_same_content_is_unchanged_without_create(library, zot):
    content = "# Existing\n"
    zot.children.return_value = [attachment()]
    zot.everything.side_effect = lambda items: items
    zot.file.return_value = content.encode("utf-8")

    result = library.attach_markdown("ABCD1234", content)

    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["key"] == "ATTACH01"
    assert result["results"][0]["status"] == "unchanged"
    zot.create_items.assert_not_called()
    zot.upload_attachments.assert_not_called()


def test_attach_markdown_different_content_is_conflict_without_create(library, zot):
    zot.children.return_value = [attachment()]
    zot.everything.side_effect = lambda items: items
    zot.file.return_value = b"# Existing\n"

    result = library.attach_markdown("ABCD1234", "# New\n")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == ATTACHMENT_CONTENT_CONFLICT
    assert result["key"] == "ATTACH01"
    zot.create_items.assert_not_called()
    zot.upload_attachments.assert_not_called()


def test_attach_markdown_multiple_names_is_conflict_without_read_or_create(library, zot):
    zot.children.return_value = [attachment("ATTACH01"), attachment("ATTACH02")]
    zot.everything.side_effect = lambda items: items

    result = library.attach_markdown("ABCD1234", "# New\n")

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ATTACHMENT_NAME_AMBIGUOUS"
    zot.file.assert_not_called()
    zot.create_items.assert_not_called()


def test_attach_markdown_unreadable_existing_attachment_does_not_duplicate(library, zot):
    zot.children.return_value = [attachment()]
    zot.everything.side_effect = lambda items: items
    zot.file.side_effect = ConnectionError("connection dropped")

    result = library.attach_markdown("ABCD1234", "# New\n")

    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["key"] == "ATTACH01"
    assert result["error"]["code"] == "ATTACHMENT_CONTENT_UNAVAILABLE"
    zot.create_items.assert_not_called()
    zot.upload_attachments.assert_not_called()


def test_attach_markdown_upload_unknown_keeps_created_key(library, zot):
    zot.children.return_value = []
    zot.everything.side_effect = lambda items: items
    zot.upload_attachments.side_effect = ConnectionError("dropped")

    result = library.attach_markdown("ABCD1234", "# New\n")

    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["key"] == "NEW00001"
    assert result["results"][0]["stage"] == "upload"
    assert result["error"]["code"] == "UNKNOWN_WRITE_RESULT"
