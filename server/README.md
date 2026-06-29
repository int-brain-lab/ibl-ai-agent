# IBL Feedback Server

A small Django app that receives feedback bundles from the `ibl-ai-agent`
`/ibl-feedback` workflow and lets reviewers browse them — including a chat-style
replay of each session — behind a login, without needing AWS/SSH access.

- **Ingest:** `POST /api/feedback` (bearer-token auth, write-only)
- **Login:** `/accounts/login/` (Django auth)
- **Browse:** `/feedback/` (list) and `/feedback/<id>/` (chat replay)
- **Admin:** `/admin/` (raw records)
- **Storage:** SQLite by default; the transcript is kept in JSON columns.

## Two separate credentials

| Credential | Who holds it | Can |
|---|---|---|
| **Ingest token** (`IBL_FEEDBACK_INGEST_TOKEN`) | every client install | only POST feedback |
| **Reviewer login** (Django user) | the few people who read feedback | view sessions |

A leaked ingest token can only submit junk; it cannot read anything. Rotate it
by changing the env var on the server and redistributing.

## Local development

```bash
cd server
uv venv && uv pip install -r requirements.txt      # or python -m venv + pip
export IBL_FEEDBACK_DEBUG=1
export IBL_FEEDBACK_INGEST_TOKEN=devtoken
uv run python manage.py migrate
uv run python manage.py createsuperuser             # make a reviewer login
uv run python manage.py runserver
```

Then submit a test payload:

```bash
curl -X POST http://127.0.0.1:8000/api/feedback \
  -H "Authorization: Bearer devtoken" \
  -H "Content-Type: application/json" \
  -d '{"schema_version":1,"feedback_id":"test-1","message":"hi",
       "host":"codex","transcript":[{"role":"user","text":"hello","kind":"message"}]}'
```

Visit <http://127.0.0.1:8000/feedback/>, log in, and open the session.

Run the tests with `uv run python manage.py test`.

## Production deploy on an EC2 (Ubuntu) instance

These are the steps; substitute your real hostname for `feedback.example.org`.

1. **Security group:** allow inbound 22 (your IP only), 80, and 443.
2. **DNS:** point `feedback.example.org` (an A record) at the instance's public
   IP. A real hostname is required for HTTPS (Let's Encrypt). A free DuckDNS
   subdomain works if you don't have a domain.
3. **Install system packages:**
   ```bash
   sudo apt update
   sudo apt install -y python3-venv nginx certbot python3-certbot-nginx git
   ```
4. **Get the code and build the venv:**
   ```bash
   sudo mkdir -p /opt/ibl-feedback-server && sudo chown $USER /opt/ibl-feedback-server
   git clone <your-fork-url> /opt/ibl-feedback-server
   cd /opt/ibl-feedback-server
   python3 -m venv .venv
   .venv/bin/pip install -r server/requirements.txt
   ```
5. **Create a service user and data dir:**
   ```bash
   sudo useradd --system --no-create-home iblfeedback
   sudo mkdir -p /var/lib/ibl-feedback && sudo chown iblfeedback /var/lib/ibl-feedback
   ```
6. **Configure secrets** in `/etc/ibl-feedback.env` (copy from
   `server/.env.example`, fill in, then `sudo chmod 600 /etc/ibl-feedback.env`).
   Generate the two secrets with:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # SECRET_KEY
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # INGEST_TOKEN
   ```
7. **Initialize the database, a reviewer login, and static files:**
   ```bash
   cd /opt/ibl-feedback-server/server
   set -a && . /etc/ibl-feedback.env && set +a
   ../.venv/bin/python manage.py migrate
   ../.venv/bin/python manage.py createsuperuser
   ../.venv/bin/python manage.py collectstatic --noinput
   ```
8. **Run via systemd:** install `server/deploy/ibl-feedback.service` to
   `/etc/systemd/system/`, then `sudo systemctl daemon-reload &&
   sudo systemctl enable --now ibl-feedback`.
9. **Reverse proxy + TLS:** install `server/deploy/nginx-ibl-feedback.conf`
   (edit the hostname), enable it, then `sudo certbot --nginx -d
   feedback.example.org`.
10. **Point the client at it** (on each user's machine):
    ```bash
    export IBL_AGENT_FEEDBACK_URL=https://feedback.example.org/api/feedback
    export IBL_AGENT_FEEDBACK_TOKEN=<the INGEST_TOKEN>
    ```

## Reading feedback later

Log in at `https://feedback.example.org/accounts/login/` and browse
`/feedback/`, or use `/admin/` for raw records. Add more reviewers with
`createsuperuser` or from the admin.

## Backups

The whole dataset is the SQLite file (`IBL_FEEDBACK_DB_PATH`). Back it up with
an EBS snapshot or a periodic copy. Move to PostgreSQL later if volume grows
(only `DATABASES` in `settings.py` changes).
