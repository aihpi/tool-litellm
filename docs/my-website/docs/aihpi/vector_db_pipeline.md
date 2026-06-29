---
id: vector_db_pipeline
title: Vector DB Pipeline (Qdrant)
sidebar_label: Vector DB Pipeline
---

# Vector DB Pipeline (Qdrant)

This doc describes the Qdrant-only ingestion pipeline endpoints. These endpoints
accept text, embed it using the vector store's configured embedding model, and
store the vectors in the Qdrant collection tied to the vector store.

Notes:
- Qdrant only.
- The vector store must define `litellm_embedding_model`.
- Users can provide their own IDs or let the API generate them.
- Permissions are enforced via vector store "write" access.
- If the API key has a `default_vector_store_id`, you can omit the ID in the endpoint path.
- Payload metadata is stored alongside the vector in Qdrant.

## Create / Upsert points

Endpoint:
`POST /v1/vector_stores/{vector_store_id}/points`

Request:
```json
{
  "items": [
    { "text": "First chunk", "metadata": { "source": "doc-a" } },
    { "id": "point-2", "text": "Second chunk", "metadata": { "source": "doc-b" } }
  ]
}
```

Response (example):
```json
{
  "status": "success",
  "vector_store_id": "vs-123",
  "point_ids": ["auto-id-1", "point-2"],
  "result": { "result": "ok" }
}
```

Python example:
```python
import requests

base_url = "http://localhost:4000"
api_key = "sk-..."
vector_store_id = "vs-123"

payload = {
    "items": [
        {"text": "First chunk", "metadata": {"source": "doc-a"}},
        {"id": "point-2", "text": "Second chunk", "metadata": {"source": "doc-b"}},
    ]
}

resp = requests.post(
    f"{base_url}/v1/vector_stores/{vector_store_id}/points",
    headers={"Authorization": f"Bearer {api_key}"},
    json=payload,
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

### Using the key default vector store

If the API key metadata includes `default_vector_store_id`, you can call:
`POST /v1/vector_store/points`

Request body is the same:
```json
{
  "items": [
    { "text": "First chunk", "metadata": { "source": "doc-a" } }
  ]
}
```

If no default is set, this endpoint returns `400` and you must call the
`/v1/vector_stores/{vector_store_id}/points` route.

## Update a point

Endpoint:
`PATCH /v1/vector_stores/{vector_store_id}/points/{point_id}`

Request:
```json
{
  "text": "Updated chunk",
  "metadata": { "tag": "latest" }
}
```

Response (example):
```json
{
  "status": "success",
  "vector_store_id": "vs-123",
  "point_id": "point-1",
  "result": { "result": "ok" }
}
```

Python example:
```python
import requests

base_url = "http://localhost:4000"
api_key = "sk-..."
vector_store_id = "vs-123"
point_id = "point-1"

payload = {"text": "Updated chunk", "metadata": {"tag": "latest"}}

resp = requests.patch(
    f"{base_url}/v1/vector_stores/{vector_store_id}/points/{point_id}",
    headers={"Authorization": f"Bearer {api_key}"},
    json=payload,
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

If the API key has `default_vector_store_id`, you can call:
`PATCH /v1/vector_store/points/{point_id}`

## Delete a point

Endpoint:
`DELETE /v1/vector_stores/{vector_store_id}/points/{point_id}`

Response (example):
```json
{
  "status": "success",
  "vector_store_id": "vs-123",
  "point_id": "point-1",
  "result": { "result": "ok" }
}
```

Python example:
```python
import requests

base_url = "http://localhost:4000"
api_key = "sk-..."
vector_store_id = "vs-123"
point_id = "point-1"

resp = requests.delete(
    f"{base_url}/v1/vector_stores/{vector_store_id}/points/{point_id}",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

If the API key has `default_vector_store_id`, you can call:
`DELETE /v1/vector_store/points/{point_id}`

## Field mapping

The text is stored in Qdrant payload under the `qdrant_text_field` configured for
the vector store. If not set, the field defaults to `"text"`.

## Testing

You can call these endpoints via the Swagger UI (`/docs`) or via curl/Postman.
