# ai-telemetry-gateway

This is a self-hosted LiteLLM proxy with a PostgreSQL ledger. It sits in front of cloud OpenAI calls from MarkUs, the grading platform. It logs each call for billing and audit. It blocks a call that goes over a course budget or the term-wide cap.

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

The gateway works end to end on a local docker-compose stack. A call takes this path:

1. MarkUs
2. the autotester
3. the AI feedback library
4. the LiteLLM proxy
5. a mock or OpenAI
6. the PostgreSQL ledger

`docs/USER_GUIDE.md` shows how to run it and test it.

## Where this fits in the larger system

```
MarkUs (Rails) ──► Autotester ──► [ this project ] ──► OpenAI cloud
                                       │
                                       ▼
                                  PostgreSQL
                                  (ledger + budgets)
```

Two proxies split the traffic:

- Local-model traffic (Ollama, llama.cpp) still goes through the existing `markus-ai-server` proxy ("polymouth").
- Cloud OpenAI traffic goes through this project.

## Local development

See `local-stack/README.md`.
