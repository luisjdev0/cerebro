#!/usr/bin/env python3
"""Seed a DISPOSABLE cerebro instance with simulated data (users, contexts, memories,
documents, flows), spread over the last ~6 months so activity charts have something to show.

Never point this at a real deployment. It talks to the gateway through
`cerebro-clients` and then backdates `created_at`/`updated_at` with SQL through
`docker compose exec postgres psql` (run it from the compose checkout).

    python scripts/seed_demo.py --url http://localhost:8090 --token <API_TOKEN>
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from cerebro_clients.auth_client import AuthClient
from cerebro_clients.base import CerebroAPIError
from cerebro_clients.docs_client import DocsClient
from cerebro_clients.flows_client import FlowsClient
from cerebro_clients.memory_client import MemoryClient

DEMO_PASSWORD = "DemoPassw0rd!"
USERS = [
    ("demo-admin", "admin", "admin@demo.local"),
    ("maria", "owner", "maria@demo.local"),
    ("carlos", "user", "carlos@demo.local"),
]

CONTEXTS = [
    ("acme-website", "Acme website", "project", "Marketing site rebuild for the client Acme."),
    ("billing-service", "Billing service", "project", "Invoicing microservice: Stripe, retries, tax rules."),
    ("infrastructure", "Infrastructure", "domain", "Servers, deployments, DNS, backups and monitoring."),
    ("personal-finance", "Personal finance", "domain", "Budgets, subscriptions and yearly expenses."),
    ("health", "Health", "domain", "Exercise, check-ups and sleep habits."),
    ("learning", "Learning", "domain", "Courses, books and certifications in progress."),
]

TOPICS = {
    "acme-website": ["Homepage hero copy approved", "Switched the CMS to a headless setup", "Lighthouse score reached 96",
                     "Client asked for a dark mode toggle", "Cookie banner must follow the EU rules", "Image CDN migrated"],
    "billing-service": ["Retry policy: 3 attempts with backoff", "Tax rounding bug fixed in invoice totals", "Stripe webhook secret rotated",
                        "Decided to store amounts as integer cents", "Dunning emails scheduled after day 3", "Idempotency keys added to charges"],
    "infrastructure": ["Production OS upgraded to Ubuntu 24.04", "Nightly backups verified with a test restore", "DNS TTL lowered before the migration",
                       "Disk usage alert set at 80%", "Reverse proxy certificates renew automatically", "Moved the staging database to its own host"],
    "personal-finance": ["Monthly budget raised to 2,000", "Cancelled two unused subscriptions", "Emergency fund target reached",
                         "Yearly insurance renewal in March", "Tracking expenses with a weekly review", "Switched to a no-fee bank account"],
    "health": ["Dentist check-up scheduled", "Started a 3x week strength routine", "Sleep average improved to 7h",
               "Allergy test results filed", "Annual blood work done", "Walking meeting every afternoon"],
    "learning": ["Finished the Rust ownership chapter", "Enrolled in the cloud certification course", "Reading list reordered by priority",
                 "Weekly study block moved to mornings", "Notes system migrated to markdown", "Completed the SQL window functions workshop"],
}
TYPES = ["semantic", "episodic", "procedural", "decision"]

DOC_CATEGORIES = [("runbooks", "Runbooks"), ("decisions", "Architecture decisions"), ("onboarding", "Onboarding")]
DOCS = [
    ("runbooks", "Restore a database backup", "# Restore a database backup\n\n## When to use\nAfter data loss or a bad migration.\n\n## Steps\n1. Stop the writers.\n2. Restore the latest dump.\n3. Run the smoke checks.\n"),
    ("runbooks", "Rotate API tokens", "# Rotate API tokens\n\n## Steps\n1. Create the new token.\n2. Update the consumers.\n3. Revoke the old token.\n"),
    ("runbooks", "Deploy to production", "# Deploy to production\n\n## Checklist\n- Tests green\n- Backup taken\n- Rollback plan ready\n"),
    ("decisions", "ADR-001: Use Postgres for everything", "# ADR-001: Use Postgres for everything\n\n## Context\nWe need relational data, full-text search and vectors.\n\n## Decision\nOne Postgres instance with separate schemas.\n"),
    ("decisions", "ADR-002: Static portal behind the gateway", "# ADR-002: Static portal behind the gateway\n\n## Decision\nServe the SPA from its own container; the gateway routes by prefix.\n"),
    ("onboarding", "Welcome to the team", "# Welcome to the team\n\nRead the runbooks first, then pick a starter task.\n"),
    ("onboarding", "Local development setup", "# Local development setup\n\n```bash\ndocker compose up -d\n```\n"),
]

FLOW_CATEGORY = ("operations", "OPS", "Operations")
FLOWS = [
    """
metadata:
  name: Handle an incident
  category: operations
  description: Triage and resolve a reported incident.
entry: analyze
procedure:
  - id: analyze
    type: task
    description: Analyze the reported incident.
    next: decide
  - id: decide
    type: decision
    description: Is there enough information?
    branches:
      sufficient: resolve
      insufficient: ask
  - id: ask
    type: task
    description: Ask the reporter for the missing information.
    next: analyze
  - id: resolve
    type: task
    description: Apply the fix.
    checkpoint:
      required: true
      prompt: "Confirm the fix before closing the incident?"
      on_reject: analyze
    next: close
  - id: close
    type: task
    description: Close the incident.
    terminal: true
""",
    """
metadata:
  name: Release checklist
  category: operations
  description: Steps to ship a release safely.
