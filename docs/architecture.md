# Karvon architecture

Karvon is a Terminal Operating System (TOS) for **land freight terminals**:
dry ports, border truck terminals, TIR parks and cross-dock warehouses.

This document describes the v0.1 domain model and the main visit lifecycle.
Diagrams live next to it in [`architecture.puml`](architecture.puml).

## Design goals

1. **One command to run.** `docker compose up` gives a working terminal with demo data.
2. **Process first.** The truck visit is the central object. Everything else
   (yard, operations, weighing, billing) hangs off a visit.
3. **Configurable, not hard-coded.** Operation types, tariffs and yard layout are data.
4. **Multilingual from day one.** EN / RU / UZ out of the box; other languages are JSON files.
5. **Boring tech.** Python, FastAPI, SQLAlchemy, PostgreSQL, server-rendered HTML forms.
   No build step for the frontend, so contributors can start in minutes.

## Modules

| Module      | Responsibility                                                     |
|-------------|--------------------------------------------------------------------|
| `gate`      | Vehicle arrival, driver and document registration, exit pass       |
| `yard`      | Parking zones and slots, assigning and releasing slots             |
| `operations`| Direct transfer (truck → truck), warehouse unload, warehouse load  |
| `warehouse` | Stock lots per owner, created by unloads, consumed by loads        |
| `weighbridge`| Weigh tickets (gross / tare / net)                                |
| `billing`   | Tariffs, charges per visit, payment                                |
| `documents` | CMR consignment note and invoice as PDF                            |

## Domain model

```
Vehicle 1───* Visit *───1 Driver
                │
                ├──*    AuditEvent ──0..1 User (who did it)
                ├──0..1 YardSlot (current parking place)
                ├──*    Operation ──*  StockLot (warehouse movements)
                ├──*    WeighTicket
                └──*    Charge
Tariff  (code → price, unit)
```

### Visit lifecycle

```
registered ──park──▶ parked ──start op──▶ in_operation ──complete op──▶ parked
     │                  │                                                 │
     └──────────────────┴────────────── request exit ─────────────────────┘
                                             │
                                     awaiting_payment ──pay──▶ cleared ──exit──▶ exited
```

Exit rules (enforced in `karvon.services`):

- every operation of the visit is `completed` or `cancelled`;
- every completed operation has customs cleared;
- every charge of the visit is paid.

### Operation types

| Code               | Meaning                                         | Warehouse effect          |
|--------------------|-------------------------------------------------|---------------------------|
| `direct_transfer`  | Goods move from this truck to another truck     | none                      |
| `warehouse_unload` | Goods are unloaded into the warehouse           | creates a `StockLot`      |
| `warehouse_load`   | Goods from the warehouse are loaded on the truck| decreases a `StockLot`    |

Customs clearance is a flag on the operation and can be set **before or after**
the physical work, matching how real border terminals work.

## Users, roles and audit

`karvon/auth.py` maps each permission to the roles that have it
(`PERMISSIONS`); `admin` has all of them. The API checks permissions with a
dependency per route, the UI checks them in the action handlers and hides
buttons the user can't use. Viewing needs only a login.

Logins are server-side sessions: the cookie holds a random token and the
`user_sessions` table keeps its SHA-256. Deactivating a user or resetting a
password ends that user's sessions. Passwords are hashed with scrypt.

Every state change in `services.py` calls `audit()`, which writes an
`AuditEvent` with the user taken from `session.info["actor"]`. Changes made
without a user (seed, scripts) are recorded as `system`.

## Layers

```
karvon/
  api.py      JSON REST API (FastAPI router), documented at /docs
  auth.py     Users, passwords, login sessions, permissions
  reports.py  Throughput, dwell time and revenue for a period
  documents.py CMR and invoice PDFs (fpdf2, bundled DejaVu font in fonts/)
  scale.py    Weighbridge indicator over TCP / RS-232, its output parser, a simulator
  web.py      HTML UI (Jinja2 templates in templates/), uses the same services
  services.py Business rules and state transitions. No HTTP here.
  models.py   SQLAlchemy models
  migrations/ Alembic migrations, applied on startup by migrate.py
  schemas.py  Pydantic request/response schemas
  i18n/       Translation files
  seed.py     Demo terminal
```

Both the API and the UI call `services`. Rules live in one place.

## Roadmap after v0.1

- ANPR camera integration at the gate
