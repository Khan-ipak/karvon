# Changelog

All notable changes to Karvon. Dates are UTC.

## [Unreleased]

### Added

- `KARVON_TIMEZONE`: the terminal's time zone. The UI shows local times and
  reports count days in it; the API and CSV stay in UTC.
- Public demo mode (`KARVON_PUBLIC_DEMO`) and a Render blueprint with a
  "Deploy to Render" button.
- Russian README.
- Dashboard column "On site": how long each truck has been at the terminal,
  highlighted after 48 hours (#19, based on work by @RiccardoBifulco).

### Fixed

- Plain `docker run` without PostgreSQL failed to write the SQLite file.
- Login cookie is marked `Secure` behind an HTTPS reverse proxy.

## [0.2.0] - 2026-11-23

### Added

- **Users, roles and an audit log** (#10). Every action needs a login; roles
  `operator`, `yard`, `customs`, `warehouse`, `accountant` and `admin` each do
  their own part of the process. Every visit shows who did what and when.
  The API accepts the session cookie or HTTP Basic auth. Admins manage users
  in the UI or with `python -m karvon.users`.
- **Reports**: arrivals and departures per day, time on site, completed
  operations in tonnes, charges by service (`/reports`, `GET /api/v1/reports`).
- **Yard map**: slots coloured by status with plate and days on site,
  occupancy per zone, click a free slot to park or move a truck.
- **CSV export** of visits with date filters (#8), by @AsuraGhost.
- Printable **exit pass** for cleared visits.
- **Search** on-site vehicles by plate number.
- Translated tariff names in visit charges.
- **Database migrations** with Alembic, applied on startup.
- Demo data: one user per role and trucks that already came and left.

### Changed

- Docker image and CI on Python 3.15; CI also tests 3.12 to 3.14.

### Upgrading from 0.1.0

Back up the database, then start 0.2.0: migrations run automatically and your
data is kept. **0.2.0 requires a login.** An existing database gets no demo
users, so create the first admin:

```bash
KARVON_ADMIN_PASSWORD='a long password' docker compose up    # or
python -m karvon.users create admin --role admin
```

Set `KARVON_SEED_DEMO=0` on a real terminal.

## [0.1.0] - 2026-11-20

First public release: gate in/out, yard, direct transfer and warehouse
operations, stock, customs flag, weighbridge, tariffs and billing, EN/RU/UZ UI,
REST API.

[Unreleased]: https://github.com/Khan-ipak/karvon/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Khan-ipak/karvon/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Khan-ipak/karvon/releases/tag/v0.1.0
