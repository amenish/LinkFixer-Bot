## 🔗 LinkFixer Bot

Bot de Telegram que detecta enlaces de ciertos dominios y los transforma automáticamente:

- \*\*fixup\*\* → Reemplaza \`x.com\` / \`twitter.com\` por \`i.fixupx.com\`
- \*\*unwall\*\* → Añade el prefijo \`[https://unwall.app/\\\`](https://unwall.app/%5C%60) y genera una vista previa con título, imagen y medio

El bot puede funcionar en **modo privado** (con control de acceso por invitación) o **modo público** (abierto a cualquier usuario).

* * *

## 📋 Características

- 🔄 Transformación automática de enlaces según reglas definidas en un fichero
- 📰 Scrapeo de metadatos (\`og:title\`, \`og:image\`, \`og:site_name\`) para acción \`unwall\`
- 🖼️ Redimensión de imágenes a preview de 640×480
- 🔐 \*\*Modo privado\*\* con autenticación por invitación de 24h (deep linking)
- 👑 Panel de administración para el propietario (\`/invite\`, \`/auth\`, \`/users\`)
- 🗂️ Reglas de dominios gestionables en caliente (\`/add\`, \`/reload\`)
- 🐳 Despliegue 100% containerizado con Docker

* * *

## 🛠️ Requisitos

- \[Docker\](https://docs.docker.com/get-docker/) y Docker Compose
- Un bot de Telegram (ver siguiente sección)
- (Opcional) Un registro de imágenes Docker propio si despliegas en múltiples nodos

* * *

## 🤖 Crear un bot en Telegram

1.  Abre Telegram y busca \[\*\*@BotFather\*\*\](https://t.me/botfather)
2.  Envía \`/newbot\`
3.  Sigue las instrucciones: elige un nombre y un username (debe terminar en \`bot\`)
4.  \*\*Guarda el token\*\* que te proporciona BotFather (\`123456789:ABCdef...\`)
5.  Anota también el \*\*username\*\* de tu bot (ej. \`@MiLinkFixerBot\`)

> 💡 \*\*Consejo\*\*: para obtener tu \`OWNER_ID\` (tu ID numérico de Telegram), habla con \[@userinfobot\](https://t.me/userinfobot).

* * *

## ⚙️ Configuración

### Variables de entorno

Crea un fichero \`.env\` en la raíz del proyecto:

```bash
touch domains authorized_users invite_codes
chmod 666 domains authorized_users invite_codes
```


### Variable obligatoria:

Tu bot creado con BotFather

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxyz
```

## Modo privado (opcional pero recomendado)

### *Si se define, el bot requiere registro. Solo el OWNER puede gestionar accesos.*

```env
OWNER_ID=123456789
```

Modo público: omite OWNER_ID. Cualquiera podrá usar el bot. Los comandos admin (/invite, /auth, /users) quedarán desactivados.  
Modo privado: OWNER_ID es obligatoria. Solo el owner y los usuarios autorizados pueden interactuar.

## Opcionales (valores por defecto mostrados)

```env
DOMAINS_FILE=/app/domains
AUTH_FILE=/app/authorized_users
INVITE_FILE=/app/invite_codes
```

## Ficheros de datos

### Crea estos ficheros en el host (el bot los monta por volumen):

```bash
touch domains authorized_users invite_codes
chmod 666 domains authorized_users invite_codes
```


| Fichero | Descripción |
| :--- | :--- |
| `domains` | Reglas `dominio;acción` (una por línea). Acciones: `fixup`, `unwall` |
| `authorized_users` | IDs numéricos de usuarios autorizados (uno por línea). Auto-gestionado. |
| `invite_codes` | Códigos de invitación con timestamp de caducidad. Auto-gestionado. |

Ejemplo de domains:

```env
# dominio;accion
x.com;fixup
twitter.com;fixup
nytimes.com;unwall
```

* * *

## 🐳 Despliegue con Docker (standalone)

### Opción A: Docker Compose (recomendada)

```bash
# 1. Clona el repositorio
git clone https://github.com/amenish/linkfixer-bot.git
cd linkfixer-bot

# 2. Configura el entorno
cp .env.example .env
# Edita .env con tu TOKEN y OWNER_ID

# 3. Crea los ficheros de datos
touch domains authorized_users invite_codes
chmod 666 domains authorized_users invite_codes

# 4. Construye y levanta
docker compose up -d --build

# 5. Ver logs
docker compose logs -f
```

### Opción B: docker run

```bash
docker build -t linkfixer-bot:latest .

docker run -d \
  --name linkfixer-bot \
  --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  -e OWNER_ID="${OWNER_ID}" \
  -v "$(pwd)/domains:/app/domains" \
  -v "$(pwd)/authorized_users:/app/authorized_users" \
  -v "$(pwd)/invite_codes:/app/invite_codes" \
  linkfixer-bot:latest
```

