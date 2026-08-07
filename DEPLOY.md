# Despliegue en VPS

Guía para el despliegue recomendado de plan_v2 §10: VPS de 4 vCPU / 8 GB RAM / 100 GB
SSD con Ubuntu 24.04 (funciona igual en Debian 12). El stack completo son 2 contenedores
(Postgres+pgvector y la API) y consume < 1.5 GB de RAM en reposo.

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

Los puertos de Postgres (5432) y la API (8000) **no se abren**: `compose.yaml` ya los
ata a `127.0.0.1` — solo el reverse proxy los alcanza.

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
# Genera un token root fuerte y ponlo en .env:
sed -i "s/^API_TOKEN=.*/API_TOKEN=$(openssl rand -hex 32)/" .env
grep API_TOKEN .env   # guárdalo en tu gestor de contraseñas
```

**Cambia también la contraseña de Postgres** (el default `knowledgeos` es solo para
dev local). Está en DOS sitios de `compose.yaml` que deben coincidir:

- `services.postgres.environment.POSTGRES_PASSWORD`
- `services.api.environment.DATABASE_URL` (el override para dentro de la red compose)

y en `DATABASE_URL` de tu `.env` (usada por el CLI si lo corres en el host).
Nota: si ya inicializaste el volumen con la contraseña vieja, cambiarla en compose no
la cambia en la base — hazlo antes del primer arranque, o usa `ALTER USER` en psql.

## 4. Levantar el stack

```bash
docker compose --profile full build   # descarga el modelo de embeddings en el build (~5 min la primera vez)
docker compose --profile full up -d
docker compose ps                     # ambos "healthy"
curl -s localhost:8000/health         # {"status":"ok"}
```

## 5. HTTPS con Caddy (reverse proxy)

Necesitas un dominio (o subdominio) apuntando al VPS. Caddy gestiona el certificado
Let's Encrypt automáticamente:

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
memoria.tudominio.com {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl reload caddy
curl -s https://memoria.tudominio.com/health
```

> **¿Sin dominio?** Alternativa privada: instala [Tailscale](https://tailscale.com) en
> el VPS y en tus máquinas; la API queda accesible solo dentro de tu tailnet vía
> `http://<ip-tailscale>:8000` sin exponer nada a internet (en ese caso ata el puerto
> de la API a la IP de tailscale o usa `tailscale serve`).

## 6. Tokens por agente (no uses el root para el día a día)

Desde tu máquina local (el CLI habla con la API remota):

```bash
set KNOWLEDGEOS_API_URL=https://memoria.tudominio.com
set KNOWLEDGEOS_API_TOKEN=<tu token root>
knowledgeos token create claude-desktop --scopes read,write
knowledgeos token create automatizacion-x --scopes read --contexts infraestructura
```

Cada `token create` imprime el token **una sola vez**. Revocables con `token revoke`.

## 7. Conectar tus agentes (MCP local → API remota)

El servidor MCP corre en TU máquina (stdio) y habla con el VPS. En
`claude_desktop_config.json` solo cambia la URL y el token:

```json
"knowledgeos": {
  "command": "D:\\dev\\jobs\\luisjdev\\cerebro\\.venv\\Scripts\\knowledgeos-mcp.exe",
  "env": {
    "KNOWLEDGEOS_API_URL": "https://memoria.tudominio.com",
    "KNOWLEDGEOS_API_TOKEN": "<token del agente, no el root>",
    "KNOWLEDGEOS_AGENT_NAME": "claude-desktop"
  }
}
```

## 8. Backups automáticos (plan_v2 §9: restore probado o no es backup)

```bash
mkdir -p ~/cerebro/backups
crontab -e
```

Añade (backup diario 03:15, conserva 14 días):

```
15 3 * * * cd ~/cerebro && docker compose exec -T postgres pg_dump -U knowledgeos knowledgeos | gzip > backups/knowledgeos-$(date +\%Y\%m\%d).sql.gz && find backups -name '*.sql.gz' -mtime +14 -delete
```

Copia los backups FUERA del VPS (rclone a un bucket/Drive, o un `scp` programado desde
tu máquina). Prueba el restore al menos una vez:

```bash
gunzip -c backups/knowledgeos-XXXXXXXX.sql.gz | docker compose exec -T postgres psql -U knowledgeos -d knowledgeos_restore_test
```

## 9. Actualizar a una versión nueva

```bash
cd ~/cerebro
git pull
docker compose --profile full build
docker compose --profile full up -d   # las migraciones se aplican solas al arrancar
```

## Checklist de seguridad final

- [ ] `API_TOKEN` root aleatorio y fuera del repo (solo en `.env` del VPS)
- [ ] Contraseña de Postgres cambiada
- [ ] `ufw` activo; 5432/8000 NO expuestos (verifica: `ss -tlnp | grep -E '5432|8000'` debe mostrar solo 127.0.0.1)
- [ ] HTTPS funcionando (o Tailscale)
- [ ] Agentes usando tokens con scope, no el root
- [ ] Cron de backup activo y un restore probado
