"""Test validation functionality."""

import json
import re
import tempfile
from pathlib import Path

import pytest

from lightnow_cli.validation import (
    ValidationError,
    validate_artifacts,
    validate_docs_file,
    validate_openapi_file,
    validate_server_json,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_server_json():
    """Valid server.json content."""
    return {
        "name": "io.lightnow/test-server",
        "version": "1.0.0",
        "description": "A test MCP server for validation",
        "author": {"name": "Test Author", "email": "test@example.com"},
        "license": "MIT",
        "transport": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "test_server"],
        },
    }


@pytest.fixture
def valid_openapi_spec():
    """Valid OpenAPI specification."""
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Test Server API",
            "version": "1.0.0",
            "description": "API for test server",
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {"200": {"description": "Server is healthy"}},
                }
            }
        },
    }


def test_validate_valid_server_json(temp_dir, valid_server_json):
    """Test validation of valid server.json."""
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        json.dump(valid_server_json, f)

    result = validate_server_json(server_file)
    assert result == valid_server_json


def test_validate_server_json_missing_file(temp_dir):
    """Test validation of missing server.json file."""
    server_file = temp_dir / "nonexistent.json"

    with pytest.raises(ValidationError, match="server.json file does not exist"):
        validate_server_json(server_file)


def test_validate_server_json_invalid_json(temp_dir):
    """Test validation of invalid JSON in server.json."""
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        f.write("invalid json content")

    with pytest.raises(ValidationError, match="server.json file contains invalid JSON"):
        validate_server_json(server_file)


def test_validate_server_json_missing_required_fields(temp_dir):
    """Test validation of server.json with missing required fields."""
    server_file = temp_dir / "server.json"
    invalid_data = {"name": "test"}  # Missing version and description

    with open(server_file, "w") as f:
        json.dump(invalid_data, f)

    with pytest.raises(ValidationError, match="server.json validation failed"):
        validate_server_json(server_file)


def test_validate_server_json_invalid_name(temp_dir):
    """Test validation of server.json with invalid name."""
    server_file = temp_dir / "server.json"
    invalid_data = {
        "name": "Invalid Name!",  # Contains invalid characters
        "version": "1.0.0",
        "description": "Test description",
    }

    with open(server_file, "w") as f:
        json.dump(invalid_data, f)

    with pytest.raises(ValidationError):
        validate_server_json(server_file)


def test_validate_server_json_invalid_version(temp_dir):
    """Test validation of server.json with invalid version."""
    server_file = temp_dir / "server.json"
    invalid_data = {
        "name": "io.lightnow/test-server",
        "version": "not-a-version",  # Invalid version format
        "description": "Test description",
    }

    with open(server_file, "w") as f:
        json.dump(invalid_data, f)

    with pytest.raises(ValidationError):
        validate_server_json(server_file)


def test_validate_valid_docs_file(temp_dir):
    """Test validation of valid docs.md."""
    docs_file = temp_dir / "docs.md"
    content = """# Test Server

This is a test MCP server.

## Installation

Run with Python."""

    with open(docs_file, "w") as f:
        f.write(content)

    result = validate_docs_file(docs_file)
    assert result == content


def test_validate_docs_file_missing(temp_dir):
    """Test validation of missing docs.md."""
    docs_file = temp_dir / "nonexistent.md"

    with pytest.raises(ValidationError, match="Documentation file does not exist"):
        validate_docs_file(docs_file)


def test_validate_docs_file_empty(temp_dir):
    """Test validation of empty docs.md."""
    docs_file = temp_dir / "docs.md"
    with open(docs_file, "w") as f:
        f.write("")

    with pytest.raises(ValidationError, match="Documentation file is empty"):
        validate_docs_file(docs_file)


def test_validate_docs_file_too_short(temp_dir):
    """Test validation of too short docs.md."""
    docs_file = temp_dir / "docs.md"
    with open(docs_file, "w") as f:
        f.write("# Title\n")  # Only 2 lines after split

    with pytest.raises(
        ValidationError,
        match=re.escape("Documentation seems too short (less than 3 lines)"),
    ):
        validate_docs_file(docs_file)


