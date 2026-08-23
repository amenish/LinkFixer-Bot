# 🔗 URLMorph Bot

Bot de Telegram genérico para transformar automáticamente URLs según reglas configurables.

Soporta **reglas públicas** (visibles para todos) y **reglas privadas** (personales de cada usuario). Los usuarios pueden promover sus reglas privadas para que el administrador las apruebe y pasen a ser públicas.

## Métodos de transformación

- **Método 1** → Reemplaza el dominio de la URL por otro indicado.
- **Método 2** → Añade un prefijo delante de la URL completa.

El bot puede funcionar en **modo privado** (con control de acceso por invitación) o **modo público** (abierto a cualquier usuario).

---

## 📋 Características

- 🔄 Transformación automática de URLs según reglas públicas y privadas
- 👤 Reglas privadas por usuario (solo el propietario las ve y usa)
- 🌐 Reglas públicas compartidas entre todos los usuarios
- 📤 Sistema de promoción: los usuarios envían reglas privadas a revisión del admin
- ✅ Panel de aprobación para el propietario (`/pending`, `/approve`, `/reject`)
- 📰 Para el método 2, genera vista previa con título, imagen y medio
- 🖼️ Redimensión de imágenes de preview a 640×480
- 🔐 **Modo privado** con autenticación por invitación de 24h
- 👑 Panel de administración para el propietario
- 🗂️ Reglas gestionables en caliente sin reconstruir el contenedor
- 🐳 Despliegue 100% containerizado con Docker

---

## 🛠️ Requisitos

