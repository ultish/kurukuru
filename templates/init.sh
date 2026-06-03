#!/usr/bin/env bash
# init.sh — bring up the {{PROJECT}} dev environment in ONE command.
#
# The long-running-agents pattern: a session (human or agent) should be able to
# go from a fresh checkout to a runnable, testable app by running just this.
# Fill in the TODOs for THIS repo. /kuru:bearings and the verifier point here for
# "how to run / verify", so keep it working.
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (this script lives in .kuru/)

# 1. Install dependencies.
# TODO: e.g. `npm ci`  |  `pip install -e '.[dev]'`  |  `go mod download`

# 2. Start any backing services the app needs (db, queue, etc.).
# TODO: e.g. `docker compose up -d`

# 3. Run migrations / seed data.
# TODO: e.g. `npm run db:migrate`

echo "init.sh: TODO — fill in the steps to bring up {{PROJECT}}."
echo "Once filled in, this is the single command to get a runnable environment."
