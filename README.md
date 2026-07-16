# OpenRouter Chat

En selvstændig Python/FastAPI chatbot med OpenRouter-modelrotation, retries og failover samt en indbygget mørk web-UI.

## Funktioner

- Futuristisk HUD web-UI med 3D-agnostiske elementer og baggrund
- Chat-grænseflade direkte i browseren (`/`)
- Automatisk rotation mellem gratis OpenRouter-modeller
- Multi-key rotation og failover
- Retry ved timeout, 429 og 5xx
- Midlertidig cooldown for fejlende routes
- Settings-fane til API-nøgler, modelvalg, temperatur og max tokens
- Lokal samtalehistorik og arkiv i browseren
- FastAPI-endpoints: `/api/chat`, `/v1/chat/completions`, `/api/models`, `/api/config`, `/health`
- Dockerfile og Render-konfiguration

## Lokal start

1. Kopiér `.env.example` til `.env` og tilføj din OpenRouter API-nøgle:
   ```bash
   cp .env.example .env
   # rediger .env
   ```

2. Installer og kør:
   ```bash
   pip install -r requirements.txt
   uvicorn llm_router.gateway:app --reload
   ```

3. Åbn `http://localhost:8000` i din browser.

## Deploy på Render

1. Opret en ny **Web Service** på Render og forbind dette repository.
2. Sæt miljøvariablen `OPENROUTER_API_KEYS` til din(e) komma-separerede nøgle(r).
3. `render.yaml` og `Dockerfile` bruges automatisk. Health check kører på `/health`.
4. (Valgfrit) Sæt `ADMIN_TOKEN` for at beskytte indstillinger mod uautoriserede ændringer.

API-nøgler committes ikke – de hentes fra miljøvariabler.
