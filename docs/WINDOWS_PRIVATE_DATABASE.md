# Windows Private Database Kernel

LightHouse 0.7.1 no longer asks ordinary Windows users for an existing PostgreSQL `postgres` password.

The Windows installer uses PostgreSQL 16 command-line tools to initialize a private cluster under:

```text
%USERPROFILE%\.lighthouse\postgres\data
```

The cluster:

- runs only on `127.0.0.1`;
- uses a generated `lighthouse` credential;
- owns only the private `lighthouse` database;
- records its managed port and paths in the current-user protected LightHouse config;
- is started before the LightHouse API by the current-user Scheduled Task;
- does not modify existing PostgreSQL roles, databases, authentication rules or services.

If an explicit `database_url` already exists and is not marked as managed, the installer preserves it instead of creating a private cluster.

The uninstaller stops only a database marked with `database_managed=true`. External PostgreSQL installations are left untouched.
