# Security policy

Karvon stores personal data (driver names, passport numbers, phone numbers) and
payment records, so we take security reports seriously.

## Supported versions

Karvon is in alpha. Security fixes go to the latest release and `main`.

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅        |
| 0.3.x   | ❌ (upgrade to 0.4, no breaking changes) |
| 0.2.x   | ❌ (upgrade to 0.4, no breaking changes) |
| 0.1.x   | ❌ (no login; upgrade to 0.2) |

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through
[GitHub's vulnerability reporting](https://github.com/Khan-ipak/karvon/security/advisories/new)
(Security tab → "Report a vulnerability").

Include what you found, how to reproduce it, and what an attacker could do with it.
You'll get a reply within 7 days. Once a fix is released, we'll credit you in the
advisory unless you prefer to stay anonymous.

## Deploying safely

Version 0.1.0 had no login. Later versions have users and roles; to run one safely:

- set `KARVON_SEED_DEMO=0`, otherwise demo users with password = username exist;
- create the first admin with `KARVON_ADMIN_PASSWORD` or `python -m karvon.users`;
- serve it over HTTPS (a reverse proxy is fine), so the session cookie is sent
  only over TLS;
- change the default database password in `docker-compose.yml`.

There is no login rate limit yet, so prefer a closed network or VPN for now.
