# OpenRouter Rotation Model

Et selvstændigt Python/FastAPI-modul til OpenRouter-modelrotation, retries og failover.

## Download som ZIP

Åbn repository-siden og vælg **Code → Download ZIP**. ZIP-downloaden indeholder kun rotationsmotoren og dens nødvendige driftsfiler.

## Funktioner

- Round-robin rotation mellem modeller
- Automatisk hentning af gratis OpenRouter-modeller
- Rotation mellem flere API-keys
- Retry ved timeout, 429 og 5xx
- Midlertidig cooldown for fejlende routes
- FastAPI-endpoints: `/api/chat`, `/v1/chat/completions`, `/api/models`, `/health`
- Dockerfile og Render-konfiguration

## Start

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn llm_router.gateway:app --host 0.0.0.0 --port 8000
```

Sæt din nøgle i `.env`:

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

API-nøgler er ikke inkluderet eller committed.
