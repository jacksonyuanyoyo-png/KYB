# FCC Complex Account Platform

Internal WI/PI workflow for collecting complex-entity ownership, account attributes, and a versioned document checklist.

> The current ruleset is a demonstrator. It is not approved legal or compliance advice and must not be used for production account opening until FCC Compliance signs off on every rule and source.

## Repository

- `complex-account-guide.html` — portable, no-backend demonstration prototype.
- `apps/web` — Next.js internal staff application.
- `apps/api` — NestJS API, OpenAPI UI at `/api/docs`.
- `packages/domain` — shared ownership validation and versioned checklist rules.
- `apps/api/prisma/schema.prisma` — PostgreSQL persistence and audit model.
- `infra/main.bicep` — Canadian-region Azure data services security baseline.
- `docs/production-readiness.md` — security, data, and approval gates.

## Local development

当前工作台（网页 + Python 接口 + PostgreSQL）用一条命令启动，说明在 [docs/一键启动.md](docs/一键启动.md)：

```bash
./scripts/up.sh
```

打开 http://localhost:3000 。停止用 `./scripts/down.sh`。下面的 `npm run dev` 会拉起旧的 Nest 接口（端口 4000），不是这条一键路径。

Requirements: Node 22.22.3+, npm 10+, Docker.

```bash
cp .env.example .env
docker compose up -d
npm install
npm run db:generate
npm run db:migrate
npm run dev
```

Open `http://localhost:3000`; API documentation is at `http://localhost:4000/api/docs`.

The local API uses explicit mock headers. Production startup must set `AUTH_MODE=entra`; the API intentionally rejects that mode until a tenant-specific token-verification adapter is configured.

## Quality commands

```bash
npm run typecheck
npm test
npm run build
```

## Architecture decisions

- A modular TypeScript monorepo keeps workflow types and rules identical in browser and API.
- PostgreSQL stores structured case state; uploaded files belong in encrypted object storage, not database rows.
- Every update uses optimistic concurrency (`version`) and writes an append-only audit event.
- Checklist output records `ruleVersion`; published rule versions must be immutable.
- Azure Canada Central is the proposed primary region, with Canada East recovery subject to FCC infrastructure review.