- [Docker](https://docs.docker.com/get-docker/) y Docker Compose
- Un bot de Telegram (ver siguiente sección)
- (Opcional) Un registro de imágenes Docker propio si despliegas en múltiples nodos

---

## 🤖 Crear un bot en Telegram

1. Abre Telegram y busca [**@BotFather**](https://t.me/botfather)
2. Envía `/newbot`
3. Sigue las instrucciones: elige un nombre y un username (debe terminar en `bot`)
4. **Guarda el token** que te proporciona BotFather (`123456789:ABCdef...`)
5. Anota también el **username** de tu bot (ej. `@URLMorphBot`)

> 💡 **Consejo**: para obtener tu `OWNER_ID` (tu ID numérico de Telegram), habla con [@userinfobot](https://t.me/userinfobot).

---

## ⚙️ Configuración

### Variables de entorno

Crea un fichero `.env` en la raíz del proyecto:

```bash
# Obligatoria
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz

# Modo privado (opcional pero recomendado)
# Si se define, el bot requiere registro. Solo el OWNER puede gestionar accesos.
OWNER_ID=123456789

# Opcionales (valores por defecto mostrados)
DOMAINS_FILE=/app/domains
USER_DOMAINS_FILE=/app/user_domains
PENDING_FILE=/app/pending_domains
AUTH_FILE=/app/authorized_users
INVITE_FILE=/app/invite_codes
```

> **Modo público**: omite `OWNER_ID`. Cualquiera podrá usar el bot. Los comandos admin quedarán desactivados.
>
> **Modo privado**: `OWNER_ID` es obligatoria. Solo el owner y los usuarios autorizados pueden interactuar.

### Ficheros de datos

Crea estos ficheros en el host (el bot los monta por volumen):

```bash
touch domains user_domains pending_domains authorized_users invite_codes
chmod 666 domains user_domains pending_domains authorized_users invite_codes
```

| Fichero | Descripción |
|---------|-------------|
| `domains` | Reglas públicas `dominio;método;modificación` (una por línea) |
| `user_domains` | Reglas privadas `user_id;dominio;método;modificación`. Auto-gestionado. |
| `pending_domains` | Reglas pendientes `user_id;dominio;método;modificación`. Auto-gestionado. |
| `authorized_users` | IDs numéricos de usuarios autorizados (uno por línea). Auto-gestionado. |
| `invite_codes` | Códigos de invitación con timestamp de caducidad. Auto-gestionado. |

#### Formato del fichero `domains` (públicas)

```text
# Formato: dominio;método;modificación
# Método 1: reemplaza el dominio por la modificación indicada
# Método 2: añade la modificación como prefijo a la URL completa

x.com;1;i.fixupx.com
twitter.com;1;i.fixupx.com
example.com;2;https://prefix.example.com/
```

---

## 🐳 Despliegue con Docker (standalone)

### Opción A: Docker Compose (recomendada)

```bash
# 1. Clona el repositorio
git clone https://github.com/tuusuario/urlmorph-bot.git
cd urlmorph-bot

# 2. Configura el entorno
cp .env.example .env
# Edita .env con tu TOKEN y OWNER_ID

# 3. Crea los ficheros de datos
touch domains user_domains pending_domains authorized_users invite_codes
chmod 666 domains user_domains pending_domains authorized_users invite_codes

# 4. Construye y levanta
docker compose up -d --build

# 5. Ver logs
docker compose logs -f
```

### Opción B: docker run

```bash
docker build -t urlmorph-bot:latest .

docker run -d \
  --name urlmorph-bot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  -e OWNER_ID="${OWNER_ID}" \
  -v "$(pwd)/domains:/app/domains" \
  -v "$(pwd)/user_domains:/app/user_domains" \
  -v "$(pwd)/pending_domains:/app/pending_domains" \
  -v "$(pwd)/authorized_users:/app/authorized_users" \
  -v "$(pwd)/invite_codes:/app/invite_codes" \
  urlmorph-bot:latest
```

### Opción C: Docker Swarm

```bash
docker build -t urlmorph-bot:latest .
docker stack deploy -c docker-compose.yml urlmorph-bot
```

> Asegúrate de que la imagen esté disponible en todos los nodos (registro o `docker service create --with-registry-auth`).

---

## 🚀 Uso del bot

### Comandos disponibles

#### Generales

| Comando | Disponible para | Descripción |
|---------|-----------------|-------------|
| `/start` | Todos | Inicia el bot o completa el registro con código de invitación |
| `/reload` | Usuarios autorizados | Recarga todos los ficheros y muestra las reglas públicas |

#### Reglas públicas (admin/owner)

| Comando | Disponible para | Descripción |
|---------|-----------------|-------------|
| `/add <dominio>;<método>;<modificación>` | Owner | Añade una regla pública |
| `/remove <dominio>` | Owner | Elimina una regla pública |

#### Reglas privadas (cada usuario)

| Comando | Disponible para | Descripción |
|---------|-----------------|-------------|
| `/myadd <dominio>;<método>;<modificación>` | Usuarios autorizados | Añade una regla privada personal |
| `/myremove <dominio>` | Usuarios autorizados | Elimina una regla privada |
| `/mylist` | Usuarios autorizados | Lista tus reglas privadas |
| `/promote <dominio>` | Usuarios autorizados | Envía una regla privada a revisión del admin para hacerla pública |

#### Gestión de promociones (owner)

| Comando | Disponible para | Descripción |
|---------|-----------------|-------------|
| `/pending` | Owner | Lista las reglas pendientes de aprobación |
| `/approve <dominio>` | Owner | Aprueba una regla pendiente (pasa a pública) |
| `/reject <dominio>` | Owner | Rechaza una regla pendiente |

#### Gestión de usuarios (owner, modo privado)

| Comando | Disponible para | Descripción |
|---------|-----------------|-------------|
| `/invite` | Owner | Genera un enlace de invitación de un solo uso (válido 24h) |
| `/auth <user_id>` | Owner | Autoriza manualmente a un usuario |
| `/users` | Owner | Lista todos los usuarios autorizados |

### Flujo en modo privado

1. **Owner** ejecuta `/invite` → recibe un enlace único:
   ```
   https://t.me/URLMorphBot?start=ABC123XYZ
   ```
2. **Nuevo usuario** pulsa el enlace → Telegram abre el chat y envía `/start ABC123XYZ`
3. El bot valida el código, registra al usuario, le asigna menú de usuario y notifica al owner
4. El código queda invalidado (un solo uso) y caduca a las 24h si no se usa

### Flujo de promoción de reglas

1. **Usuario** crea una regla privada con `/myadd x.com;1;i.fixupx.com`
2. El usuario ejecuta `/promote x.com` → la regla pasa a estado **pendiente**
3. El **owner** recibe una notificación y ejecuta `/pending` para verla
4. El owner ejecuta `/approve x.com` → la regla pasa a **pública** y se notifica al usuario
5. Si el owner ejecuta `/reject x.com` → la regla se elimina de pendientes y se notifica al usuario

### Flujo en modo público

1. No se define `OWNER_ID`
2. Cualquier usuario puede usar `/start`, `/add`, `/remove`, `/reload`, `/myadd`, `/myremove`, `/mylist`, `/promote`
3. Los comandos `/invite`, `/auth`, `/users`, `/pending`, `/approve`, `/reject` están desactivados

### Ejemplo de interacción

**Usuario envía:**
```
Mira este enlace https://x.com/usuario/status/1234567890
```

**Bot responde** (y borra el mensaje original):
```
https://i.fixupx.com/usuario/status/1234567890
```

**Otro ejemplo con método 2:**

**Usuario envía:**
```
Artículo interesante https://example.com/noticia/123
```

**Bot responde** (con preview):
```
Example: Título del artículo

https://prefix.example.com/https://example.com/noticia/123
```
Acompañado de la imagen de preview redimensionada.

---

## 📁 Estructura del proyecto

```
.
├── bot.py              # Código principal del bot
├── Dockerfile          # Imagen Docker
├── docker-compose.yml  # Orquestación standalone/Swarm
├── requirements.txt    # Dependencias Python
├── domains             # Reglas públicas (bind mount)
├── user_domains        # Reglas privadas por usuario (bind mount)
├── pending_domains     # Reglas pendientes de aprobación (bind mount)
├── authorized_users    # Usuarios autorizados (bind mount)
├── invite_codes        # Códigos de invitación (bind mount)
├── .env                # Variables de entorno (no subir a git)
└── README.md           # Este fichero
```

---

## 🧪 Desarrollo local (sin Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Crea los ficheros de datos
touch domains user_domains pending_domains authorized_users invite_codes

# Ejecuta
python bot.py
```

---

## 🔒 Seguridad

- En modo privado, los usuarios no autorizados reciben `⛔ No tienes permiso...`
- Los códigos de invitación son de **un solo uso** y **caducan a las 24h**
- Los ficheros de datos se montan como volúmenes; no están embebidos en la imagen
- El bot no almacena conversaciones ni contenido de los mensajes
- Las reglas privadas de un usuario nunca son visibles para otros usuarios

---

## ⚠️ Aviso legal

Este bot es una utilidad de transformación de URLs con fines educativos. No aloja, reproduce ni distribuye contenido protegido por copyright.

El usuario es responsable de:
- Cumplir con los términos de servicio de los sitios web enlazados.
- Respetar la legislación aplicable en su jurisdicción sobre propiedad intelectual.

El autor no se hace responsable del uso indebido de esta herramienta.

---

## 📄 Licencia

MIT

---

## 🙋 Soporte

Si encuentras un bug o tienes una idea, abre un [Issue](https://github.com/tuusuario/urlmorph-bot/issues) o un Pull Request.
