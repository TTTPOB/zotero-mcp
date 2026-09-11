"""Start the local MCP server or request persistent local write permission."""

import argparse
import sys

from zotero_mcp import __version__


def authorize() -> None:
    from pyzotero import Zotero

    from zotero_mcp.config import LocalAuth, Settings, auth_path, save_auth

    settings = Settings.from_env()
    # Do not load a stale server ID here: this command explicitly authorizes the
    # currently open Zotero profile. Never perform an implicit authorization retry.
    zot = Zotero(settings.library_id, settings.library_type, local=True)
    try:
        result = zot.authorize_local("Zotero MCP (local)")
        if result.get("remember") is not True:
            raise ValueError(
                "One-time permission was not saved. Run authorize again and choose Always Allow."
            )
        save_auth(LocalAuth(server_id=zot.server_id, key=result["key"]))
        print(
            f"Local write permission saved to {auth_path()}. Restart the MCP server if it is running."
        )
    finally:
        zot.client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zotero 10 local MCP server")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "authorize"])
    args = parser.parse_args(argv)
    try:
        if args.command == "authorize":
            authorize()
        else:
            from zotero_mcp.server import build_server

            build_server().run(transport="stdio")
        return 0
    except Exception as exc:
        print(f"Zotero MCP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