entry: freeze
procedure:
  - id: freeze
    type: task
    description: Freeze the release branch.
    next: verify
  - id: verify
    type: task
    description: Run the full test suite.
    checkpoint:
      required: true
      prompt: "Are the tests green?"
      on_reject: freeze
    next: ship
  - id: ship
    type: task
    description: Tag and deploy.
    terminal: true
""",
]


def _ts(rng: random.Random, days: int = 180) -> datetime:
    # Recent days are busier: square the uniform draw so the heatmap looks organic.
    back = (rng.random() ** 1.6) * days
    return datetime.now(timezone.utc) - timedelta(days=back, hours=rng.random() * 14, minutes=rng.random() * 60)


def _psql(compose_dir: str, sql: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "knowledgeos", "-d", "knowledgeos", "-v", "ON_ERROR_STOP=1"],
        input=sql.encode(), cwd=compose_dir, check=True, capture_output=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="gateway base URL, e.g. http://localhost:8090")
    ap.add_argument("--token", required=True, help="root API token")
    ap.add_argument("--compose-dir", default=".", help="directory with compose.yaml (for backdating SQL)")
    ap.add_argument("--memories", type=int, default=140)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    base = args.url.rstrip("/")

    auth = AuthClient(f"{base}/auth", args.token)
    for name, level, email in USERS:
        try:
            auth.create_user(name, email=email, access_level=level, password=DEMO_PASSWORD)
        except CerebroAPIError as e:
            if e.status_code != 409:
                raise
    try:
        auth.create_group("platform", "Platform team")
    except CerebroAPIError as e:
        if e.status_code != 409:
            raise
    for member in ("maria", "carlos"):
        try:
            auth.add_group_member("platform", member)
        except CerebroAPIError:
            pass
    # owners inherit their groups' scopes: without this their session tokens reach no module
    auth.set_group_scopes("platform", ["memory", "docs", "flows"])

    def user_clients(name: str):
        tok = AuthClient(f"{base}/auth", None).login_password(name, DEMO_PASSWORD)["token"]
        return MemoryClient(f"{base}/memory", tok), DocsClient(f"{base}/docs", tok), FlowsClient(f"{base}/flows", tok)

    root_mem = MemoryClient(f"{base}/memory", args.token)
    root_docs = DocsClient(f"{base}/docs", args.token)
    root_flows = FlowsClient(f"{base}/flows", args.token)

    for slug, name, kind, desc in CONTEXTS:
        try:
            root_mem.create_context(slug, name, kind, description=desc)
        except CerebroAPIError as e:
            if e.status_code != 409:
                raise

    # Memories: written by different users so ownership gates have data to show.
    writers = {"demo-admin": user_clients("demo-admin")[0], "maria": user_clients("maria")[0], "carlos": user_clients("carlos")[0]}
    mem_ids: list[str] = []
    for i in range(args.memories):
        ctx = rng.choice(list(TOPICS))
        topic = rng.choice(TOPICS[ctx])
        kind = rng.choice(TYPES)
        who = rng.choice(list(writers))
        res = writers[who].create_memory(
            f"{topic}. Note #{i + 1}: details recorded for later reference.",
            ctx, kind, title=f"{topic} (#{i + 1})", importance=round(rng.uniform(0.3, 0.95), 2), source=who,
        )
        mem_id = (res.get("memory") or res).get("id")
        if mem_id:
            mem_ids.append(mem_id)

    # Documents.
    for slug, name in DOC_CATEGORIES:
        try:
            root_docs.create_category(slug, name)
        except CerebroAPIError as e:
            if e.status_code != 409:
                raise
    doc_ids: list[str] = []
    for cat, title, content in DOCS:
        try:
            doc_ids.append(root_docs.create_document(title, content, cat)["id"])
        except CerebroAPIError as e:
            if e.status_code != 409:
                raise

    # Flows (definitions + a couple of runs).
    try:
        root_flows.create_category(FLOW_CATEGORY[0], FLOW_CATEGORY[1], FLOW_CATEGORY[2])
    except CerebroAPIError as e:
        if e.status_code != 409:
            raise
    for yaml_content in FLOWS:
        try:
            flow = root_flows.create_flow(FLOW_CATEGORY[0], yaml_content)
            run = root_flows.start_flow(flow["code"])
            run_id = run.get("run_id")
            if run_id:
                root_flows.next_step(run_id)
        except CerebroAPIError as e:
            print(f"flow skipped: {e}", file=sys.stderr)

    # Backdate so charts show months of history.
    stmts = []
    for mid in mem_ids:
        t = _ts(rng)
        u = t + timedelta(hours=rng.random() * 60) if rng.random() < 0.35 else t
        stmts.append(f"UPDATE cerebro_memory.memories SET created_at='{t.isoformat()}', updated_at='{min(u, datetime.now(timezone.utc)).isoformat()}' WHERE id='{mid}';")
    for did in doc_ids:
        t = _ts(rng, 120)
        stmts.append(f"UPDATE cerebro_docs.documents SET created_at='{t.isoformat()}', updated_at='{t.isoformat()}' WHERE id='{did}';")
    if stmts:
        _psql(args.compose_dir, "\n".join(stmts))
    print(f"seeded: {len(USERS)} users, {len(CONTEXTS)} contexts, {len(mem_ids)} memories, {len(doc_ids)} documents, flows {len(FLOWS)}")
    print(f"demo logins: {', '.join(u[0] for u in USERS)} / {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
