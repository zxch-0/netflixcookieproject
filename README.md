# Netflix Dispo Checker

Web port of the Netflix cookie checker: drop your `.txt` cookie files (like the `cookies/` folder of the original script) or paste them, and get the accounts back sorted by plan tier (Premium → Standard → Standard With Ads → Basic → Mobile → Free → Invalid), each with an NFToken and PC / mobile / TV login links.

Deployable as-is on Render through the bundled blueprint.

- Many files at once by drag and drop or picker, plus pasted text in the same run
- Netscape files, JSON exports and raw `NetflixId=...` strings
- Every file is split into separate accounts, duplicates dropped across all inputs
- NFToken links with expiry, results sorted by tier, extra-member and on-hold flags
- Proxy pool with rotation and retries on 403/429/5xx, TXT / JSON export
- Strict verdicts: only a `/login` bounce, a login page, `401` or `404` is reported invalid. Proxies, timeouts and `5xx` land in errors, so invalids stay trustworthy
- Keep-alive sessions, compiled parsers and incremental rendering to stay fast on 10k batches
=======

## Deploy on Render

1. Push this repository to GitHub.
2. On Render: New → Blueprint, pick the repo, apply.
3. Set `ACCESS_TOKEN` so only you can use the instance.
4. Set `PROXY_LIST` if Netflix blocks the Render IPs: datacenter IPs often get 403, residential or mobile proxies fix it.

The free plan sleeps when idle.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ACCESS_TOKEN` | empty | Shared secret sent as `X-Access-Token`, empty disables auth |
| `PROXY_LIST` | empty | Server-side proxy pool, newline or comma separated |
| `DEFAULT_CONCURRENCY` | 8 | Threads per job by default |
| `MAX_CONCURRENCY` | 24 | Upper bound accepted from clients |
| `MAX_COOKIES_PER_JOB` | 150 | Accounts checked per job |
| `MAX_ACTIVE_JOBS` | 4 | Concurrent jobs before 429 |
| `JOB_TTL_SECONDS` | 3600 | How long finished jobs stay in memory |
| `REQUEST_TIMEOUT_SECONDS` | 12 | Per-request timeout |
| `CHECK_RETRIES` | 2 | Attempts per account, proxy rotated each try |
| `NFTOKEN_ATTEMPTS` | 3 | Attempts for token generation |
| `NFTOKEN_ENABLED` | true | Disable token generation entirely |

## Local run

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000. With Docker:

    docker build -t netflix-dispo .
    docker run --rm -p 8000:8000 -e ACCESS_TOKEN=secret netflix-dispo

## API

Files, multipart, up to 200 files:

    curl -X POST https://your-app.onrender.com/api/jobs/files \
      -H 'X-Access-Token: secret' \
      -F 'files=@cookies/one.txt' -F 'files=@cookies/two.txt' \
      -F 'devices=pc,tv'

Single paste, JSON:

    curl -X POST https://your-app.onrender.com/api/jobs \
      -H 'Content-Type: application/json' -H 'X-Access-Token: secret' \
      -d '{"cookies": ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tXXX", "devices": ["pc"]}'

Both return 202 with a `job_id`, poll `GET /api/jobs/{id}` until `status` is `done`.

## Tests

    pip install -r requirements-dev.txt
    pytest

The suite covers cookie parsing, zip expansion, plan mapping, verdict classification against a mocked Netflix and the full HTTP API. CI runs it on every push.

## Limitations

- Jobs live in memory: a restart drops running and finished jobs.
- Netflix changes its markup regularly, a field can go missing.
- Free Render instances share IPs, expect 403 without proxies.
- Educational use, on cookies you are allowed to test.
