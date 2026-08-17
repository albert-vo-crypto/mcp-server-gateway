# MCP Server Gateway

A local, book-ready reference stack for running an **MCP Gateway** with three OAuth-protected backends:

| Backend | Namespace | OAuth provider | What it does |
|---------|-----------|----------------|--------------|
| [Artifactory MCP](artifactory/) | `/artifactory/mcp` | Google | JFrog Artifactory tools (repos, artifacts, search) |
| [GitHub MCP](github/) | `/github/mcp` | GitHub | GitHub API tools (repos, issues, PRs, search) |
| [Google Drive MCP](gdrive/) | `/gdrive/mcp` | Google (Drive scopes) | Drive + Docs tools (list, export, create, edit) |

There is also an optional **[JFrog AI Agent (Command Center)](Agents/jfrog-agent/)**, a LangGraph + Streamlit app that talks to the Artifactory MCP server *through* this gateway (per-user OAuth). It includes durable conversation memory, LangGraph checkpoints, optional BigQuery tracking/eval, and an optional context optimizer layer before LLM calls. Set the gateway up first, then see [Agents/jfrog-agent/README.md](Agents/jfrog-agent/README.md).

The gateway sits in front of all three MCP servers. In secure mode, every tool call requires the user to **sign in via OAuth** and approve a **gateway consent screen**. No shared tokens are checked into the repo.

```
Cursor / oauth-client-demo / JFrog agent
        |
        |  http://localhost:8090/{namespace}/mcp
        v
   MCP Gateway (:8090)
        |-- Google OAuth       (artifactory)
        |-- GitHub OAuth       (github)
        |-- Google Drive OAuth (gdrive)
        |
        +-- artifactory-mcp (:8091, internal)
        +-- github-mcp      (:8080, internal)
        +-- gdrive-mcp        (:8092, internal)
```

---

## Prerequisites

Install these before you start:

| Tool | Version | Check |
|------|---------|-------|
| **Docker Desktop** (or Docker Engine + Compose) | recent | `docker compose version` |
| **Node.js** | 20+ (24 recommended for gateway dev) | `node --version` |
| **Python** | 3.10+ | `python3 --version` |
| **OpenSSL** | any | `openssl version` |

Optional but recommended for IDE testing:

- **Cursor** (or any MCP client that supports OAuth)
- A **Google account** (for Artifactory and Google Drive OAuth)
- A **GitHub account** (for GitHub OAuth)
- A **JFrog Cloud free-tier instance** ([jfrog.com/start](https://jfrog.com/start)) if you want live Artifactory data

---

## Step 1: Clone and open the repo

```bash
git clone https://github.com/harish-gaggar/mcp-server-gateway.git
cd mcp-server-gateway
```

Repo layout:

```
mcp-server-gateway/
├── mcp-gateway/          # Gateway + docker compose + Cursor wrappers + monitoring
├── artifactory/          # Python Artifactory MCP server
├── github/               # TypeScript GitHub MCP server
├── gdrive/               # Python Google Drive + Docs MCP server
├── Agents/
│   └── jfrog-agent/      # Optional LangGraph + Streamlit JFrog AI agent
└── README.md             # you are here
```

---

## Step 2: Create `.env` and encryption key

```bash
cd mcp-gateway
cp .env.example .env
```

Generate a token-encryption key and paste it into `.env`:

```bash
openssl rand -base64 32
# paste the output as TOKEN_ENCRYPTION_KEY=...
```

Enable **secure (per-user OAuth) mode** by uncommenting these lines in `.env`:

```bash
OAUTH_CONFIG_FILE=./configs/secure-oauth-config.yml
MCP_CONFIG_FILE=./configs/secure-mcp-config.yml
GATEWAY_BASE_URL=http://localhost:8090
GDRIVE_SECURE_MODE=true
```

Leave `ARTIFACTORY_ACCESS_TOKEN` **empty** in secure mode. The gateway forwards each user's OAuth token instead of a shared JFrog token.

Set your JFrog base URL (free-tier example):

```bash
ARTIFACTORY_BASE_URL=https://YOUR_INSTANCE.jfrog.io/artifactory
```

---

## Step 3: Google OAuth (Artifactory and Google Drive)

Both the Artifactory and Google Drive backends use **Google** as their OAuth provider. Secure mode uses one Google OAuth client for both, with different scopes per namespace (see `configs/secure-oauth-config.yml`).

### 3a. Create a Google Cloud OAuth client

1. Open [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials).
2. Create a project (or pick an existing one).
3. **APIs & Services → OAuth consent screen**
   - User type: **External** (or Internal if you have Google Workspace).
   - Fill in app name, support email, developer contact.
   - Scopes: add `openid`, `email`, and (for Google Drive) `https://www.googleapis.com/auth/drive` and `https://www.googleapis.com/auth/documents`.
   - Add your email as a **test user** while the app is in "Testing" mode.
4. **Credentials → Create credentials → OAuth client ID**
   - Application type: **Web application**
   - Name: `MCP Gateway (local)`
   - **Authorized redirect URIs**, add exactly:
     ```
     http://localhost:8090/oauth2callback
     ```
5. Copy the **Client ID** and **Client secret** into `.env`:

```bash
GOOGLE_OAUTH_CLIENT_ID=123456789-xxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxx
```

> **Important:** The redirect URI must be `http` (not `https`) and port `8090`, which is where the gateway listens locally.

### 3b. (Optional) True per-user JFrog tokens

To exchange each user's Google `id_token` for a **user-scoped JFrog token** (instead of a shared token), you also need a JFrog OIDC integration:

1. In JFrog Platform → **Administration** → **General Management** → **Manage Integrations** → **OpenID Connect** tab, click **New Integration → OpenID Connect** and create a provider (set the Projects dropdown to **All Projects** first):
   - **Provider Name:** `google-mcp-gateway` (must match `ARTIFACTORY_OIDC_PROVIDER_NAME` in `.env`)
   - **Provider Type:** `generic`
   - **Provider URL:** `https://accounts.google.com`
   - **Audience:** your Google OAuth **client ID** (the same `GOOGLE_OAUTH_CLIENT_ID` from `.env`)
2. Add an **Identity Mapping** to that integration. Match on the `aud` claim (your client ID) so **any** user who authenticated through the gateway is trusted, with no per-email hardcoding:
   - **Claims JSON:**
     ```json
     { "aud": "<YOUR_GOOGLE_CLIENT_ID>.apps.googleusercontent.com" }
     ```
   - **Token scope:** `Group` → `readers` (uniform read access for every authenticated user), leave the user field blank.
     Alternatively, use `User Dynamic Mapping` with pattern `{{email}}` for per-user identity (non-existent users become transient users and inherit any group marked *Automatically Join New Users*).
   - **Service:** All · **Expiry:** `60`
3. Uncomment in `.env`:
   ```bash
   ARTIFACTORY_OIDC_PROVIDER_NAME=google-mcp-gateway
   ```

> **Important:** If you ever recreate the Google OAuth client, the token's `aud` changes. Update the **Audience** on the integration **and** the `aud` in the identity mapping's Claims JSON to the new client ID, or the exchange fails with `HTTP 403 Forbidden`.

Skip this subsection if you only want to validate the OAuth + gateway flow first.

---

## Step 4: GitHub OAuth App (GitHub namespace)

The GitHub backend uses a **GitHub OAuth App** (not a GitHub App).

### 4a. Register the OAuth App

1. Open [GitHub → Settings → Developer settings → OAuth Apps](https://github.com/settings/developers).
2. **New OAuth App**
   - **Application name:** `mcp-gateway` (any name works)
   - **Homepage URL:** `http://localhost:8090`
   - **Authorization callback URL**, must be exactly:
     ```
     http://localhost:8090/oauth2callback
     ```
3. Click **Register application**.
4. Copy the **Client ID**.
5. Click **Generate a new client secret** and copy it immediately.

Add both to `.env`:

```bash
GITHUB_OAUTH_CLIENT_ID=Ov23lixxxxxxxx
GITHUB_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 4b. Copy credentials carefully

GitHub client IDs are case-sensitive and easy to mistype:

- The third segment uses **`I`** (capital i) vs **`l`** (lowercase L). Copy with the **Copy** button, do not retype.
- The last character is usually **`0`** (zero), not **`O`** (letter O).

A wrong client ID causes a GitHub **404** on the authorize page after you sign in.

### 4c. (Optional) Restrict which GitHub users can connect

```bash
GITHUB_ALLOWED_USERS=your-github-login
```

Comma-separated list. Leave unset to allow any GitHub user who completes OAuth.

---

## Step 5: Start the stack

From `mcp-gateway/`:

```bash
docker compose up -d --build
```

If images already exist and you only changed config, `docker compose up -d` is enough.

Wait until all services are healthy:

```bash
docker compose ps
```

Expected:

| Container | Port | Status |
|-----------|------|--------|
| `mcp-gateway` | 8090 | healthy |
| `artifactory-mcp-server` | 8091 | healthy |
| `github-mcp-server` | internal only | healthy |
| `gdrive-mcp-server` | internal only | healthy |
| `mcp-gateway-redis` | 6379 | healthy |

Quick sanity check:

```bash
curl -s http://localhost:8090/health
# {"status":"ok"}
```

Unauthenticated MCP calls must return **401**:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8090/github/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# 401
```

---

## Step 6: Test OAuth with the reference client

The repo includes a zero-dependency Python client that runs the full MCP OAuth handshake (DCR → browser login → token → tool call).

Run from `mcp-gateway/`:

### Artifactory (Google OAuth)

```bash
python3 scripts/oauth-client-demo.py --namespace artifactory --tool list_repositories
```

### GitHub (GitHub OAuth)

```bash
python3 scripts/oauth-client-demo.py --namespace github --tool list_repositories
```

### Google Drive (Google OAuth with Drive scopes)

```bash
python3 scripts/oauth-client-demo.py --namespace gdrive --tool list_files
```

What happens:

1. Terminal prints discovery + DCR output.
2. Browser opens → **gateway consent screen** → click **Authorize**.
3. Browser redirects to **Google** or **GitHub** → sign in → approve the app.
4. Browser shows *"Authorization complete. You can close this tab."*
5. Terminal prints the live tool result.

If the GitHub step shows a **404** after login, re-check the Client ID in `.env` against GitHub settings (Step 4b).

---

## Step 7: Connect Cursor

Cursor cannot talk HTTP OAuth to `localhost` directly; use the included **`mcp-remote`** wrappers.

### 7a. Install mcp-remote (one time)

```bash
cd mcp-gateway/tools/mcp-remote-runner
npm install
cd ../..
```

### 7b. Add MCP servers to Cursor

Edit `~/.cursor/mcp.json` (use absolute paths on your machine):

```json
{
  "mcpServers": {
    "artifactory": {
      "command": "/ABSOLUTE/PATH/TO/mcp-server-gateway/mcp-gateway/scripts/artifactory-mcp-remote.sh"
    },
    "github": {
      "command": "/ABSOLUTE/PATH/TO/mcp-server-gateway/mcp-gateway/scripts/github-mcp-remote.sh"
    },
    "gdrive": {
      "command": "/ABSOLUTE/PATH/TO/mcp-server-gateway/mcp-gateway/scripts/gdrive-mcp-remote.sh"
    }
  }
}
```

> **Note:** Each wrapper pins a **fixed loopback callback port** (`42833` artifactory, `42834` github, `42835` gdrive). This keeps the `mcp-remote` OAuth token cache stable across restarts. Otherwise a random port each launch invalidates the cached token and forces a fresh browser login every time. Override per server with `MCP_CALLBACK_PORT` if a port is already in use.

### 7c. Authenticate in Cursor

1. **Cursor → Settings → Tools → MCP**
2. Enable **artifactory**, **github**, and/or **gdrive**
3. Click **Authenticate** (or **Connect**) on each server
4. Complete gateway consent → Google/GitHub login → approve
5. You should see tools listed (e.g. *7 tools* for Artifactory, *13 tools* for GitHub)

In chat, try:

> List my GitHub repositories using the MCP server

---

## Step 8: (Optional) Monitoring

An OpenTelemetry + Grafana stack lives under `mcp-gateway/monitoring/`. To enable traces:

1. Uncomment in `.env`:
   ```bash
   OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
   ```
2. Recreate the gateway so it picks up the endpoint:
   ```bash
   cd mcp-gateway
   docker compose up -d gateway
   ```
3. Start the monitoring stack:
   ```bash
   cd mcp-gateway/monitoring
   docker compose up -d
   ```
4. Open Grafana at `http://localhost:3000`.

The *MCP Gateway* dashboard includes a **Client type** view that separates autonomous agent traffic (e.g. the JFrog agent below) from interactive coding assistants like Cursor.

| Service | Port |
|---------|------|
| Grafana | 3000 |
| Prometheus | 9090 |
| Tempo | 3200 |
| OTEL Collector (HTTP) | 4318 |

---

## Step 9: (Optional) JFrog AI Agent (Command Center)

Once the gateway is running (Step 7 works), you can run the LangGraph + Streamlit **JFrog agent** that queries Artifactory through the gateway with per-user OAuth.

```bash
cd Agents/jfrog-agent
cp .env.example .env          # then set OPENAI_API_KEY (or use the offline planner)
docker compose up --build     # UI at http://localhost:8501
```

The agent keeps three concerns separate:

| Concern | What it stores | Default backend |
|---------|----------------|-----------------|
| **Conversation memory** | Threads, messages, LangGraph checkpoints | SQLite (`/data/memory.db`, `/data/checkpoints.db`) |
| **Tracking / eval** | Job runs, tool invocations, trajectories, quality scores | SQLite (or BigQuery for production eval) |
| **Audit trail** | Immutable, secret-redacted per-run records | JSONL under `/data/audit/` |

Optional features (see `.env.example`):

- **BigQuery tracking:** set `JFROG_AGENT_TRACKING_BACKEND=bigquery` plus project/dataset IDs.
- **Context optimizer:** set `CTX_OPT_ENABLED=true` to apply layered token budgeting before planner/summarizer LLM calls.
- **Spanner memory:** set `JFROG_AGENT_MEMORY_BACKEND=spanner` to use the local Spanner emulator included in `docker compose`.

Verify memory and checkpoints:

```bash
cd Agents/jfrog-agent
PYTHONPATH=. python scripts/verify_memory.py
```

Full instructions (local run, memory backends, tracking, and the Streamlit UI) are in [Agents/jfrog-agent/README.md](Agents/jfrog-agent/README.md).

---

## Endpoints reference

| URL | Purpose |
|-----|---------|
| `http://localhost:8090/health` | Gateway health |
| `http://localhost:8090/artifactory/mcp` | Artifactory MCP (OAuth required) |
| `http://localhost:8090/github/mcp` | GitHub MCP (OAuth required) |
| `http://localhost:8090/gdrive/mcp` | Google Drive MCP (OAuth required) |
| `http://localhost:8090/oauth2callback` | OAuth redirect (all providers) |
| `http://localhost:8091/mcp` | Artifactory MCP direct (bypasses gateway, dev only) |
| `http://localhost:8501` | JFrog agent Streamlit UI (when running) |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| GitHub **404** on authorize | Wrong `GITHUB_OAUTH_CLIENT_ID` in `.env` | Copy Client ID from GitHub settings again |
| `error fetching user info` in Cursor | Gateway container cannot reach `api.github.com` | Retry; check Docker network / VPN; restart gateway |
| Gateway **401** forever | Secure mode not enabled | Set `OAUTH_CONFIG_FILE` + `MCP_CONFIG_FILE` in `.env`, restart |
| Cursor **Error (Show Output)** | `mcp-remote` not installed | Run `npm install` in `tools/mcp-remote-runner/` |
| Artifactory tools return empty/errors | JFrog URL wrong or OIDC not configured | Check `ARTIFACTORY_BASE_URL`; set up OIDC for per-user mode |
| Google Drive tools fail | Drive/Docs scopes missing from OAuth consent | Add Drive + Docs scopes in Google Cloud Console |
| `GITHUB_ALLOWED_USERS` rejection | Logged in as different GitHub user | Match login in `.env` or clear the allowlist |
| Browser login prompts **every** call / OAuth callback `ERR_CONNECTION_REFUSED` on a random `localhost:<port>` | `mcp-remote` used a random callback port, invalidating the cached token | Ensure the wrappers pin `CALLBACK_PORT` (already set); clear stale state with `rm -rf ~/.mcp-auth/mcp-remote-*` and reconnect once |
| `JFrog OIDC exchange failed: HTTP 403 Forbidden` | Identity mapping's `aud` (or email) claim doesn't match the live token, usually after recreating the Google client | Update the OIDC integration **Audience** and the identity mapping **Claims JSON** `aud` to the current `GOOGLE_OAUTH_CLIENT_ID` |
| `JFrog OIDC exchange failed: HTTP 400 invalid audience` | Integration **Audience** doesn't equal the Google client ID | Set the integration's Audience to `GOOGLE_OAUTH_CLIENT_ID` |
| `gdrive-mcp` build fails on pip SSL | Corporate TLS proxy during `docker compose --build` | Use existing images with `docker compose up -d`, or add corporate CA to the Dockerfile |

View gateway logs:

```bash
cd mcp-gateway
docker compose logs -f gateway
```

---

## Development

See [mcp-gateway/README.md](mcp-gateway/README.md) for gateway-specific dev commands (`npm run dev`, tests, config reference).

Per-backend docs:

- [artifactory/README.md](artifactory/README.md)
- [github/README.md](github/README.md)
- [gdrive/README.md](gdrive/README.md)

---

## Security notes

- **Never commit `.env`**. It is gitignored. Only `.env.example` belongs in the repo.
- OAuth client secrets live in `.env` locally and in Google/GitHub developer consoles.
- In secure mode, no shared `GITHUB_TOKEN` or `ARTIFACTORY_ACCESS_TOKEN` is required.
- The gateway encrypts stored OAuth tokens at rest using `TOKEN_ENCRYPTION_KEY`.