def test_validate_valid_openapi_file(temp_dir, valid_openapi_spec):
    """Test validation of valid OpenAPI file."""
    spec_file = temp_dir / "openapi.yaml"

    with open(spec_file, "w") as f:
        json.dump(valid_openapi_spec, f)

    result = validate_openapi_file(spec_file)
    assert result == valid_openapi_spec


def test_validate_openapi_file_missing(temp_dir):
    """Test validation of missing OpenAPI file."""
    spec_file = temp_dir / "nonexistent.yaml"

    with pytest.raises(ValidationError, match="OpenAPI file does not exist"):
        validate_openapi_file(spec_file)


def test_validate_openapi_file_invalid_yaml(temp_dir):
    """Test validation of invalid YAML in OpenAPI file."""
    spec_file = temp_dir / "openapi.yaml"
    with open(spec_file, "w") as f:
        f.write("invalid: yaml: content: [")

    with pytest.raises(ValidationError, match="OpenAPI file contains invalid"):
        validate_openapi_file(spec_file)


def test_validate_openapi_file_missing_required_fields(temp_dir):
    """Test validation of OpenAPI file with missing required fields."""
    spec_file = temp_dir / "openapi.yaml"
    invalid_spec = {"openapi": "3.0.0"}  # Missing info and paths

    with open(spec_file, "w") as f:
        json.dump(invalid_spec, f)

    with pytest.raises(ValidationError, match="OpenAPI file missing required field"):
        validate_openapi_file(spec_file)


def test_validate_openapi_file_unsupported_version(temp_dir):
    """Test validation of OpenAPI file with unsupported version."""
    spec_file = temp_dir / "openapi.yaml"
    invalid_spec = {
        "openapi": "2.0",  # Unsupported version
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {},
    }

    with open(spec_file, "w") as f:
        json.dump(invalid_spec, f)

    with pytest.raises(ValidationError, match="Unsupported OpenAPI version"):
        validate_openapi_file(spec_file)


def test_validate_artifacts_all_valid(temp_dir, valid_server_json, valid_openapi_spec):
    """Test validation of all valid artifacts."""
    # Create files
    server_file = temp_dir / "server.json"
    docs_file = temp_dir / "docs.md"
    spec_file = temp_dir / "openapi.json"

    with open(server_file, "w") as f:
        json.dump(valid_server_json, f)

    with open(docs_file, "w") as f:
        f.write("# Test Server\n\nThis is documentation.\n\nMore content here.")

    with open(spec_file, "w") as f:
        json.dump(valid_openapi_spec, f)

    # Validate
    results = validate_artifacts(
        server_file=server_file, docs_file=docs_file, spec_file=spec_file
    )

    assert results["valid"] is True
    assert len(results["errors"]) == 0
    assert "server" in results["validated"]
    assert "docs" in results["validated"]
    assert "spec" in results["validated"]


def test_validate_artifacts_with_errors(temp_dir):
    """Test validation with errors."""
    # Create invalid server.json
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        json.dump({"name": "test"}, f)  # Missing required fields

    results = validate_artifacts(server_file=server_file)

    assert results["valid"] is False
    assert len(results["errors"]) > 0
    assert "server.json" in results["errors"][0]


def test_validate_artifacts_cross_validation_warning(
    temp_dir, valid_server_json, valid_openapi_spec
):
    """Test cross-validation warning."""
    # Create files with mismatched names
    server_file = temp_dir / "server.json"
    spec_file = temp_dir / "openapi.json"

    server_data = valid_server_json.copy()
    server_data["name"] = "io.lightnow/different-server"

    spec_data = valid_openapi_spec.copy()
    spec_data["info"]["title"] = "Completely Different API"

    with open(server_file, "w") as f:
        json.dump(server_data, f)

    with open(spec_file, "w") as f:
        json.dump(spec_data, f)

    results = validate_artifacts(server_file=server_file, spec_file=spec_file)

    assert results["valid"] is True
    assert len(results["warnings"]) > 0
