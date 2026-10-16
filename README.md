# MiroFishPro (Modified)

A modified version of [MiroFishPro](https://github.com/mxypoo/MiroFishPro), which builds upon [MiroFish](https://github.com/666ghj/MiroFish) but completely removes the requirement for a paid Zep Cloud subscription. 

This repository intercepts file uploads to process images multimodally using the Gemini API, saving textual image descriptions locally as accessible project assets.

---

## Prerequisites

Before starting, ensure you have the following installed on your host system and WSL environment:

*   **Node.js**: `18+`
*   **Python**: `3.11+`
*   **WSL (Windows Subsystem for Linux)** equipped with:
    *   [`uv`](https://docs.astral.sh/uv/) (Extremely fast Python package & project manager)
    *   **Docker** (Recommended, for one-click Neo4j & Qdrant database orchestration)

---

## Setup Instructions

### 1. Start Local Dependencies (Database Layer)
Spin up the required localized Neo4j and Qdrant instances using Docker Compose:

```bash
docker compose -f docker-compose.local.yml up -d

```

> ⚠️ **Note on Hardcoded Credentials:**
> Default Neo4j credentials are pre-configured in `docker-compose.local.yml`. Your `.env` file **must** match these exactly:
> * **User:** `neo4j`
> * **Password:** `mirofish` (`NEO4J_PASSWORD=mirofish`)
> 
> 

### 2. Configure Environment Variables

Copy the sample environment file and insert your respective API keys (including your Gemini API configuration):

```bash
cp .env.example .env

```

### 3. Install Frontend & Global Dependencies

From the project root, install all Node packages and root toolchains:

```bash
npm run setup:all

```

### 4. Set Up the Python Backend (WSL Environment)

Open your WSL terminal, navigate to the project root, and execute the following to set up your virtual environment and resolve python dependencies:

```bash
# 1. Create a modern isolated virtual environment
uv venv

# 2. Activate the virtual environment
source .venv/bin/activate

# 3. Cleanly sync your environment directly from the requirements sheet
uv pip sync requirements.txt

```

*(Using `uv pip sync` ensures your environment exactly reflects `requirements.txt` and prevents conflicting rebuilds when launching development tools).*

---

## Running the Project

To spin up both frontend and backend systems simultaneously, run:

```bash
npm run dev
```

For local WSL development, run Vite (port 3000) and Flask (port 5001) together
inside WSL, then open `http://localhost:3000` from Windows. API requests and report
download/export links use the browser's current origin by default. Vite proxies
`/api` requests to `http://127.0.0.1:5001` inside WSL.

To call an API directly, set `VITE_API_BASE_URL` in `frontend/.env.local` (or the
environment used to start Vite) and restart Vite. A nonempty value overrides the
default in both development and production and bypasses the local proxy when it
points to another server. Use the backend base URL, for example
`VITE_API_BASE_URL=http://localhost:5001`, without appending `/api`; endpoint paths
already include it. Production builds default to `http://localhost:5001` when no
override is set. The browser startup console logs the effective API base URL.

Run the frontend request checks and production build using installed packages in WSL:

```bash
node frontend/tests/ontology-request.mjs
npm run build
```

> 💡 **Important:** The startup process automatically checks for missing dependencies. You must wait until the terminal explicitly outputs **`Debugger is active`**—this log confirms the Python backend has successfully initialized and is ready to intercept actions.
