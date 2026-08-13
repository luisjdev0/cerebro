# Despliegue en VPS

Guía para el despliegue recomendado de plan_v2 §10: VPS de 4 vCPU / 8 GB RAM / 100 GB
SSD con Ubuntu 24.04 (funciona igual en Debian 12). El stack completo son 3 contenedores
(Postgres+pgvector, `cerebro-memory-api` y `cerebro-docs-api`, ecosistema-cerebro.md §8)
y consume < 1.5 GB de RAM en reposo.

## 1. Preparar el VPS (una sola vez)

```bash
# Docker + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# cierra sesión y vuelve a entrar para que el grupo aplique

# Firewall: solo SSH y HTTPS expuestos
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Los puertos de Postgres (5432), `cerebro-memory-api` (8005) y `cerebro-docs-api` (8006)
**no se abren**: `compose.yaml` ya los ata a `127.0.0.1` — solo el reverse proxy los
alcanza.

## 2. Clonar el repo (es privado)

Opción simple con deploy key de solo lectura:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cerebro_deploy -N ""
cat ~/.ssh/cerebro_deploy.pub
# pega esa clave en GitHub: repo cerebro → Settings → Deploy keys → Add (sin write access)

git clone git@github.com:luisjdev0/cerebro.git -c core.sshCommand="ssh -i ~/.ssh/cerebro_deploy"
cd cerebro
git config core.sshCommand "ssh -i ~/.ssh/cerebro_deploy"
```

## 3. Configurar secretos

```bash
cp .env.example .env
# Genera un token root fuerte y ponlo en .env (root para AMBOS servicios — §6):
sed -i "s/^API_TOKEN=.*/API_TOKEN=$(openssl rand -hex 32)/" .env
grep API_TOKEN .env   # guárdalo en tu gestor de contraseñas
```

**Cambia también la contraseña de Postgres.** A diferencia de antes, ya **no** se edita
en `compose.yaml`: se define una sola vez como `POSTGRES_PASSWORD` en `.env` y
`compose.yaml` la interpola en los tres sitios que la necesitan (el propio servicio
`postgres` y el `DATABASE_URL` que se le inyecta a cada API dentro de la red compose —
commit "Despliegue VPS: password de Postgres via .env y puerto host 8005"):

```bash
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 32)/" .env
```

Si omites `POSTGRES_PASSWORD` en `.env`, `docker compose` falla al arrancar con un
error explícito (`define POSTGRES_PASSWORD en .env`) en vez de arrancar con un default
inseguro.

Nota: si ya inicializaste el volumen con la contraseña vieja, cambiarla en `.env` no la
cambia en la base — hazlo antes del primer arranque, o usa `ALTER USER` en psql.

## 4. Levantar el stack

```bash
docker compose --profile full build   # descarga el modelo de embeddings en el build (~5 min la primera vez)
docker compose --profile full up -d
docker compose ps                     # los tres "healthy" (postgres, cerebro-memory-api, cerebro-docs-api)
curl -s localhost:8005/health         # {"status":"ok"}  (cerebro-memory-api)
curl -s localhost:8006/health         # {"status":"ok"}  (cerebro-docs-api)
```

`docker compose up -d` (sin `--profile full`) sigue levantando solo `postgres` — flujo
de día a día sin cambios si corres las APIs fuera de Docker.

## 5. HTTPS con Caddy (reverse proxy)

Necesitas un dominio (o subdominio) por servicio apuntando al VPS. El dominio actual en
uso para memoria es `cerebro.luisjdev.com`; para docs añade un segundo subdominio, por
ejemplo `docs-cerebro.luisjdev.com`. Caddy gestiona ambos certificados Let's Encrypt
automáticamente:

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8005
}

docs-cerebro.luisjdev.com {
    reverse_proxy 127.0.0.1:8006
}
EOF
sudo systemctl reload caddy
curl -s https://cerebro.luisjdev.com/health
curl -s https://docs-cerebro.luisjdev.com/health
```

> **¿Sin dominio?** Alternativa privada: instala [Tailscale](https://tailscale.com) en
> el VPS y en tus máquinas; las APIs quedan accesibles solo dentro de tu tailnet vía
> `http://<ip-tailscale>:8005` y `:8006` sin exponer nada a internet (en ese caso ata
> los puertos de las APIs a la IP de tailscale o usa `tailscale serve`).

