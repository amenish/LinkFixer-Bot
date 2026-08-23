import os
import re
import io
import time
import secrets
import logging
import html
from urllib.parse import urlparse
from telegram import (
    Update, BotCommand,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters
)
from telegram.error import BadRequest
import aiohttp
from bs4 import BeautifulSoup
from PIL import Image

# ─── Configuración ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DOMAINS_FILE = os.getenv("DOMAINS_FILE", "/app/domains")
USER_DOMAINS_FILE = os.getenv("USER_DOMAINS_FILE", "/app/user_domains")
PENDING_FILE = os.getenv("PENDING_FILE", "/app/pending_domains")
AUTH_FILE = os.getenv("AUTH_FILE", "/app/authorized_users")
INVITE_FILE = os.getenv("INVITE_FILE", "/app/invite_codes")
OWNER_ID = os.getenv("OWNER_ID", "").strip()
INVITE_TTL_SECONDS = 24 * 60 * 60

# Modo privado: variable explícita PRIVATE_MODE tiene prioridad absoluta.
_private_mode_env = os.getenv("PRIVATE_MODE", "").strip().lower()
if _private_mode_env in ("true", "1", "yes"):
    PRIVATE_MODE = True
elif _private_mode_env in ("false", "0", "no"):
    PRIVATE_MODE = False
elif _private_mode_env == "" and OWNER_ID:
    PRIVATE_MODE = True
else:
    PRIVATE_MODE = False

if PRIVATE_MODE:
    logger.info("🔒 Modo PRIVADO activado.")
    if not OWNER_ID:
        logger.warning("⚠️ Modo privado activo pero OWNER_ID no está definido. Los comandos admin no funcionarán.")
else:
    logger.info("🌐 Modo PÚBLICO activado.")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

domain_actions = {}
user_domain_actions = {}
pending_domains = []
authorized_users = set()

DOMAIN_VALID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://\S+")

# ─── Comandos por rol ────────────────────────────────────────────
COMMANDS_PUBLIC = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("list", "Ver reglas públicas"),
    BotCommand("add", "Añadir regla pública"),
    BotCommand("remove", "Eliminar regla pública"),
    BotCommand("reload", "Recargar y listar reglas"),
    BotCommand("myadd", "Añadir regla privada"),
    BotCommand("myremove", "Eliminar regla privada"),
    BotCommand("mylist", "Listar reglas privadas"),
    BotCommand("promote", "Promover regla a pública"),
]
COMMANDS_PRIVATE_NONE = [
    BotCommand("start", "Iniciar o registrarse"),
]
COMMANDS_PRIVATE_USER = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("list", "Ver reglas públicas"),
    BotCommand("add", "Añadir regla pública"),
    BotCommand("remove", "Eliminar regla pública"),
    BotCommand("reload", "Recargar y listar reglas"),
    BotCommand("myadd", "Añadir regla privada"),
    BotCommand("myremove", "Eliminar regla privada"),
    BotCommand("mylist", "Listar reglas privadas"),
    BotCommand("promote", "Promover regla a pública"),
]
COMMANDS_PRIVATE_OWNER = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("list", "Ver reglas públicas"),
    BotCommand("add", "Añadir regla pública"),
    BotCommand("remove", "Eliminar regla pública"),
    BotCommand("reload", "Recargar y listar reglas"),
    BotCommand("myadd", "Añadir regla privada"),
    BotCommand("myremove", "Eliminar regla privada"),
    BotCommand("mylist", "Listar reglas privadas"),
    BotCommand("promote", "Promover regla a pública"),
    BotCommand("pending", "Ver reglas pendientes"),
    BotCommand("approve", "Aprobar regla pendiente"),
    BotCommand("reject", "Rechazar regla pendiente"),
    BotCommand("invite", "Generar invitación"),
    BotCommand("auth", "Autorizar usuario"),
    BotCommand("users", "Listar usuarios"),
]


