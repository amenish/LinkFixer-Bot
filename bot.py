import os
import re
import io
import time
import secrets
import logging
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
AUTH_FILE = os.getenv("AUTH_FILE", "/app/authorized_users")
INVITE_FILE = os.getenv("INVITE_FILE", "/app/invite_codes")
OWNER_ID = os.getenv("OWNER_ID")
INVITE_TTL_SECONDS = 24 * 60 * 60

PRIVATE_MODE = OWNER_ID is not None and OWNER_ID.strip() != ""
if PRIVATE_MODE:
    OWNER_ID = OWNER_ID.strip()
    logger.info("🔒 Modo PRIVADO activado.")
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
authorized_users = set()

# Regex para validar dominios (sin espacios, formato básico)
DOMAIN_VALID_REGEX = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)

# ─── Comandos por rol ────────────────────────────────────────────
COMMANDS_PUBLIC = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("add", "Añadir dominio;acción"),
    BotCommand("remove", "Eliminar dominio"),
    BotCommand("reload", "Recargar y listar dominios"),
]
COMMANDS_PRIVATE_NONE = [
    BotCommand("start", "Iniciar o registrarse con invitación"),
]
COMMANDS_PRIVATE_USER = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("add", "Añadir dominio;acción"),
    BotCommand("remove", "Eliminar dominio"),
    BotCommand("reload", "Recargar y listar dominios"),
]
COMMANDS_PRIVATE_OWNER = [
    BotCommand("start", "Descripción del bot"),
    BotCommand("add", "Añadir dominio;acción"),
    BotCommand("remove", "Eliminar dominio"),
    BotCommand("reload", "Recargar y listar dominios"),
    BotCommand("invite", "Generar enlace de invitación"),
    BotCommand("auth", "Autorizar usuario manualmente"),
    BotCommand("users", "Listar usuarios autorizados"),
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
    return PRIVATE_MODE and OWNER_ID is not None and str(user_id) == OWNER_ID


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
        "Contacta con el administrador si necesitas acceso."
    )


# ─── Invitaciones con caducidad ───────────────────────────────────
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
    """Valida que el dominio no tenga espacios y tenga formato básico correcto."""
    d = normalize_domain(domain)
    if not d:
        return False
    if " " in d or "\t" in d:
        return False
    return bool(DOMAIN_VALID_REGEX.match(d))


def get_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return normalize_domain(netloc)


def load_domains() -> None:
    global domain_actions
    domain_actions = {}
    if not os.path.exists(DOMAINS_FILE):
        return
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ";" not in line:
                continue
            domain, action = line.split(";", 1)
            domain = normalize_domain(domain)
            action = action.strip().lower()
            if action in ("fixup", "unwall"):
                domain_actions[domain] = action
    logger.info(f"✅ {len(domain_actions)} reglas cargadas.")