## 6. Actualizar un VPS existente a esta versión (monorepo + schemas)

**Solo aplica si tu VPS corre una versión anterior al monorepo** (un único contenedor
`api` sobre el schema `public`). Si es una instalación nueva, sáltate esta sección.

El arranque de `cerebro-memory-api` aplica la migración `005_schema_cerebro_memory.sql`,
que mueve todas sus tablas (`contexts`, `memories`, `audit_log`, `disambiguation_log`,
`context_preferences`, `memory_edges`, `api_tokens`, `schema_migrations`) de `public` al
schema propio `cerebro_memory` (`ALTER TABLE ... SET SCHEMA`, no una copia). Además, el
servicio compose se renombró de `api` a `cerebro-memory-api`. Sigue este orden:

**a) Backup completo obligatorio, antes de tocar nada:**

```bash
cd ~/cerebro
mkdir -p ~/cerebro-backups
docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos \
  | gzip > ~/cerebro-backups/pre-upgrade-$(date +%Y%m%d-%H%M%S).sql.gz
```

No sigas si este comando falla o produce un archivo vacío.

**b) `git pull` y levantar con `--remove-orphans`:**

El rename de servicio (`api` → `cerebro-memory-api`) hace que Docker Compose ya no
reconozca el contenedor viejo `api` como parte del stack — queda huérfano, corriendo
sin que `docker compose up` lo toque, salvo que se lo indiques explícitamente:

```bash
git pull
docker compose --profile full build
docker compose --profile full up -d --remove-orphans
```

Es seguro: el contenedor `api` viejo es stateless (los datos viven en el volumen de
Postgres, no en el contenedor), así que eliminarlo no pierde nada.

**c) Verificación post-arranque:**

```bash
docker compose ps                     # postgres, cerebro-memory-api, cerebro-docs-api: "healthy"; ningún "api" viejo
curl -s localhost:8005/health         # {"status":"ok"}
curl -s localhost:8006/health         # {"status":"ok"}

# Conteos de filas movidas al schema nuevo — deben coincidir con lo que tenías antes
# del upgrade (compáralos contra el backup si tienes dudas):
docker compose exec -T postgres psql -U knowledgeos -d knowledgeos -c "
  SELECT 'memories' AS tabla, count(*) FROM cerebro_memory.memories
  UNION ALL SELECT 'contexts', count(*) FROM cerebro_memory.contexts
  UNION ALL SELECT 'memory_edges', count(*) FROM cerebro_memory.memory_edges
  UNION ALL SELECT 'api_tokens', count(*) FROM cerebro_memory.api_tokens;
"
```

Si algo falla o los conteos no cuadran, restaura desde el backup del paso (a) antes de
seguir usando el sistema.

## 7. Tokens (no uses el root para el día a día)

Desde tu máquina local (el CLI habla con las APIs remotas):

```bash
set CEREBRO_MEMORY_URL=https://cerebro.luisjdev.com
set CEREBRO_DOCS_URL=https://docs-cerebro.luisjdev.com
set CEREBRO_TOKEN=<tu token root, el API_TOKEN de .env>

# Token ESCOPADO a un solo servicio (cerebro-memory):
cerebro memory token create claude-desktop --scopes read,write

# Token TRANSVERSAL: un solo secreto (prefijo cbr_), registrado en AMBOS servicios
# en la misma operación (ecosistema-cerebro.md SS13):
cerebro token create automatizacion-x --scopes read --contexts infraestructura
```

`cerebro token create` (transversal) imprime el secreto **una sola vez**; úsalo como
`CEREBRO_TOKEN` (válido para memory y docs). `cerebro memory token create` genera en
cambio un token válido solo para cerebro-memory. Ambos son revocables: `cerebro token
revoke <nombre>` (transversal, revoca en las dos APIs) o `cerebro memory token revoke
<nombre>` (solo memory).

