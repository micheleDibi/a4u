# Seed iniziale del database (`db_seed.sql.gz`)

Cartella per i dump del DB usati come **seed iniziale** in fase di
deploy. I file `*.sql`, `*.sql.gz`, `*.dump` qui dentro sono **gitignorati**:
contengono password hashate ed email degli utenti — vanno trasferiti
manualmente al server (via `scp`/`rsync`), non commitati.

## Generare un dump dal DB locale

Il container Postgres locale (`a4u-postgres`) deve essere running
(`docker compose up -d postgres`).

```bash
# Dump compresso plaintext SQL (più portabile, leggibile in caso di debug)
docker exec a4u-postgres pg_dump -U a4u -d a4u \
    --clean --if-exists --no-owner --no-privileges \
    | gzip > deploy/seed/db_seed.sql.gz
```

Note sui flag:
- `--clean --if-exists`: il dump inizia con `DROP TABLE` per ogni
  oggetto, così il restore può applicarlo a un DB esistente senza errori.
- `--no-owner --no-privileges`: rimuove `ALTER ... OWNER TO` e GRANT,
  così il dump è portabile fra DB con utenti diversi (locale `a4u`,
  produzione magari `a4u_prod`).

## Caricare il dump sul server di produzione

Vedi `docs/07-deployment.md` § "Seeding iniziale del DB".

Riepilogo:

```bash
# 1. Trasferisci il dump
scp deploy/seed/db_seed.sql.gz user@server:/tmp/

# 2. Sul server, dopo che lo stack è avviato e migrato:
ssh user@server
cd /path/to/a4u
gunzip -c /tmp/db_seed.sql.gz | docker compose exec -T postgres psql -U a4u -d a4u
```
