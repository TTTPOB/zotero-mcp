#!/usr/bin/env python

"""
Setup helper for zotero-mcp.

This script provides utilities to automatically configure zotero-mcp
by finding the installed executable and updating Claude Desktop's config.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def find_executable():
    """Find the full path to the zotero-mcp executable."""
    # Try to find the executable in the PATH
    exe_name = "zotero-mcp"
    if sys.platform == "win32":
        exe_name += ".exe"

    exe_path = shutil.which(exe_name)
    if exe_path:
        print(f"Found zotero-mcp in PATH at: {exe_path}")
        return exe_path

    # If not found in PATH, try to find it in common installation directories
    potential_paths = []

    # User site-packages
    import site

    for site_path in site.getsitepackages():
        potential_paths.append(Path(site_path) / "bin" / exe_name)

    # User's home directory
    potential_paths.append(Path.home() / ".local" / "bin" / exe_name)

    # Virtual environment
    if "VIRTUAL_ENV" in os.environ:
        potential_paths.append(Path(os.environ["VIRTUAL_ENV"]) / "bin" / exe_name)

    # Additional common locations
    if sys.platform == "darwin":  # macOS
        potential_paths.append(Path("/usr/local/bin") / exe_name)
        potential_paths.append(Path("/opt/homebrew/bin") / exe_name)

    for path in potential_paths:
        if path.exists() and os.access(path, os.X_OK):
            print(f"Found zotero-mcp at: {path}")
            return str(path)

    # If still not found, search in common directories
    print("Searching for zotero-mcp in common locations...")
    try:
        # On Unix-like systems, try using the 'find' command
        if sys.platform != "win32":
            import subprocess

            result = subprocess.run(
                [
                    "find",
                    os.path.expanduser("~"),
                    "-name",
                    "zotero-mcp",
                    "-type",
                    "f",
                    "-executable",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            paths = result.stdout.strip().split("\n")
            if paths and paths[0]:
                print(f"Found zotero-mcp at {paths[0]}")
                return paths[0]
    except Exception as e:
        print(f"Error searching for zotero-mcp: {e}")

    print("Warning: Could not find zotero-mcp executable.")
    print("Make sure zotero-mcp is installed and in your PATH.")
    return None


def find_claude_config():
    """Find Claude Desktop config file path."""
    config_paths = []

    # macOS
    if sys.platform == "darwin":
        # Try both old and new paths
        config_paths.append(
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
        config_paths.append(
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude Desktop"
            / "claude_desktop_config.json"
        )

    # Windows
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
            config_paths.append(
                Path(appdata) / "Claude Desktop" / "claude_desktop_config.json"
            )

    # Linux
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        config_paths.append(Path(config_home) / "Claude" / "claude_desktop_config.json")
        config_paths.append(
            Path(config_home) / "Claude Desktop" / "claude_desktop_config.json"
        )

    # Check all possible locations
    for path in config_paths:
        if path.exists():
            print(f"Found Claude Desktop config at: {path}")
            return path

    # Return the default path for the platform if not found
    # We'll use the newer "Claude Desktop" path as default
    if sys.platform == "darwin":  # macOS
        default_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude Desktop"
            / "claude_desktop_config.json"
        )
    elif sys.platform == "win32":  # Windows
        appdata = os.environ.get("APPDATA", "")
        default_path = Path(appdata) / "Claude Desktop" / "claude_desktop_config.json"
    else:  # Linux and others
        config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        default_path = (
            Path(config_home) / "Claude Desktop" / "claude_desktop_config.json"
        )

    print(f"Claude Desktop config not found. Using default path: {default_path}")
    return default_path


def update_claude_config(
    config_path,
    zotero_mcp_path,
    local=True,
    api_key=None,
    library_id=None,
    library_type="user",
):
    """Update Claude Desktop config to add zotero-mcp."""
    # Create directory if it doesn't exist
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new one
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            print(f"Loaded existing config from: {config_path}")
        except json.JSONDecodeError:
            print(
                f"Error: Config file at {config_path} is not valid JSON. Creating new config."
            )
            config = {}
    else:
        print(f"Creating new config file at: {config_path}")
        config = {}

    # Ensure mcpServers key exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Create environment settings based on local vs web API
    env_settings = {"ZOTERO_LOCAL": "true" if local else "false"}

    # Add API key and library settings for web API
    if not local:
        if api_key:
            env_settings["ZOTERO_API_KEY"] = api_key
        if library_id:
            env_settings["ZOTERO_LIBRARY_ID"] = library_id
        if library_type:
            env_settings["ZOTERO_LIBRARY_TYPE"] = library_type

    # Add or update zotero config
    config["mcpServers"]["zotero"] = {"command": zotero_mcp_path, "env": env_settings}

    # Write updated config
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\nSuccessfully wrote config to: {config_path}")
    except Exception as e:
        print(f"Error writing config file: {str(e)}")
        return False

    return config_path


def _write_standalone_config(
    local: bool,
    api_key: str,
    library_id: str,
    library_type: str,
    no_claude: bool = False,
) -> Path:
    """Write a central config file and provide client env."""
    cfg_dir = Path.home() / ".config" / "zotero-mcp"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"

    # Load or initialize
    full = {}
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                full = json.load(f)
        except Exception:
            full = {}

    # Provide a helper env section for web-based clients
    client_env = {"ZOTERO_LOCAL": "true" if local else "false"}
    # Persist global guard to disable Claude detection/output if requested
    if no_claude:
        client_env["ZOTERO_NO_CLAUDE"] = "true"
    if not local:
        if api_key:
            client_env["ZOTERO_API_KEY"] = api_key
        if library_id:
            client_env["ZOTERO_LIBRARY_ID"] = library_id
        if library_type:
            client_env["ZOTERO_LIBRARY_TYPE"] = library_type

    full["client_env"] = client_env

    with open(cfg_path, "w") as f:
        json.dump(full, f, indent=2)

    return cfg_path


def main(cli_args=None):
    """Main function to run the setup helper."""
    parser = argparse.ArgumentParser(
        description="Configure zotero-mcp for Claude Desktop"
    )
    parser.add_argument(
        "--no-local",
        action="store_true",
        help="Configure for Zotero Web API instead of local API",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Don't setup Claude Desktop config: instead store settings in config file.",
    )
    parser.add_argument(
        "--api-key", help="Zotero API key (only needed with --no-local)"
    )
    parser.add_argument(
        "--library-id", help="Zotero library ID (only needed with --no-local)"
    )
    parser.add_argument(
        "--library-type",
        choices=["user", "group"],
        default="user",
        help="Zotero library type (only needed with --no-local)",
    )
    parser.add_argument("--config-path", help="Path to Claude Desktop config file")

    # If this is being called from CLI with existing args
    if cli_args is not None and hasattr(cli_args, "no_local"):
        args = cli_args
        print("Using arguments passed from command line")
    else:
        # Otherwise parse from command line
        args = parser.parse_args()
        print("Parsed arguments from command line")

    # Find zotero-mcp executable
    exe_path = find_executable()
    if not exe_path:
        print("Error: Could not find zotero-mcp executable.")
        return 1
    print(f"Using zotero-mcp at: {exe_path}")

    # Find Claude Desktop config unless --no-claude
    config_path = None
    if not args.no_claude:
        config_path = args.config_path
        if not config_path:
            config_path = find_claude_config()
        else:
            print(f"Using specified config path: {config_path}")
            config_path = Path(config_path)
        if not config_path:
            print("Error: Could not determine Claude Desktop config path.")
            return 1

    # Update config
    use_local = not args.no_local
    api_key = args.api_key
    library_id = args.library_id
    library_type = args.library_type

    print("\nSetup with the following settings:")
    print(f"  Local API: {use_local}")
    if not use_local:
        print(f"  API Key: {api_key or 'Not provided'}")
        print(f"  Library ID: {library_id or 'Not provided'}")
        print(f"  Library Type: {library_type}")

    # Update configuration based on mode
    try:
        if args.no_claude:
            cfg_path = _write_standalone_config(
                local=use_local,
                api_key=api_key,
                library_id=library_id,
                library_type=library_type,
                no_claude=args.no_claude,
            )
            print("\nSetup complete (standalone/web mode)!")
            print(f"Config saved to: {cfg_path}")
            # Emit one-line client_env for easy copy/paste
            try:
                with open(cfg_path) as f:
                    full = json.load(f)
                env_line = json.dumps(full.get("client_env", {}), separators=(",", ":"))
                print("Client environment (single-line JSON):")
                print(env_line)
            except Exception:
                pass
            return 0
        else:
            updated_config_path = update_claude_config(
                config_path,
                exe_path,
                local=use_local,
                api_key=api_key,
                library_id=library_id,
                library_type=library_type,
            )
            if updated_config_path:
                print("\nSetup complete!")
                print("To use Zotero in Claude Desktop:")
                print("1. Restart Claude Desktop if it's running")
                print("2. In Claude, type: /tools zotero")
                if use_local:
                    print(
                        "\nNote: Make sure Zotero desktop is running and the local API is enabled in preferences."
                    )
                else:
                    missing = []
                    if not api_key:
                        missing.append("API key")
                    if not library_id:
                        missing.append("Library ID")
                    if missing:
                        print(
                            f"\nWarning: The following required settings for Web API were not provided: {', '.join(missing)}"
                        )
                        print(
                            "You may need to set these as environment variables or reconfigure."
                        )
                return 0
            else:
                print("\nSetup failed. See errors above.")
                return 1
    except Exception as e:
        print(f"\nSetup failed with error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