### Opción C: Docker Swarm

```bash
docker build -t linkfixer-bot:latest .
docker stack deploy -c docker-compose.yml linkfixer-bot
```

* * *

## 🚀 Uso del bot

### Comandos disponibles

| Comando | Disponible para | Descripción |
| :--- | :--- | :--- |
| `/start` | Todos | Inicia el bot o completa el registro con código de invitación |
| `/add <dominio>;<acción>` | Usuarios autorizados | Añade una nueva regla de dominio |
| `/reload` | Usuarios autorizados | Recarga el fichero `domains` y muestra la lista actual |
| `/invite` | Solo Owner | Genera un enlace de invitación de un solo uso (válido 24h) |
| `/auth <user_id>` | Solo Owner | Autoriza manualmente a un usuario por su ID numérico |
| `/users` | Solo Owner | Lista todos los usuarios autorizados |

### Flujo en modo privado

1.  **Owner** ejecuta `/invite` → recibe un enlace único:
    
    ```plain
    https://t.me/MiLinkFixerBot?start=ABC123XYZ
    ```
    
2.  **Nuevo usuario** pulsa el enlace → Telegram abre el chat y envía `/start ABC123XYZ`
    
3.  El bot valida el código, registra al usuario, le asigna menú de usuario y notifica al owner
    
4.  El código queda invalidado (un solo uso) y caduca a las 24h si no se usa
    

### Flujo en modo público

1.  No se define `OWNER_ID`
    
2.  Cualquier usuario puede usar `/start`, `/add` y `/reload`
    
3.  Los comandos `/invite`, `/auth` y `/users` están desactivados
    

### 🧪 Prueba rápida

**Modo público** (`.env` sin `OWNER_ID`):

```bash
docker run -d \
  -e TELEGRAM_BOT_TOKEN="tu_token" \
  -v "$(pwd)/domains:/app/domains" \
  linkfixer-bot
```

→ Cualquiera puede escribirle y usar `/add`.

**Modo privado** (`.env` con `OWNER_ID`):

```bash
docker run -d \
  -e TELEGRAM_BOT_TOKEN="tu_token" \
  -e OWNER_ID="123456789" \
  -v "$(pwd)/domains:/app/domains" \
  -v "$(pwd)/authorized_users:/app/authorized_users" \
  -v "$(pwd)/invite_codes:/app/invite_codes" \
  linkfixer-bot
```

→ Solo tú y quienes invites pueden usarlo.

### Ejemplo de interacción

**Usuario envía:**

```plain
https://www.nytimes.com/2026/08/21/world/canada/trump-tariffs-trade-no-deal-carney-canada.html
```

**Bot responde** (y borra el mensaje original):

```plain
ELMUNDO: El BCE mantiene los tipos de interés en el 3,5%

https://unwall.app/https://www.nytimes.com/2026/08/21/world/canada/trump-tariffs-trade-no-deal-carney-canada.html
```

Acompañado de la imagen de la noticia redimensionada.

* * *

## 📁 Estructura del proyecto

```plain
.
├── bot.py              # Código principal del bot
├── Dockerfile          # Imagen Docker
├── docker-compose.yml  # Orquestación standalone/Swarm
├── requirements.txt    # Dependencias Python
├── domains             # Reglas de dominios (bind mount)
├── authorized_users    # Usuarios autorizados (bind mount)
├── invite_codes        # Códigos de invitación (bind mount)
├── .env                # Variables de entorno (no subir a git)
└── README.md           # Este fichero
```

* * *

## 🧪 Desarrollo local (sin Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Crea los ficheros de datos
touch domains authorized_users invite_codes

# Ejecuta
python bot.py
```

* * *

## 🔒 Seguridad

- En modo privado, los usuarios no autorizados reciben `⛔ No tienes permiso...`
    
- Los códigos de invitación son de **un solo uso** y **caducan a las 24h**
    
- Los ficheros de datos se montan como volúmenes; no están embebidos en la imagen
    
- El bot no almacena conversaciones ni contenido de los mensajes
    

* * *
## ⚠️ Aviso legal

Este bot es una utilidad de transformación de URLs con fines educativos. 
No aloja, reproduce ni distribuye contenido protegido por copyright.

El usuario es responsable de:
- Cumplir con los términos de servicio de los sitios web enlazados.
- Respetar la legislación aplicable en su jurisdicción sobre propiedad intelectual.

El autor no se hace responsable del uso indebido de esta herramienta.
* * *
## 📄 Licencia

MIT

* * *

## 🙋 Soporte

Si encuentras un bug o tienes una idea, abre un [Issue](https://github.com/tuusuario/linkfixer-bot/issues) o un Pull Request.
