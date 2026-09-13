"""Serve the local MCP tools over stdio or Streamable HTTP, or request write permission."""

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


def serve(transport: str, host: str | None, port: int | None) -> None:
    from zotero_mcp.config import HttpSettings
    from zotero_mcp.server import build_server

    if transport != "http":
        if host is not None or port is not None:
            raise ValueError("--host and --port apply to --transport http only.")
        build_server().run(transport="stdio")
        return
    settings = HttpSettings.from_env(host=host, port=port)
    server = build_server(host=settings.host, port=settings.port)
    # No authentication: keep the default loopback binding unless the user overrides it.
    print(
        f"Zotero MCP: Streamable HTTP on http://{settings.host}:{settings.port}"
        f"{server.settings.streamable_http_path} without authentication",
        file=sys.stderr,
    )
    server.run(transport="streamable-http")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotero-mcp", description="Zotero 10 local MCP server")
    parser.add_argument("--version", action="version", version=__version__)
    parser.set_defaults(transport="stdio", host=None, port=None)
    commands = parser.add_subparsers(dest="command")
    serve_command = commands.add_parser("serve", help="Run the MCP server (stdio by default).")
    serve_command.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for clients that launch a process; http for Streamable HTTP.",
    )
    serve_command.add_argument(
        "--host", default=None, help="HTTP bind address (default 127.0.0.1)."
    )
    serve_command.add_argument("--port", type=int, default=None, help="HTTP port (default 8000).")
    commands.add_parser("authorize", help="Request persistent local write permission.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "authorize":
            authorize()
        else:
            serve(args.transport, args.host, args.port)
        return 0
    except Exception as exc:
        print(f"Zotero MCP: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
