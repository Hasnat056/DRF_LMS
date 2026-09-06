# Does the admin list cache earn its keep?

The endpoint audit measured one request at a time. At concurrency 1 MySQL is
uncontended, which is the cache's worst case for showing value — so the finding
that it saves only 165 ms while costing 19,657 ms per invalidation deserves a
test under real load before anyone acts on it.

This directory holds that test.

## The question

Not "is the cache faster" — under pure read load it obviously is. The question
is whether it is *net* faster once you pay for invalidation, and that depends
entirely on how often someone writes.

So the variable is write rate, and everything else is held still:

| arm | readers | writes | `WRITE_INTERVAL_S` |
|---|---|---|---|
| A | 50 | none | `0` |
| B | 50 | 1/min | `60` |
| C | 50 | 6/min | `10` |
| D | 50 | 60/min | `1` |

If the cache earns its keep, p95 stays flat across the arms. If the audit's
finding holds, it degrades as writes get more frequent, because each write
queues a rebuild that competes with the readers for the same CPU — the
mechanism that already made five student endpoints slower warm than cold.

## Setup

The test stack lives in its own compose file, `tests/docker-compose.yaml`,
with its own env file beside it (`tests/.env`, from `tests/.env.example`).
Compose loads that automatically, so the commands below need no flags. It sets
`DJANGO_ENV=test`, which points settings.py at `database-test` and
`redis-test` -- and pytest forces the same thing on its own, so the suite is
isolated even if you run it from a dev shell.

```bash
# Shorthand for the rest of this file
TC="docker compose -f tests/docker-compose.yaml"

# 1. Bring up the stress stack: the seeded database, its Redis, a real worker,
#    and a backend that serves them.
$TC up -d

# 2. Confirm it is the stress data, not dev data
curl -s localhost:8002/api/schema/ -o /dev/null -w '%{http_code}\n'   # 200
$TC exec -T backend-stress python -c "
import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','NexusAPI.settings'); django.setup()
from Models.models import Student; print(Student.objects.count())"     # 5000

# 3. Mint a token. The seeder creates users with no password, so there is
#    nothing to log in with -- this is the normal path, not a workaround.
export LOAD_TOKEN=$($TC exec -T backend-stress python manage.py shell -c "
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User
print('TOKEN:', RefreshToken.for_user(User.objects.get(
    username='bench.admin@stress.test')).access_token)" \
  | grep '^TOKEN:' | cut -d' ' -f2 | tr -d '\r')
```

Tokens last 60 minutes, which covers all four arms.

## Running the arms

```bash
mkdir -p tests/loadtest/results

for arm_interval in a:0 b:60 c:10 d:1; do
  arm=${arm_interval%%:*}; interval=${arm_interval##*:}
  echo "== arm $arm (WRITE_INTERVAL_S=$interval) =="
  WRITE_INTERVAL_S=$interval locust -f tests/loadtest/cache_pressure.py \
    --headless -u 50 -r 10 --run-time 3m --host http://localhost:8002 \
    --csv tests/loadtest/results/arm_$arm
  sleep 30   # let the worker drain before the next arm
done
```

Then compare — the p95 column is the one that answers the question:

```bash
column -s, -t tests/loadtest/results/arm_*_stats.csv | grep -E 'Name|/students/'
```

## Reading the result

- **p95 flat across A→D** — the cache is worth keeping. The audit's conclusion
  was an artefact of measuring at concurrency 1.
- **p95 climbs from A→D** — the audit's conclusion holds, and the fix is to
  scope the student-list rebuild (already done in `be0c39d`) or drop the cache
  on the filtered branches.
- **Arm A already slow** — the app server is the bottleneck, not the database.
  Raise `GUNICORN_WORKERS` and rerun; nothing else in the run means anything
  until arm A is comfortable.

## Things that will silently ruin a run

**Host memory.** This machine has swung 2.6× from swap pressure — the same 57
untouched endpoints ran 2.58× slower between two runs hours apart. Watch
`vmstat 5` in another terminal. Any run showing swap-in is void, not
interesting.

**A stale worker.** Celery does not reload changed modules. If you edit
`AdminModule/tasks.py`, restart `celery-worker-test` before measuring, and
check its start time is later than the edit. This has produced a "regression"
of 1,540 ms vs 86 ms once already.

**Looking at the wrong Redis key.** Django prefixes cache keys, so the student
list is `LMS:1:admin:student_list`, not `admin:student_list`. And the Celery
queue is `default`, not `celery` — `LLEN celery` always returns 0 and means
nothing. Use `LLEN default`.

**The writer doing nothing.** It PATCHes a student's status to the value it
already has. The write path runs in full and fires the rebuild exactly as a
real edit would, but no row changes — so repeated runs do not drift the stress
dataset the benchmark depends on. Confirm it is working before trusting an arm:

```bash
$TC exec -T redis-test redis-cli -n 0 DEL 'LMS:1:admin:student_list'
# ...PATCH...  then the key should reappear within ~2s
$TC exec -T redis-test redis-cli -n 0 EXISTS 'LMS:1:admin:student_list'
```

Measured at setup: the key came back **1,842 ms** after the PATCH.

## Why not just turn the cache off?

Because a disabled cache makes every request a miss, and every miss queues a
rebuild — that arm would measure a pathological state that does not exist in
production. Varying the write rate tests the real trade-off.

## Note on the other locustfile

`locustfile.py` at the repo root has three scenarios whose docstrings claim
"20% writes" and "50% writes". They are read-only; the only `POST` is the token
fetch. Useful for throughput, useless for anything about invalidation.