Si `cerebro token create` falla en un servicio y tiene éxito en el otro (fallo
parcial), el CLI lo reporta explícitamente por servicio y termina con error; reintenta
el mismo comando — reutiliza el mismo secreto de forma segura (no duplica el registro).

**Compatibilidad**: `KNOWLEDGEOS_API_URL`/`KNOWLEDGEOS_API_TOKEN` siguen soportadas
como legado, solo para cerebro-memory, si algún script viejo todavía las usa.

## 8. Conectar tus agentes (MCP local → APIs remotas)

El servidor MCP corre en TU máquina (stdio) y habla con el VPS. Es un único binario
(`cerebro-mcp`, paquete `cerebro-mcp`) que expone las 19 tools de ambos servicios
(`memory_*` y `docs_*`). En `claude_desktop_config.json`:

```json
"cerebro": {
  "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\cerebro-mcp.exe",
  "env": {
    "CEREBRO_MEMORY_URL": "https://cerebro.luisjdev.com",
    "CEREBRO_DOCS_URL": "https://docs-cerebro.luisjdev.com",
    "CEREBRO_TOKEN": "<token transversal del agente, no el root>",
    "CEREBRO_AGENT_NAME": "claude-desktop"
  }
}
```

`CEREBRO_DOCS_URL` no tiene fallback a una URL vieja (cerebro-docs es un servicio
nuevo) — si lo omites, el cliente cae al default de desarrollo local
(`http://localhost:8010`), que no sirve para un VPS remoto. Inclúyelo siempre.

## 9. Backups automáticos (plan_v2 §9 / ecosistema-cerebro.md §9: restore probado o no es backup)

Un solo Postgres compartido significa que un solo `pg_dump` de la instancia cubre
**ambos** schemas (`cerebro_memory` y `cerebro_docs`) completos en una operación — no
hace falta tratamiento especial por servicio.

```bash
mkdir -p ~/cerebro/backups
crontab -e
```

Añade (backup diario 03:15, conserva 14 días; el nombre ya no lleva "knowledgeos" — es
un backup del ecosistema completo):

```
15 3 * * * cd ~/cerebro && docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos | gzip > backups/cerebro-$(date +\%Y\%m\%d).sql.gz && find backups -name '*.sql.gz' -mtime +14 -delete
```

Copia los backups FUERA del VPS (rclone a un bucket/Drive, o un `scp` programado desde
tu máquina). Prueba el restore al menos una vez:

```bash
gunzip -c backups/cerebro-XXXXXXXX.sql.gz | docker compose exec -T postgres psql -U knowledgeos -d knowledgeos_restore_test
```

**Alternativa local**: `cerebro backup` (sin argumentos) hace lo mismo vía el CLI, pero
escribe **fuera del árbol del repo** (`../cerebro-backups/`, hermano de `cerebro/`) con
permisos `0600` en el archivo — pensado para no terminar commiteado por accidente ni
legible por otros usuarios del sistema (los documentos de cerebro-docs no filtran
contenido, así que un dump puede llevar secretos pegados por error). `cerebro restore
<archivo>` hace el restore inverso, con confirmación interactiva salvo `--yes`.

## 10. Actualizar a una versión nueva

```bash
cd ~/cerebro
git pull
docker compose --profile full build
docker compose --profile full up -d --remove-orphans   # las migraciones se aplican solas al arrancar
```

`--remove-orphans` es seguro dejarlo siempre: solo actúa si un `git pull` trajo un
rename o eliminación de servicio en `compose.yaml` (como pasó una vez, §6); si no,
no hace nada.

## Checklist de seguridad final

- [ ] `API_TOKEN` root aleatorio y fuera del repo (solo en `.env` del VPS) — es el root
      de **ambos** servicios
- [ ] `POSTGRES_PASSWORD` cambiado en `.env` (no en `compose.yaml`)
- [ ] `ufw` activo; 5432/8005/8006 NO expuestos (verifica: `ss -tlnp | grep -E '5432|8005|8006'` debe mostrar solo 127.0.0.1)
- [ ] HTTPS funcionando en ambos subdominios (o Tailscale)
- [ ] Agentes usando tokens con scope (transversales o escopados a un servicio), no el root
- [ ] Cron de backup activo y un restore probado
