# Architecture map (selftest fixture)

Invented for `selftest.py`, so the campus-pack check runs the same on every machine. It describes no real deployment;
a real context directory sits outside the repository and is never committed.

## Stack
| Layer | Technology |
|---|---|
| Frontend | Next.js |
| API | Node and Express |
| Database | PostgreSQL |
| Auth | JWT bearer tokens |
| Hosting | one container on a VPS |
---

## External Services
| Service | Purpose | Notes |
|---|---|---|
| Mail relay | password resets | rate limited |
| Object store | uploads | private bucket |
| Payments | tuition | test keys in staging |
---
