"""Tests for GraphQLIntrospectTool."""

from __future__ import annotations

import httpx
import pytest
import respx

from molly.tools.graphql_introspect import (
    INTROSPECTION_QUERY,
    GraphQLIntrospectTool,
    ToolError,
)


class TestGraphQLIntrospectTool:
    def test_capability_name_is_graphql_introspect(self):
        tool = GraphQLIntrospectTool()
        assert tool.capability_name == "graphql_introspect"

    def test_name(self):
        tool = GraphQLIntrospectTool()
        assert tool.name() == "GraphQL Introspect"

    def test_description(self):
        tool = GraphQLIntrospectTool()
        assert "Introspects" in tool.description()
        assert "GraphQL endpoint" in tool.description()

    def test_parameters_schema_returns_valid_schema(self):
        tool = GraphQLIntrospectTool()
        schema = tool.parameters_schema()
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "headers" in schema["properties"]
        assert "url" in schema["required"]

    def test_introspection_query_is_valid_graphql(self):
        assert "__schema" in INTROSPECTION_QUERY
        assert "query IntrospectionQuery" in INTROSPECTION_QUERY


@pytest.mark.asyncio
@respx.mock
async def test_execute_success():
    """execute returns the full schema data on success."""
    schema_data = {
        "__schema": {
            "queryType": {"name": "Query"},
            "mutationType": {"name": "Mutation"},
            "subscriptionType": None,
            "types": [
                {
                    "kind": "OBJECT",
                    "name": "Query",
                    "description": None,
                    "fields": [],
                    "inputFields": None,
                    "interfaces": [],
                    "enumValues": None,
                    "possibleTypes": None,
                }
            ],
            "directives": [],
        }
    }
    respx.post("https://example.com/graphql").mock(
        return_value=httpx.Response(200, json={"data": schema_data})
    )
    async with httpx.AsyncClient() as client:
        tool = GraphQLIntrospectTool()
        result = await tool.execute(url="https://example.com/graphql", client=client)

    assert result["__schema"]["queryType"]["name"] == "Query"
    assert result["__schema"]["mutationType"]["name"] == "Mutation"


@pytest.mark.asyncio
@respx.mock
async def test_execute_with_headers():
    """execute sends custom headers and returns schema data."""
    schema_data = {
        "__schema": {
            "queryType": {"name": "Query"},
            "mutationType": None,
            "subscriptionType": None,
            "types": [],
            "directives": [],
        }
    }
    route = respx.post("https://example.com/graphql").mock(
        return_value=httpx.Response(200, json={"data": schema_data})
    )
    async with httpx.AsyncClient() as client:
        tool = GraphQLIntrospectTool()
        result = await tool.execute(
            url="https://example.com/graphql",
            client=client,
            headers={"Authorization": "Bearer test-token"},
        )

    assert result["__schema"]["queryType"]["name"] == "Query"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
@respx.mock
async def test_execute_graphql_errors_raises_tool_error():
    """execute raises ToolError when GraphQL returns errors."""
    respx.post("https://example.com/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": None, "errors": [{"message": "Not authenticated"}]}
        )
    )
    async with httpx.AsyncClient() as client:
        tool = GraphQLIntrospectTool()
        with pytest.raises(ToolError, match="GraphQL error"):
            await tool.execute(url="https://example.com/graphql", client=client)


@pytest.mark.asyncio
@respx.mock
async def test_execute_http_error_raises_tool_error():
    """execute raises ToolError on HTTP 500."""
    respx.post("https://example.com/graphql").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )
    async with httpx.AsyncClient() as client:
        tool = GraphQLIntrospectTool()
        with pytest.raises(ToolError, match="Failed to connect"):
            await tool.execute(url="https://example.com/graphql", client=client)


@pytest.mark.asyncio
async def test_execute_missing_client_raises_tool_error():
    """execute raises ToolError when no client is provided."""
    tool = GraphQLIntrospectTool()
    with pytest.raises(ToolError, match="No HTTP client provided"):
        await tool.execute(url="https://example.com/graphql")
