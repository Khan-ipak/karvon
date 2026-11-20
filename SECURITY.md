# Security policy

Karvon stores personal data (driver names, passport numbers, phone numbers) and
payment records, so we take security reports seriously.

## Supported versions

Karvon is in alpha. Security fixes go to the latest release and `main`.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through
[GitHub's vulnerability reporting](https://github.com/Khan-ipak/karvon/security/advisories/new)
(Security tab → "Report a vulnerability").

Include what you found, how to reproduce it, and what an attacker could do with it.
You'll get a reply within 7 days. Once a fix is released, we'll credit you in the
advisory unless you prefer to stay anonymous.

## Deploying safely

Version 0.1 has **no login yet** (see [#10](https://github.com/Khan-ipak/karvon/issues/10)).
Don't expose it to the internet. Run it on a closed local network or behind a VPN
or a reverse proxy with authentication, and change the default database password
in `docker-compose.yml`.