def save_domain(domain: str, action: str) -> bool:
    domain = normalize_domain(domain)
    action = action.strip().lower()
    if action not in ("fixup", "unwall"):
        return False
    lines = []
    if os.path.exists(DOMAINS_FILE):
        with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line:
            existing_domain = normalize_domain(line.split(";", 1)[0])
            if existing_domain == domain:
                return False
    with open(DOMAINS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{domain};{action}\n")
    return True


def remove_domain_exact(domain: str, action: str) -> bool:
    """Elimina un par dominio;acción exacto del fichero."""
    domain = normalize_domain(domain)
    action = action.strip().lower()
    if action not in ("fixup", "unwall"):
        return False
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
            if ";" in stripped:
                parts = stripped.split(";", 1)
                existing_domain = normalize_domain(parts[0])
                existing_action = parts[1].strip().lower()
                if existing_domain == domain and existing_action == action:
                    found = True
                    continue
            f.write(line)
    return found


def remove_domain_any_action(domain: str) -> tuple[bool, str | None]:
    """Elimina un dominio del fichero sin importar su acción. Devuelve (éxito, acción_eliminada)."""
    domain = normalize_domain(domain)
    if not os.path.exists(DOMAINS_FILE):
        return False, None

    found = False
    removed_action = None
    with open(DOMAINS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    with open(DOMAINS_FILE, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                f.write(line)
                continue
            if ";" in stripped:
                parts = stripped.split(";", 1)
                existing_domain = normalize_domain(parts[0])
                if existing_domain == domain:
                    found = True
                    removed_action = parts[1].strip().lower()
                    continue
            f.write(line)
    return found, removed_action


def format_domains_list() -> str:
    grouped = {"fixup": [], "unwall": []}
    for domain, action in domain_actions.items():
        if action in grouped:
            grouped[action].append(domain)
    lines = [f"📋 <b>{len(domain_actions)} dominios configurados</b>"]
    for action in ("fixup", "unwall"):
        domains = sorted(grouped[action])
        if domains:
            lines.append(f"\n<b>{action.upper()}</b>:")
            lines.extend(f"  • {d}" for d in domains)
    lines.append("\n<i>Usa /add dominio acción para añadir o /remove dominio para eliminar.</i>")
    return "\n".join(lines)


# ─── URLs ────────────────────────────────────────────────────────
URL_PATTERN = re.compile(r"https?://\S+")

def apply_fixup(url: str) -> str | None:
    m = re.match(r"(https?://)(?:www\.)?(x\.com|twitter\.com)(/\S*)", url, re.I)
    if m:
        return f"{m.group(1)}i.fixupx.com{m.group(3)}"
    return None


async def fetch_article_info(url: str) -> tuple[str | None, str | None, str | None]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
    except Exception as e:
        logger.warning(f"No se pudo fetch {url}: {e}")
        return None, None, None
    soup = BeautifulSoup(html, "html.parser")
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


async def send_unwall_message(update: Update, url: str) -> bool:
    unwall_url = f"https://unwall.app/{url}"
    title, image_url, medio = await fetch_article_info(url)
    if title:
        caption = f"{medio}: {title}\n\n{unwall_url}"
    else:
        caption = f"{medio}\n\n{unwall_url}"
    if len(caption) > 1024:
        max_title = 1024 - len(f"{medio}: \n\n{unwall_url}") - 3
        title = (title or "")[:max_title] + "..."
        caption = f"{medio}: {title}\n\n{unwall_url}"
    try:
        if image_url:
            img_bytes = await download_image(image_url)
            if img_bytes:
                photo = resize_image(img_bytes)
                await update.message.reply_photo(photo=photo, caption=caption)
                return True
        await update.message.reply_text(caption)
        return True
    except Exception as e:
        logger.error(f"Error enviando unwall para {url}: {e}")
        try:
            await update.message.reply_text(unwall_url)
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
                    "✅ ¡Registro completado! Ya tienes acceso al bot."
                )
                await set_user_commands(context.bot, user_id, "user")
                if OWNER_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=int(OWNER_ID),
                            text=f"🔓 Nuevo usuario registrado: <code>{user_id}</code> ({update.effective_user.full_name})",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            else:
                await update.message.reply_text(
                    "⚠️ Ya estabas registrado. Usa los comandos del bot."
                )
            return
        else:
            await update.message.reply_text(
                "❌ Código de invitación inválido o caducado (válido 24h)."
            )
            return

    if PRIVATE_MODE and not is_authorized(user_id):
        await unauthorized_message(update)
        return

    description = (
        "🤖 <b>LinkFixer Bot</b>\n"
        "\n"
        "Detecto enlaces de ciertos dominios y los transformo automáticamente:\n"
        "• <b>fixup</b> → reemplazo el dominio por <code>i.fixupx.com</code>\n"
        "• <b>unwall</b> → añado prefijo <code>https://unwall.app/</code> con preview\n"
        "\n"
        "Usa el menú de comandos (/) para gestionar las reglas."
    )
    await update.message.reply_text(description, parse_mode="HTML")


async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text("ℹ️ El bot está en modo público. No se necesitan invitaciones.")
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Solo el administrador puede generar invitaciones.")
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
    )


async def auth_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text("ℹ️ El bot está en modo público. No se necesita autorización.")
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Solo el administrador puede autorizar usuarios.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /auth <user_id>\nEjemplo: /auth 123456789",
            parse_mode="HTML",
        )
        return

    try:
        new_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ El ID debe ser un número.")
        return

    if save_authorized_user(new_id):
        await set_user_commands(context.bot, new_id, "user")
        await update.message.reply_text(
            f"✅ Usuario <code>{new_id}</code> autorizado.", parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"⚠️ El usuario <code>{new_id}</code> ya estaba autorizado.", parse_mode="HTML"
        )


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PRIVATE_MODE:
        await update.message.reply_text("ℹ️ El bot está en modo público. No hay lista de usuarios.")
        return
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Solo el administrador puede ver la lista de usuarios.")
        return

    lines = ["🔐 <b>Usuarios autorizados</b>\n"]
    if OWNER_ID:
        lines.append(f"  👑 Owner: <code>{OWNER_ID}</code>")
    for uid in sorted(authorized_users):
        lines.append(f"  • <code>{uid}</code>")
    if not authorized_users:
        lines.append("  (ninguno aparte del owner)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def add_domain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /add <dominio>;<acción>\nEjemplo: /add elmundo.es;unwall",
            parse_mode="HTML",
        )
        return

    raw = " ".join(context.args)
    if ";" in raw:
        domain, action = raw.split(";", 1)
    elif len(context.args) >= 2:
        domain = context.args[0]
        action = context.args[1]
    else:
        await update.message.reply_text(
            "❌ Formato incorrecto. Usa: /add dominio;accion",
            parse_mode="HTML",
        )
        return

    domain = domain.strip()
    action = action.strip().lower()

    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ El dominio <b>{domain}</b> no es válido.\n"
            "No puede contener espacios ni caracteres especiales.",
            parse_mode="HTML",
        )
        return

    if action not in ("fixup", "unwall"):
        await update.message.reply_text(
            f"❌ Acción no válida: <b>{action}</b>.\nUsa <code>fixup</code> o <code>unwall</code>.",
            parse_mode="HTML",
        )
        return

    if save_domain(domain, action):
        load_domains()
        await update.message.reply_text(
            f"✅ <b>{normalize_domain(domain)}</b> añadido con acción <b>{action}</b>.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"⚠️ <b>{normalize_domain(domain)}</b> ya existe en el fichero.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
        )


