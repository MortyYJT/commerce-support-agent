# MercuryDesk

An e-commerce customer support agent built with FastAPI, LangGraph, MySQL, and Milvus. It combines cited knowledge answers, order and logistics tools, human-confirmed ticket and refund flows, and an offline topic-classification pipeline.

## Capabilities

- Stream customer conversations and extract structured requests.
- Retrieve policy answers from a hybrid search pipeline with reranking and citations.
- Route order, logistics, after-sales, and refund requests through built-in tools and MCP servers.
- Keep conversation context with message windows and background summaries.
- Review low-confidence conversations and feed approved knowledge back into the knowledge base.
- Track model usage and retrieval quality; train and serve an optional topic classifier.

## Stack

Python 3.12+, FastAPI, LangGraph, SQLAlchemy, MySQL, Milvus, and Langfuse.

## Run locally

```bash
cp .env.example .env
# Set CHAT_*, EMBED_*, and RERANK_* in .env
docker compose up -d
make seed
make dev
```

Open <http://localhost:8000>. See [DEPLOY.md](DEPLOY.md) for service setup and configuration details. Use `make help` to list development and evaluation commands.

## Project layout

| Path | Purpose |
| --- | --- |
| `app/api/` | HTTP APIs and page routes |
| `app/graph/` | Conversation graph, state, and routing |
| `app/core/` | Retrieval, prompts, model clients, memory, and observability |
| `app/kb/` | Knowledge ingestion, chunking, embeddings, and review |
| `app/tools/` | Built-in tools, MCP client, and execution engine |
| `app/db/` | SQLAlchemy models and repositories |
| `mcp_servers/` | Local logistics and after-sales MCP servers |
| `sql/` | Database schema and seed scripts |
| `scripts/` | Evaluation, data preparation, and model workflows |

## Configuration

Chat, embedding, and reranking providers are configured independently through `CHAT_*`, `EMBED_*`, and `RERANK_*` environment variables. Optional services such as Milvus and Langfuse can be enabled through the included Compose files.
