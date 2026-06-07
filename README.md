# ai-telemetry-gateway

Self-hosted LiteLLM proxy + PostgreSQL ledger that fronts cloud OpenAI calls from the MarkUs grading platform. Records every call for billing and audit, refuses calls that exceed a course budget or term-wide cap.

## What lives here

```
ai-telemetry-gateway/
├── README.md            (this file)
├── docs/                decision record and user guide
├── lib/                 LiteLLM proxy hooks (attribution, gatekeeper, telemetry, CAD FX, encryption, dead-letter drain)
├── db/                  SQL migrations, seeds, and named health-check queries
├── tests/               pytest suite against a live local database
└── local-stack/         docker-compose for local development
```

## Status

The gateway is implemented and validated end-to-end against a local docker-compose stack: MarkUs → autotester → AI feedback library → LiteLLM proxy → mock or OpenAI → PostgreSQL ledger. See `docs/USER_GUIDE.md` for how to run and test it.

## Where this fits in the larger system

```
MarkUs (Rails) ──► Autotester ──► [ this project ] ──► OpenAI cloud
                                       │
                                       ▼
                                  PostgreSQL
                                  (ledger + budgets)
```

Local-model traffic (Ollama, llama.cpp) keeps flowing through the existing `markus-ai-server` proxy ("polymouth"). This project owns cloud OpenAI traffic only.

## Local development

See `local-stack/README.md`.