async def remove_domain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina un dominio del fichero. Puede ser por par exacto o solo por dominio."""
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /remove <dominio> o /remove <dominio>;<acción>\n"
            "Ejemplos:\n"
            "  /remove elmundo.es\n"
            "  /remove elmundo.es;unwall",
            parse_mode="HTML",
        )
        return

    raw = " ".join(context.args).strip()

    # Caso 1: formato con punto y coma → par exacto
    if ";" in raw:
        domain, action = raw.split(";", 1)
        domain = domain.strip()
        action = action.strip().lower()

        if not is_valid_domain(domain):
            await update.message.reply_text(
                f"❌ El dominio <b>{domain}</b> no es válido. No puede contener espacios.",
                parse_mode="HTML",
            )
            return

        if action not in ("fixup", "unwall"):
            await update.message.reply_text(
                f"❌ Acción no válida: <b>{action}</b>.\nUsa <code>fixup</code> o <code>unwall</code>.",
                parse_mode="HTML",
            )
            return

        normalized = normalize_domain(domain)

        # Verifica que el par exacto existe en memoria
        if domain_actions.get(normalized) != action:
            await update.message.reply_text(
                f"❌ El par <b>{normalized};{action}</b> no existe en la configuración actual.\n\n"
                f"{format_domains_list()}",
                parse_mode="HTML",
            )
            return

        if remove_domain_exact(domain, action):
            load_domains()
            await update.message.reply_text(
                f"🗑️ <b>{normalized};{action}</b> eliminado correctamente.\n\n"
                f"{format_domains_list()}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"⚠️ No se pudo eliminar <b>{normalized};{action}</b>. Revisa el fichero manualmente.",
                parse_mode="HTML",
            )
        return

    # Caso 2: solo dominio (sin punto y coma) → borra cualquier acción asociada
    domain = raw
    if not is_valid_domain(domain):
        await update.message.reply_text(
            f"❌ El dominio <b>{domain}</b> no es válido. No puede contener espacios ni caracteres especiales.",
            parse_mode="HTML",
        )
        return

    normalized = normalize_domain(domain)

    if normalized not in domain_actions:
        await update.message.reply_text(
            f"❌ El dominio <b>{normalized}</b> no existe en la configuración actual.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
        )
        return

    found, removed_action = remove_domain_any_action(domain)
    if found:
        load_domains()
        await update.message.reply_text(
            f"🗑️ <b>{normalized}</b> (acción: {removed_action}) eliminado correctamente.\n\n"
            f"{format_domains_list()}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"⚠️ No se pudo eliminar <b>{normalized}</b>. Revisa el fichero manualmente.",
            parse_mode="HTML",
        )


async def reload_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if PRIVATE_MODE and not is_authorized(update.effective_user.id):
        await unauthorized_message(update)
        return

    load_domains()
    await update.message.reply_text(format_domains_list(), parse_mode="HTML")


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
    fixup_text = text

    for url in urls:
        domain = get_domain(url)
        action = domain_actions.get(domain)

        if action == "unwall":
            success = await send_unwall_message(update, url)
            if success:
                modified = True
                fixup_text = fixup_text.replace(url, "")

        elif action == "fixup":
            fixed = apply_fixup(url)
            if fixed:
                fixup_text = fixup_text.replace(url, fixed)
                modified = True

    fixup_text = re.sub(r"\s+", " ", fixup_text).strip()

    if fixup_text and fixup_text != text:
        await update.message.reply_text(fixup_text)
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
        await bot.set_my_commands(
            COMMANDS_PRIVATE_NONE,
            scope=BotCommandScopeAllPrivateChats()
        )
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
        await bot.set_my_commands(
            COMMANDS_PUBLIC,
            scope=BotCommandScopeAllPrivateChats()
        )

    logger.info("📋 Menús de comandos configurados.")


# ─── Main ────────────────────────────────────────────────────────
def main() -> None:
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN no definido.")
        raise SystemExit(1)

    load_domains()
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
    application.add_handler(CommandHandler("add", add_domain))
    application.add_handler(CommandHandler("remove", remove_domain_cmd))
    application.add_handler(CommandHandler("reload", reload_domains))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

    logger.info("🤖 Bot iniciado.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
