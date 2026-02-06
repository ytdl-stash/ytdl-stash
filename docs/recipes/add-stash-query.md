# Recipe: Add a New Stash GraphQL Query/Mutation

How to add a new method to the `StashClient` class for communicating with Stash.

---

## Steps

### 1. Find the GraphQL schema

Stash exposes its GraphQL schema at `{stash_url}/graphql` (use the GraphQL playground in a browser to explore). Alternatively, check the Stash source code:
- [Stash GraphQL schema](https://github.com/stashapp/stash/tree/develop/graphql/schema)

### 2. Write the query/mutation string

```python
FIND_TAGS_QUERY = """
query FindTags($filter: FindFilterType!, $tag_filter: TagFilterType!) {
    findTags(filter: $filter, tag_filter: $tag_filter) {
        tags {
            id
            name
        }
    }
}
"""
```

**Tips:**
- Only request the fields you need (minimize response size).
- Use variables (`$filter`, `$tag_filter`) for parameterized queries.
- Test the query in Stash's GraphQL playground first.

### 3. Add the method to `StashClient` in `app/stash_client.py`

```python
async def find_tag(self, name: str) -> str | None:
    """Find a tag by exact name match. Returns tag ID or None."""
    variables = {
        "filter": {"per_page": 1},
        "tag_filter": {
            "name": {
                "value": name,
                "modifier": "EQUALS",
            }
        }
    }
    data = await self._query(FIND_TAGS_QUERY, variables)
    tags = data["findTags"]["tags"]
    return tags[0]["id"] if tags else None
```

### 4. Follow the existing patterns

- All methods are `async`.
- Use `self._query()` for the HTTP call -- it handles headers, error checking, and JSON parsing.
- Return simple types (`str`, `dict`, `None`, `bool`), not raw GraphQL responses.
- Use the "find or create" pattern for entities that may need to be created.

### 5. Common filter modifiers

| Modifier | Meaning |
|----------|---------|
| `EQUALS` | Exact match |
| `NOT_EQUALS` | Inverse exact match |
| `INCLUDES` | Substring match (contains) |
| `EXCLUDES` | Does not contain |
| `IS_NULL` | Field is null/empty |
| `NOT_NULL` | Field has a value |
| `MATCHES_REGEX` | Regex match |
| `GREATER_THAN` | Numeric/date comparison |
| `LESS_THAN` | Numeric/date comparison |

### 6. Common mutation patterns

**Create:**
```graphql
mutation TagCreate($input: TagCreateInput!) {
    tagCreate(input: $input) { id }
}
```

**Update:**
```graphql
mutation TagUpdate($input: TagUpdateInput!) {
    tagUpdate(input: $input) { id }
}
```

**Delete:**
```graphql
mutation TagDestroy($input: TagDestroyInput!) {
    tagDestroy(input: $input)
}
```

---

## Testing

Test your new query against a running Stash instance:

```python
import asyncio
from app.stash_client import StashClient

async def test():
    client = StashClient("http://localhost:9999", "your-api-key")
    result = await client.find_tag("some-tag")
    print(result)

asyncio.run(test())
```

---

## Update Documentation

- Add the new method to the method list in `docs/patterns/stash-graphql.md`.
- If this introduces a new integration flow, update `docs/data-flow.md`.
