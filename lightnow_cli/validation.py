"""Local validation of MCP server artifacts."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, cast

import jsonschema
import yaml
from rich.console import Console

console = Console()


# Local schema for quick client-side MCP server metadata validation.
MCP_SERVER_SCHEMA = {
    "type": "object",
    "required": ["name", "version", "description"],
    "properties": {
        "name": {
            "type": "string",
            "pattern": "^[A-Za-z0-9.-]+/[A-Za-z0-9._-]+$",
            "minLength": 3,
            "maxLength": 200,
        },
        "version": {
            "type": "string",
            "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+(-[a-zA-Z0-9-]+)?$",
        },
        "description": {"type": "string", "minLength": 10, "maxLength": 500},
        "author": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string", "format": "email"},
            },
        },
        "license": {"type": "string"},
        "homepage": {"type": "string", "format": "uri"},
        "repository": {
            "oneOf": [
                {"type": "string", "format": "uri"},
                {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "format": "uri"},
                        "source": {"type": "string"},
                        "id": {"type": "string"},
                    },
                    "required": ["url"],
                    "additionalProperties": True,
                },
            ]
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "transport": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["stdio", "sse", "websocket", "http"],
                },
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object"},
            },
        },
    },
}


class ValidationError(Exception):
    """Validation error."""

    pass


def validate_json_file(
    file_path: Path, schema: Dict[str, Any], name: str
) -> Dict[str, Any]:
    """Validate a JSON file against a schema."""
    if not file_path.exists():
        raise ValidationError(f"{name} file does not exist: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValidationError(f"{name} file contains invalid JSON: {e}")
    except Exception as e:
        raise ValidationError(f"Failed to read {name} file: {e}")

    try:
        jsonschema.validate(data, schema)
        return cast(Dict[str, Any], data)
    except jsonschema.ValidationError as e:
        raise ValidationError(f"{name} validation failed: {e.message}")


def validate_server_json(file_path: Path) -> Dict[str, Any]:
    """Validate server.json file."""
    return validate_json_file(file_path, MCP_SERVER_SCHEMA, "server.json")


def validate_docs_file(file_path: Path) -> str:
    """Validate docs.md file."""
    if not file_path.exists():
        raise ValidationError(f"Documentation file does not exist: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise ValidationError(f"Failed to read documentation file: {e}")

    if not content.strip():
        raise ValidationError("Documentation file is empty")

    lines = content.split("\n")
    if len(lines) < 3:
        raise ValidationError("Documentation seems too short (less than 3 lines)")

    return content


def validate_openapi_file(file_path: Path) -> Dict[str, Any]:
    """Validate OpenAPI specification file."""
    if not file_path.exists():
        raise ValidationError(f"OpenAPI file does not exist: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            if file_path.suffix.lower() in [".yaml", ".yml"]:
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValidationError(f"OpenAPI file contains invalid YAML/JSON: {e}")
    except Exception as e:
        raise ValidationError(f"Failed to read OpenAPI file: {e}")

    # Basic OpenAPI validation
    if not isinstance(data, dict):
        raise ValidationError("OpenAPI file must contain a JSON object")

    required_fields = ["openapi", "info", "paths"]
    for field in required_fields:
        if field not in data:
            raise ValidationError(f"OpenAPI file missing required field: {field}")

    # Check OpenAPI version
    openapi_version = data.get("openapi", "")
    if not openapi_version.startswith("3."):
        raise ValidationError(
            f"Unsupported OpenAPI version: {openapi_version}. Only 3.x is supported."
        )

    # Validate info section
    info = data.get("info", {})
    if not isinstance(info, dict):
        raise ValidationError("OpenAPI 'info' field must be an object")

    required_info_fields = ["title", "version"]
    for field in required_info_fields:
        if field not in info:
            raise ValidationError(
                f"OpenAPI info section missing required field: {field}"
            )

    return cast(Dict[str, Any], data)


def validate_artifacts(
    server_file: Optional[Path] = None,
    docs_file: Optional[Path] = None,
    spec_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate all provided artifacts."""
    results: Dict[str, Any] = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "validated": {},
    }

    # Validate server.json
    if server_file:
        try:
            server_data = validate_server_json(server_file)
            results["validated"]["server"] = server_data
            console.print("[green]✓[/green] server.json is valid")
        except ValidationError as e:
            results["valid"] = False
            results["errors"].append(f"server.json: {e}")
            console.print(f"[red]✗[/red] server.json: {e}")

    # Validate docs.md
    if docs_file:
        try:
            docs_content = validate_docs_file(docs_file)
            results["validated"]["docs"] = docs_content
            console.print("[green]✓[/green] docs.md is valid")
        except ValidationError as e:
            results["valid"] = False
            results["errors"].append(f"docs.md: {e}")
            console.print(f"[red]✗[/red] docs.md: {e}")

    # Validate OpenAPI spec
    if spec_file:
        try:
            spec_data = validate_openapi_file(spec_file)
            results["validated"]["spec"] = spec_data
            console.print("[green]✓[/green] OpenAPI spec is valid")
        except ValidationError as e:
            results["valid"] = False
            results["errors"].append(f"OpenAPI spec: {e}")
            console.print(f"[red]✗[/red] OpenAPI spec: {e}")

    # Cross-validation checks
    if (
        server_file
        and spec_file
        and "server" in results["validated"]
        and "spec" in results["validated"]
    ):
        server_data = results["validated"]["server"]
        spec_data = results["validated"]["spec"]

        # Check if server name matches spec title (warning only)
        server_name = server_data.get("name", "")
        spec_title = spec_data.get("info", {}).get("title", "")

        if server_name and spec_title and server_name.lower() not in spec_title.lower():
            warning = f"Server name '{server_name}' doesn't match OpenAPI title '{spec_title}'"
            results["warnings"].append(warning)
            console.print(f"[yellow]⚠[/yellow] {warning}")

    return results
