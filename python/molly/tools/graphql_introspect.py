from molly.tools.base import ToolBase, ToolError

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind
      name
      description
      fields(includeDeprecated: true) {
        name
        description
        args {
          name
          description
          type {
            kind
            name
            type { kind name }
          }
          defaultValue
        }
        type {
          kind
          name
          type { kind name }
        }
        isDeprecated
        deprecationReason
      }
      inputFields {
        name
        description
        type {
          kind
          name
          type { kind name }
        }
        defaultValue
      }
      interfaces {
        kind
        name
        type { kind name }
      }
      enumValues(includeDeprecated: true) {
        name
        description
        isDeprecated
        deprecationReason
      }
      possibleTypes {
        kind
        name
        type { kind name }
      }
    }
    directives {
      name
      description
      locations
      args {
        name
        description
        type {
          kind
          name
          type { kind name }
        }
        defaultValue
      }
    }
  }
}
"""


class GraphQLIntrospectTool(ToolBase):
    capability_name = "graphql_introspect"

    def name(self) -> str:
        return "GraphQL Introspect"

    def description(self) -> str:
        return (
            "Introspects a GraphQL endpoint to enumerate all types, queries, "
            "mutations, subscriptions, and directives."
        )

    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the GraphQL endpoint.",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers to include in the request.",
                },
            },
            "required": ["url"],
        }

    async def execute(
        self, *, url: str, client=None, headers: dict | None = None, **kwargs
    ) -> dict:
        if client is None:
            raise ToolError("No HTTP client provided")

        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)

        try:
            response = await client.post(
                url,
                json={"query": INTROSPECTION_QUERY},
                headers=request_headers,
            )
            response.raise_for_status()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Failed to connect to GraphQL endpoint: {exc}") from exc

        result = response.json()
        if "errors" in result:
            raise ToolError(f"GraphQL error: {result['errors']}")

        return result["data"]
