# mock-ticketing-system

Two small services used by the `triage-agent` demo:

- **`ticketing-system/`** — a lightweight ServiceNow Table API simulator (FastAPI +
  SQLite) for incident management, with an HTML dashboard at `/`.
- **`ticketing-mcp-server/`** — an MCP server (FastMCP, `streamable-http` at `/mcp`) that
  wraps the ticketing-system REST API as MCP tools: `create_incident`, `list_incidents`,
  `get_incident`, `update_incident`, `add_work_note`.

Both are independent Python apps, each with its own `Dockerfile` and `requirements.txt`,
built and published as two separate images.

## Build

```bash
podman build -t quay.io/sovereign-selfheal/ticketing-system:<tag> ticketing-system/
podman build -t quay.io/sovereign-selfheal/ticketing-mcp-server:<tag> ticketing-mcp-server/
```

CI (`.github/workflows/build.yml`) runs on pull requests and pushes to `main` to verify
that both images build (no push). Quay builds/pushes tagged release images separately.

## Consumer

Kubernetes manifests and the pinned image digests live in the `gitops` repo:
`components/ticketing-system/` and `components/ticketing-mcp-server/`. This repo only
owns the source and the build.