# ─── Control de acceso ───────────────────────────────────────────
def load_authorized_users() -> None:
    global authorized_users
    authorized_users = set()
    if not os.path.exists(AUTH_FILE):
        return
    with open(AUTH_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    authorized_users.add(int(line))
                except ValueError:
                    continue
    logger.info(f"🔐 {len(authorized_users)} usuarios autorizados.")


def save_authorized_user(user_id: int) -> bool:
    if user_id in authorized_users:
        return False
    with open(AUTH_FILE, "a", encoding="utf-8") as f:
        f.write(f"{user_id}\n")
    authorized_users.add(user_id)
    return True


def is_authorized(user_id: int) -> bool:
    if not PRIVATE_MODE:
        return True
    if OWNER_ID and str(user_id) == OWNER_ID:
        return True
    return user_id in authorized_users


def is_owner(user_id: int) -> bool:
    return OWNER_ID != "" and str(user_id) == OWNER_ID


async def set_user_commands(bot, user_id: int, role: str) -> None:
    scope = BotCommandScopeChat(chat_id=user_id)
    if role == "owner":
        await bot.set_my_commands(COMMANDS_PRIVATE_OWNER, scope=scope)
    elif role == "user":
        await bot.set_my_commands(COMMANDS_PRIVATE_USER, scope=scope)
    else:
        await bot.set_my_commands(COMMANDS_PRIVATE_NONE, scope=scope)


async def unauthorized_message(update: Update) -> None:
    await update.message.reply_text(
        "⛔ No tienes permiso para usar este bot.\n"
        "Contacta con el administrador si necesitas acceso.",
        disable_web_page_preview=True,
    )


# ─── Invitaciones ───────────────────────────────────────────────
def load_invite_codes() -> dict[str, float]:
    codes = {}
    if not os.path.exists(INVITE_FILE):
        return codes
    now = time.time()
    with open(INVITE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ";" in line:
                code, ts_str = line.split(";", 1)
                try:
                    expires_at = float(ts_str)
                    if expires_at > now:
                        codes[code] = expires_at
                except ValueError:
                    continue
            else:
                codes[line] = now + INVITE_TTL_SECONDS
    return codes


def save_invite_code(code: str) -> None:
    expires_at = time.time() + INVITE_TTL_SECONDS
    with open(INVITE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{code};{expires_at}\n")


def remove_invite_code(code: str) -> None:
    if not os.path.exists(INVITE_FILE):
        return
    with open(INVITE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(INVITE_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            if not line.strip().startswith(code + ";") and line.strip() != code:
                f.write(line)


def generate_invite_code(length: int = 12) -> str:
    return secrets.token_urlsafe(length)[:length].upper()


# ─── Utilidades de dominios ──────────────────────────────────────
def normalize_domain(domain: str) -> str:
    d = domain.strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def is_valid_domain(domain: str) -> bool:
    d = normalize_domain(domain)
    if not d:
        return False
    if " " in d or "\t" in d:
        return False
    return bool(DOMAIN_VALID_REGEX.match(d))


def get_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return normalize_domain(netloc)


def parse_preview_flag(val: str | None) -> bool:
    """Convierte string a bool de preview. Por defecto True (1)."""
    if val is None:
        return True
    return val.strip() not in ("0", "false", "no", "off")


# ─── Reglas públicas ─────────────────────────────────────────────
def load_domains() -> None:
    global domain_actions
    domain_actions = {}
    if not os.path.exists(DOMAINS_FILE):
        return
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 3:
                continue
            domain = normalize_domain(parts[0])
            method = parts[1].strip()
            modification = parts[2].strip()
            preview = parse_preview_flag(parts[3].strip() if len(parts) > 3 else None)
            if method in ("1", "2") and domain and modification:
                domain_actions[domain] = (method, modification, preview)
    logger.info(f"✅ {len(domain_actions)} reglas públicas cargadas.")


def save_domain(domain: str, method: str, modification: str, preview: bool = True) -> bool:
    domain = normalize_domain(domain)
    method = method.strip()
    modification = modification.strip()
    if method not in ("1", "2") or not domain or not modification:
        return False
    lines = []
    if os.path.exists(DOMAINS_FILE):
        with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) >= 1:
            existing_domain = normalize_domain(parts[0])
            if existing_domain == domain:
                return False
    preview_str = "1" if preview else "0"
    with open(DOMAINS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{domain};{method};{modification};{preview_str}\n")
    return True


def remove_domain_exact(domain: str, method: str, modification: str, preview: bool | None = None) -> bool:
    domain = normalize_domain(domain)
    method = method.strip()
    modification = modification.strip()
    if not os.path.exists(DOMAINS_FILE):
        return False
    found = False
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(DOMAINS_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                f.write(line)
                continue
            parts = stripped.split(";")
            if len(parts) >= 3:
                existing_domain = normalize_domain(parts[0])
                existing_method = parts[1].strip()
                existing_mod = parts[2].strip()
                existing_preview = parse_preview_flag(parts[3].strip() if len(parts) > 3 else None)
                if (existing_domain == domain and existing_method == method and existing_mod == modification
                        and (preview is None or existing_preview == preview)):
                    found = True
                    continue
            f.write(line)
    return found


def remove_domain_any(domain: str) -> tuple[bool, str | None, str | None, bool | None]:
    domain = normalize_domain(domain)
    if not os.path.exists(DOMAINS_FILE):
        return False, None, None, None
    found = False
    removed_method = None
    removed_mod = None
    removed_preview = None
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(DOMAINS_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                f.write(line)
                continue
            parts = stripped.split(";")
            if len(parts) >= 1:
                existing_domain = normalize_domain(parts[0])
                if existing_domain == domain:
                    found = True
                    if len(parts) >= 3:
                        removed_method = parts[1].strip()
                        removed_mod = parts[2].strip()
                        removed_preview = parse_preview_flag(parts[3].strip() if len(parts) > 3 else None)
                    continue
            f.write(line)
    return found, removed_method, removed_mod, removed_preview


# ─── Reglas privadas por usuario ─────────────────────────────────
def load_user_domains() -> None:
    global user_domain_actions
    user_domain_actions = {}
    if not os.path.exists(USER_DOMAINS_FILE):
        return
    with open(USER_DOMAINS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 4:
                continue
            try:
                user_id = int(parts[0].strip())
            except ValueError:
                continue
            domain = normalize_domain(parts[1])
            method = parts[2].strip()
            modification = parts[3].strip()
            preview = parse_preview_flag(parts[4].strip() if len(parts) > 4 else None)
            if method in ("1", "2") and domain and modification:
                if user_id not in user_domain_actions:
                    user_domain_actions[user_id] = {}
                user_domain_actions[user_id][domain] = (method, modification, preview)
    logger.info(f"✅ Reglas privadas cargadas para {len(user_domain_actions)} usuarios.")


def save_user_domain(user_id: int, domain: str, method: str, modification: str, preview: bool = True) -> bool:
    domain = normalize_domain(domain)
    method = method.strip()
    modification = modification.strip()
    if method not in ("1", "2") or not domain or not modification:
        return False
    if domain in domain_actions:
        return False
    if user_id in user_domain_actions and domain in user_domain_actions[user_id]:
        return False
    preview_str = "1" if preview else "0"
    with open(USER_DOMAINS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{user_id};{domain};{method};{modification};{preview_str}\n")
    if user_id not in user_domain_actions:
        user_domain_actions[user_id] = {}
    user_domain_actions[user_id][domain] = (method, modification, preview)
    return True


def remove_user_domain(user_id: int, domain: str) -> bool:
    domain = normalize_domain(domain)
    if not os.path.exists(USER_DOMAINS_FILE):
        return False
    found = False
    with open(USER_DOMAINS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(USER_DOMAINS_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                f.write(line)
                continue
            parts = stripped.split(";")
            if len(parts) >= 4:
                try:
                    uid = int(parts[0].strip())
                except ValueError:
                    f.write(line)
                    continue
                existing_domain = normalize_domain(parts[1])
                if uid == user_id and existing_domain == domain:
                    found = True
                    continue
            f.write(line)
    if found and user_id in user_domain_actions and domain in user_domain_actions[user_id]:
        del user_domain_actions[user_id][domain]
        if not user_domain_actions[user_id]:
            del user_domain_actions[user_id]
    return found


def get_user_domains(user_id: int) -> dict[str, tuple[str, str, bool]]:
    return user_domain_actions.get(user_id, {})


# ─── Reglas pendientes de aprobación ─────────────────────────────
def load_pending() -> None:
    global pending_domains
    pending_domains = []
    if not os.path.exists(PENDING_FILE):
        return
    with open(PENDING_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 4:
                continue
            try:
                user_id = int(parts[0].strip())
            except ValueError:
                continue
            domain = normalize_domain(parts[1])
            method = parts[2].strip()
            modification = parts[3].strip()
            preview = parse_preview_flag(parts[4].strip() if len(parts) > 4 else None)
            if method in ("1", "2") and domain and modification:
                pending_domains.append((user_id, domain, method, modification, preview))
    logger.info(f"⏳ {len(pending_domains)} reglas pendientes.")


def save_pending(user_id: int, domain: str, method: str, modification: str, preview: bool = True) -> bool:
    domain = normalize_domain(domain)
    method = method.strip()
    modification = modification.strip()
    if method not in ("1", "2") or not domain or not modification:
        return False
    for uid, dom, _, _, _ in pending_domains:
        if uid == user_id and dom == domain:
            return False
    preview_str = "1" if preview else "0"
    with open(PENDING_FILE, "a", encoding="utf-8") as f:
        f.write(f"{user_id};{domain};{method};{modification};{preview_str}\n")
    pending_domains.append((user_id, domain, method, modification, preview))
    return True


def remove_pending(user_id: int, domain: str) -> bool:
    domain = normalize_domain(domain)
    global pending_domains
    new_pending = [(uid, dom, meth, mod, prev) for uid, dom, meth, mod, prev in pending_domains
                   if not (uid == user_id and dom == domain)]
    if len(new_pending) == len(pending_domains):
        return False
    pending_domains = new_pending
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        for uid, dom, meth, mod, prev in pending_domains:
            preview_str = "1" if prev else "0"
            f.write(f"{uid};{dom};{meth};{mod};{preview_str}\n")
    return True


# ─── Formateo de listas ────────────────────────────────────────────
def _preview_icon(preview: bool) -> str:
    return "🖼️" if preview else "🔗"


def format_domains_list() -> str:
    grouped = {"1": [], "2": []}
    for domain, (method, modification, preview) in domain_actions.items():
        if method in grouped:
            grouped[method].append((domain, modification, preview))
    lines = [f"📋 <b>{len(domain_actions)} reglas públicas configuradas</b>"]
    lines.append("\n<b>Método 1</b> (reemplazo de dominio):")
    if grouped["1"]:
        for domain, mod, preview in sorted(grouped["1"]):
            icon = _preview_icon(preview)
            lines.append(f"  {icon} {html.escape(domain)} → {html.escape(mod)}&lt;url&gt;")
    else:
        lines.append("  (ninguno)")
    lines.append("\n<b>Método 2</b> (prefijo a URL completa):")
    if grouped["2"]:
        for domain, mod, preview in sorted(grouped["2"]):
            icon = _preview_icon(preview)
            lines.append(f"  {icon} {html.escape(domain)} → {html.escape(mod)}&lt;url&gt;")
    else:
        lines.append("  (ninguno)")
    return "\n".join(lines)


def format_user_domains_list(user_id: int) -> str:
    rules = get_user_domains(user_id)
    if not rules:
        return "📭 No tienes reglas privadas configuradas."
    grouped = {"1": [], "2": []}
    for domain, (method, modification, preview) in rules.items():
        grouped[method].append((domain, modification, preview))
    lines = [f"📋 <b>Tus {len(rules)} reglas privadas</b>"]
    lines.append("\n<b>Método 1</b> (reemplazo de dominio):")
    if grouped["1"]:
        for domain, mod, preview in sorted(grouped["1"]):
            icon = _preview_icon(preview)
            lines.append(f"  {icon} {html.escape(domain)} → {html.escape(mod)}&lt;url&gt;")
    else:
        lines.append("  (ninguno)")
    lines.append("\n<b>Método 2</b> (prefijo a URL completa):")
    if grouped["2"]:
        for domain, mod, preview in sorted(grouped["2"]):
            icon = _preview_icon(preview)
            lines.append(f"  {icon} {html.escape(domain)} → {html.escape(mod)}&lt;url&gt;")
    else:
        lines.append("  (ninguno)")
    return "\n".join(lines)


def format_pending_list() -> str:
    if not pending_domains:
        return "⏳ No hay reglas pendientes de aprobación."
    lines = [f"⏳ <b>{len(pending_domains)} reglas pendientes</b>\n"]
    for user_id, domain, method, modification, preview in pending_domains:
        icon = _preview_icon(preview)
        lines.append(f"  {icon} <code>{html.escape(domain)}</code> (método {method}: {html.escape(modification)}) — por {user_id}")
    lines.append("\nUsa /approve dominio o /reject dominio")
    return "\n".join(lines)


# ─── Aplicación de reglas ───────────────────────────────────────
def apply_method_1(url: str, old_domain: str, new_domain: str) -> str | None:
    pattern = re.compile(
        rf"(https?://)(?:www\.)?({re.escape(old_domain)})(/\S*?)?(?=\s|$)",
        re.IGNORECASE
    )
    m = pattern.search(url)
    if m:
        return pattern.sub(rf"\1{new_domain}\3", url)
    return None


def apply_method_2(url: str, prefix: str) -> str | None:
    if url.startswith(prefix):
        return None
    return f"{prefix}{url}"


async def fetch_article_info(url: str) -> tuple[str | None, str | None, str | None]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html_text = await resp.text()
    except Exception as e:
        logger.warning(f"No se pudo fetch {url}: {e}")
        return None, None, None
    soup = BeautifulSoup(html_text, "html.parser")
    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content")
    elif soup.title:
        title = soup.title.get_text(strip=True)
    image = None
    og_image = soup.find("meta", property="og:image")
    if og_image:
        image = og_image.get("content")
    if not image:
        tw_image = soup.find("meta", attrs={"name": "twitter:image"})
        if tw_image:
            image = tw_image.get("content")
    medio = None
    og_site = soup.find("meta", property="og:site_name")
    if og_site:
        medio = og_site.get("content")
    if not medio:
        medio = get_domain(url)
    return title, image, medio


async def download_image(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        logger.warning(f"No se pudo descargar imagen {url}: {e}")
    return None


def resize_image(image_bytes: bytes, max_size: tuple = (640, 480)) -> io.BytesIO:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail(max_size, Image.Resampling.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    output.seek(0)
    output.name = "preview.jpg"
    return output


async def send_preview_message(update: Update, url: str, transformed_url: str) -> bool:
    title, image_url, medio = await fetch_article_info(url)
    if title:
        caption = f"{medio}: {title}\n\n{transformed_url}"
    else:
        caption = f"{medio}\n\n{transformed_url}"
    if len(caption) > 1024:
        max_title = 1024 - len(f"{medio}: \n\n{transformed_url}") - 3
        title = (title or "")[:max_title] + "..."
        caption = f"{medio}: {title}\n\n{transformed_url}"
    try:
        if image_url:
            img_bytes = await download_image(image_url)
            if img_bytes:
                photo = resize_image(img_bytes)
                await update.message.reply_photo(photo=photo, caption=caption)
                return True
        await update.message.reply_text(caption, disable_web_page_preview=True)
        return True
    except Exception as e:
        logger.error(f"Error enviando preview para {url}: {e}")
        try:
            await update.message.reply_text(transformed_url, disable_web_page_preview=True)
            return True
        except Exception:
            return False


# ─── Handlers ────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if PRIVATE_MODE and context.args:
        code = context.args[0].strip().upper()
        valid_codes = load_invite_codes()
        if code in valid_codes:
            if save_authorized_user(user_id):
                remove_invite_code(code)
                await update.message.reply_text(
                    "✅ ¡Registro completado! Ya tienes acceso al bot.",
                    disable_web_page_preview=True,
                )
                await set_user_commands(context.bot, user_id, "user")
                if OWNER_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=int(OWNER_ID),
                            text=f"🔓 Nuevo usuario registrado: <code>{user_id}</code> ({update.effective_user.full_name})",
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        pass
            else:
                await update.message.reply_text(
                    "⚠️ Ya estabas registrado. Usa los comandos del bot.",
                    disable_web_page_preview=True,
                )
            return
        else:
            await update.message.reply_text(
                "❌ Código de invitación inválido o caducado (válido 24h).",
                disable_web_page_preview=True,
            )
            return

    if PRIVATE_MODE and not is_authorized(user_id):
        await unauthorized_message(update)
        return

    description = (
        "🤖 <b>URLMorph Bot</b>\n\n"
        "Transformo automáticamente enlaces según tus reglas configuradas:\n"
        "• <b>Método 1</b>: reemplazo el dominio por otro indicado\n"
        "• <b>Método 2</b>: añado un prefijo delante de la URL completa\n\n"
        "<b>Reglas públicas</b>: visibles para todos los usuarios.\n"
        "<b>Reglas privadas</b>: solo tú las ves y usas.\n"
        "Puedes promover tus reglas privadas para que el administrador las haga públicas.\n\n"
        "Usa el menú de comandos (/) para gestionar tus reglas."
    )
    await update.message.reply_text(description, parse_mode="HTML", disable_web_page_preview=True)


async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text(
            "ℹ️ El bot está en modo público. No se necesitan invitaciones.",
            disable_web_page_preview=True,
        )
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede generar invitaciones.",
            disable_web_page_preview=True,
        )
        return
    code = generate_invite_code()
    save_invite_code(code)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={code}"
    await update.message.reply_text(
        f"🔗 <b>Enlace de invitación generado</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"⏳ Válido por <b>24 horas</b>. Un solo uso.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def auth_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text(
            "ℹ️ El bot está en modo público.",
            disable_web_page_preview=True,
        )
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede autorizar usuarios.",
            disable_web_page_preview=True,
        )
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /auth &lt;user_id&gt;\nEjemplo: /auth 123456789",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    try:
        new_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text(
            "❌ El ID debe ser un número.",
            disable_web_page_preview=True,
        )
        return
    if save_authorized_user(new_id):
        await set_user_commands(context.bot, new_id, "user")
        await update.message.reply_text(
            f"✅ Usuario <code>{new_id}</code> autorizado.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            f"⚠️ El usuario <code>{new_id}</code> ya estaba autorizado.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text(
            "ℹ️ El bot está en modo público.",
            disable_web_page_preview=True,
        )
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede ver la lista de usuarios.",
            disable_web_page_preview=True,
        )
        return
    lines = ["🔐 <b>Usuarios autorizados</b>\n"]
    if OWNER_ID:
        lines.append(f"  👑 Owner: <code>{OWNER_ID}</code>")
    for uid in sorted(authorized_users):
        lines.append(f"  • <code>{uid}</code>")
    if not authorized_users:
        lines.append("  (ninguno aparte del owner)")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ─── Reglas públicas (list/add/remove/reload) ────────────────────
async def list_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    await update.message.reply_text(
        format_domains_list(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def add_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /add &lt;dominio&gt;;&lt;método&gt;;&lt;modificación&gt;;&lt;preview&gt;\n"
            "Ejemplos:\n"
            "  /add x.com;1;i.fixupx.com\n"
            "  /add example.com;2;https://prefix.example.com/;0\n\n"
            "<b>Preview</b>: 1 (con preview, por defecto) o 0 (solo URL).",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    raw = " ".join(context.args)
    parts = raw.split(";")
    if len(parts) >= 3:
        domain = parts[0].strip()
        method = parts[1].strip()
        modification = parts[2].strip()
        preview = parse_preview_flag(parts[3].strip() if len(parts) > 3 else None)
    elif len(context.args) >= 3:
        domain, method, modification = context.args[0], context.args[1], context.args[2]
        preview = True
    else:
        await update.message.reply_text(
            "❌ Formato incorrecto. Usa: /add dominio;método;modificación;preview",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ El dominio <b>{html.escape(domain)}</b> no es válido.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    if method not in ("1", "2"):
        await update.message.reply_text(
            f"❌ Método no válido: <b>{method}</b>.\nUsa <code>1</code> o <code>2</code>.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    if not modification:
        await update.message.reply_text(
            "❌ La modificación no puede estar vacía.",
            disable_web_page_preview=True,
        )
        return

    if save_domain(domain, method, modification, preview):
        load_domains()
        icon = _preview_icon(preview)
        await update.message.reply_text(
            f"✅ {icon} <b>{normalize_domain(domain)}</b> añadido a reglas públicas "
            f"(método {method}: <code>{html.escape(modification)}</code>, preview: <code>{'1' if preview else '0'}</code>).\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            f"⚠️ <b>{normalize_domain(domain)}</b> ya existe en las reglas públicas.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def remove_domain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /remove &lt;dominio&gt; o /remove &lt;dominio&gt;;&lt;método&gt;;&lt;modificación&gt;\n"
            "Ejemplos:\n"
            "  /remove x.com\n"
            "  /remove x.com;1;i.fixupx.com",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    raw = " ".join(context.args).strip()

    if raw.count(";") >= 2:
        parts = raw.split(";", 2)
        domain = parts[0].strip()
        method = parts[1].strip()
        modification = parts[2].strip()
        if not is_valid_domain(domain):
            await update.message.reply_text(
                f"❌ Dominio no válido: <b>{html.escape(domain)}</b>.",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if method not in ("1", "2"):
            await update.message.reply_text(
                f"❌ Método no válido: <b>{method}</b>.",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        normalized = normalize_domain(domain)
        current = domain_actions.get(normalized)
        if not current or current[0] != method or current[1] != modification:
            await update.message.reply_text(
                f"❌ La regla <b>{html.escape(normalized)};{method};{html.escape(modification)}</b> no existe.\n\n"
                f"{format_domains_list()}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        if remove_domain_exact(domain, method, modification):
            load_domains()
            await update.message.reply_text(
                f"🗑️ <b>{html.escape(normalized)};{method};{html.escape(modification)}</b> eliminado.\n\n"
                f"{format_domains_list()}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        else:
            await update.message.reply_text(
                "⚠️ No se pudo eliminar la regla.",
                disable_web_page_preview=True,
            )
        return

    domain = raw
    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ Dominio no válido: <b>{html.escape(domain)}</b>.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    normalized = normalize_domain(domain)
    if normalized not in domain_actions:
        await update.message.reply_text(
            f"❌ El dominio <b>{html.escape(normalized)}</b> no existe en las reglas públicas.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    found, removed_method, removed_mod, removed_preview = remove_domain_any(domain)
    if found:
        load_domains()
        icon = _preview_icon(removed_preview) if removed_preview is not None else ""
        await update.message.reply_text(
            f"🗑️ {icon} <b>{html.escape(normalized)}</b> "
            f"(método {removed_method}: <code>{html.escape(removed_mod)}</code>) eliminado.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            "⚠️ No se pudo eliminar.",
            disable_web_page_preview=True,
        )


async def reload_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    load_domains()
    load_user_domains()
    load_pending()
    user_id = update.effective_user.id
    public_list = format_domains_list()
    private_list = format_user_domains_list(user_id)
    await update.message.reply_text(
        "🔄 Ficheros recargados.\n\n"
        f"{public_list}\n\n"
        f"{'─' * 20}\n\n"
        f"{private_list}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ─── Reglas privadas (myadd/myremove/mylist) ─────────────────────
async def myadd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /myadd &lt;dominio&gt;;&lt;método&gt;;&lt;modificación&gt;;&lt;preview&gt;\n"
            "Ejemplos:\n"
            "  /myadd x.com;1;i.fixupx.com\n"
            "  /myadd example.com;2;https://prefix.example.com/;0\n\n"
            "<b>Preview</b>: 1 (con preview, por defecto) o 0 (solo URL).",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    raw = " ".join(context.args)
    parts = raw.split(";")
    if len(parts) >= 3:
        domain = parts[0].strip()
        method = parts[1].strip()
        modification = parts[2].strip()
        preview = parse_preview_flag(parts[3].strip() if len(parts) > 3 else None)
    elif len(context.args) >= 3:
        domain, method, modification = context.args[0], context.args[1], context.args[2]
        preview = True
    else:
        await update.message.reply_text(
            "❌ Formato incorrecto. Usa: /myadd dominio;método;modificación;preview",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ El dominio <b>{html.escape(domain)}</b> no es válido.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    if method not in ("1", "2"):
        await update.message.reply_text(
            f"❌ Método no válido: <b>{method}</b>.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    if not modification:
        await update.message.reply_text(
            "❌ La modificación no puede estar vacía.",
            disable_web_page_preview=True,
        )
        return

    if save_user_domain(user_id, domain, method, modification, preview):
        icon = _preview_icon(preview)
        await update.message.reply_text(
            f"✅ {icon} <b>{normalize_domain(domain)}</b> añadido a tus reglas privadas "
            f"(método {method}: <code>{html.escape(modification)}</code>, preview: <code>{'1' if preview else '0'}</code>).\n\n"
            f"{format_user_domains_list(user_id)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            f"⚠️ <b>{normalize_domain(domain)}</b> ya existe en tus reglas privadas o en las públicas.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def myremove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /myremove &lt;dominio&gt;\nEjemplo: /myremove x.com",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    domain = " ".join(context.args).strip()
    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ Dominio no válido: <b>{html.escape(domain)}</b>.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    normalized = normalize_domain(domain)
    if remove_user_domain(user_id, domain):
        await update.message.reply_text(
            f"🗑️ <b>{html.escape(normalized)}</b> eliminado de tus reglas privadas.\n\n"
            f"{format_user_domains_list(user_id)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            f"❌ <b>{html.escape(normalized)}</b> no existe en tus reglas privadas.\n\n"
            f"{format_user_domains_list(user_id)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def mylist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    user_id = update.effective_user.id
    await update.message.reply_text(
        format_user_domains_list(user_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ─── Promoción a públicas ──────────────────────────────────────────
async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /promote &lt;dominio&gt;\nEjemplo: /promote x.com",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    domain = " ".join(context.args).strip()
    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ Dominio no válido: <b>{html.escape(domain)}</b>.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    normalized = normalize_domain(domain)
    rules = get_user_domains(user_id)
    if normalized not in rules:
        await update.message.reply_text(
            f"❌ <b>{html.escape(normalized)}</b> no existe en tus reglas privadas.\n\n"
            f"{format_user_domains_list(user_id)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    method, modification, preview = rules[normalized]
    if save_pending(user_id, domain, method, modification, preview):
        await update.message.reply_text(
            f"📤 <b>{html.escape(normalized)}</b> enviado a revisión del administrador para ser promovido a regla pública.\n"
            f"El owner debe usar /approve para aceptarlo.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if OWNER_ID:
            try:
                icon = _preview_icon(preview)
                await context.bot.send_message(
                    chat_id=int(OWNER_ID),
                    text=f"📥 <b>Nueva regla pendiente</b>\n\n"
                         f"Dominio: <code>{html.escape(normalized)}</code>\n"
                         f"Método: {method}\n"
                         f"Modificación: <code>{html.escape(modification)}</code>\n"
                         f"Preview: <code>{'1' if preview else '0'}</code> {icon}\n"
                         f"Usuario: <code>{user_id}</code>\n\n"
                         f"Usa /pending para verla y /approve {html.escape(normalized)} para aceptarla.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
    else:
        await update.message.reply_text(
            "⚠️ Esta regla ya está pendiente de aprobación.",
            disable_web_page_preview=True,
        )


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede ver las reglas pendientes.",
            disable_web_page_preview=True,
        )
        return
    await update.message.reply_text(
        format_pending_list(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede aprobar reglas.",
            disable_web_page_preview=True,
        )
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /approve &lt;dominio&gt;\nEjemplo: /approve x.com",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    domain = " ".join(context.args).strip()
    normalized = normalize_domain(domain)

    found = None
    for uid, dom, meth, mod, prev in pending_domains:
        if dom == normalized:
            found = (uid, dom, meth, mod, prev)
            break
    if not found:
        await update.message.reply_text(
            f"❌ <b>{html.escape(normalized)}</b> no está en la lista de pendientes.\n\n"
            f"{format_pending_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    uid, dom, meth, mod, prev = found
    if save_domain(dom, meth, mod, prev):
        remove_pending(uid, dom)
        remove_user_domain(uid, dom)
        load_domains()
        await update.message.reply_text(
            f"✅ <b>{html.escape(dom)}</b> aprobado y añadido a las reglas públicas.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"🎉 ¡Tu regla para <b>{html.escape(dom)}</b> ha sido aprobada y ahora es pública!",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(
            f"⚠️ <b>{html.escape(dom)}</b> ya existe en las reglas públicas. Eliminando de pendientes.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        remove_pending(uid, dom)


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Solo el administrador puede rechazar reglas.",
            disable_web_page_preview=True,
        )
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /reject &lt;dominio&gt;\nEjemplo: /reject x.com",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return
    domain = " ".join(context.args).strip()
    normalized = normalize_domain(domain)

    found = None
    for uid, dom, meth, mod, prev in pending_domains:
        if dom == normalized:
            found = (uid, dom, meth, mod, prev)
            break
    if not found:
        await update.message.reply_text(
            f"❌ <b>{html.escape(normalized)}</b> no está en la lista de pendientes.\n\n"
            f"{format_pending_list()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    uid, dom, meth, mod, prev = found
    remove_pending(uid, dom)
    await update.message.reply_text(
        f"❌ <b>{html.escape(dom)}</b> rechazado y eliminado de pendientes.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ Tu regla para <b>{html.escape(dom)}</b> ha sido rechazada por el administrador.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


# ─── Procesamiento de mensajes ───────────────────────────────────
async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    if PRIVATE_MODE and not is_authorized(user_id):
        await unauthorized_message(update)
        return

    text = update.message.text
    urls = URL_PATTERN.findall(text)
    if not urls:
        return

    modified = False
    transformed_text = text

    user_rules = get_user_domains(user_id)

    for url in urls:
        domain = get_domain(url)
        rule = None

        if domain in user_rules:
            rule = user_rules[domain]
        elif domain in domain_actions:
            rule = domain_actions[domain]

        if not rule:
            continue

        method, modification, preview = rule
        if method == "1":
            fixed = apply_method_1(url, domain, modification)
            if fixed:
                if preview:
                    # Preview generado por el bot (parseo + foto/caption)
                    await send_preview_message(update, url, fixed)
                else:
                    # Solo la URL transformada, permitiendo preview nativo de Telegram
                    await update.message.reply_text(fixed)
                transformed_text = transformed_text.replace(url, "")
                modified = True
        elif method == "2":
            fixed = apply_method_2(url, modification)
            if fixed:
                if preview:
                    # Preview generado por el bot (parseo + foto/caption)
                    await send_preview_message(update, url, fixed)
                else:
                    # Solo la URL transformada, permitiendo preview nativo de Telegram
                    await update.message.reply_text(fixed)
                transformed_text = transformed_text.replace(url, "")
                modified = True

    transformed_text = re.sub(r"\s+", " ", transformed_text).strip()
    if transformed_text and transformed_text != text:
        # El texto restante (sin URLs) se envía sin preview web
        await update.message.reply_text(transformed_text, disable_web_page_preview=True)
        modified = True

    if modified:
        try:
            await update.message.delete()
        except BadRequest:
            pass


# ─── Post-init ───────────────────────────────────────────────────
async def post_init(application: Application) -> None:
    bot = application.bot
    if PRIVATE_MODE:
        await bot.set_my_commands(COMMANDS_PRIVATE_NONE, scope=BotCommandScopeAllPrivateChats())
        if OWNER_ID:
            try:
                await set_user_commands(bot, int(OWNER_ID), "owner")
            except Exception as e:
                logger.warning(f"No se pudo configurar menú del owner: {e}")
        for uid in authorized_users:
            try:
                await set_user_commands(bot, uid, "user")
            except Exception:
                pass
    else:
        await bot.set_my_commands(COMMANDS_PUBLIC, scope=BotCommandScopeAllPrivateChats())
    logger.info("📋 Menús de comandos configurados.")


# ─── Main ────────────────────────────────────────────────────────
def main() -> None:
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN no definido.")
        raise SystemExit(1)

    load_domains()
    load_user_domains()
    load_pending()
    load_authorized_users()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("invite", invite))
    application.add_handler(CommandHandler("auth", auth_user))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("list", list_domains))
    application.add_handler(CommandHandler("add", add_domain))
    application.add_handler(CommandHandler("remove", remove_domain_cmd))
    application.add_handler(CommandHandler("reload", reload_domains))
    application.add_handler(CommandHandler("myadd", myadd))
    application.add_handler(CommandHandler("myremove", myremove))
    application.add_handler(CommandHandler("mylist", mylist))
    application.add_handler(CommandHandler("promote", promote))
    application.add_handler(CommandHandler("pending", pending))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("reject", reject))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

    logger.info("🤖 URLMorph Bot iniciado.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
