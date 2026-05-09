import asyncio
import json
import sys
import os
import threading
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv, set_key
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types
from kommo_client import KommoClient

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
REDIRECT_URI = "http://localhost:8080/callback"

# ── ESTADO GLOBAL DE CONEXIÓN ─────────────────────────────────────────────────

_auth_code: str | None = None
_oauth_ready = threading.Event()   # se activa cuando los tokens quedan guardados
_client: KommoClient | None = None


def _get_client() -> KommoClient:
    global _client
    if _client is None:
        load_dotenv(ENV_PATH, override=True)
        _client = KommoClient()
    return _client


def _is_configured() -> bool:
    load_dotenv(ENV_PATH, override=True)
    return bool(os.getenv("KOMMO_CLIENT_ID") and os.getenv("KOMMO_CLIENT_SECRET")
                and os.getenv("KOMMO_SUBDOMAIN"))


def _is_authenticated() -> bool:
    load_dotenv(ENV_PATH, override=True)
    return bool(os.getenv("KOMMO_ACCESS_TOKEN"))


# ── OAUTH CALLBACK SERVER (background thread) ─────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:40px'>"
                b"<h2 style='color:#6B21A8'>G&M Capital conectado</h2>"
                b"<p>Autorizacion exitosa. Vuelve a Claude y escribe: "
                b"<b>verificar conexion Kommo</b></p></body></html>"
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no se recibio codigo.")

    def log_message(self, *args):
        pass


def _exchange_and_save(client_id: str, client_secret: str, code: str) -> None:
    global _client
    resp = httpx.post(
        "https://kommo.com/oauth2/access_token",
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", data["access_token"])
    set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", data["refresh_token"])
    load_dotenv(ENV_PATH, override=True)
    _client = KommoClient()   # reinicializar con tokens frescos
    _oauth_ready.set()


def _start_callback_listener(client_id: str, client_secret: str) -> None:
    """Lanza servidor HTTP en :8080 en background. Captura código y canjea por tokens."""
    global _auth_code
    _auth_code = None
    _oauth_ready.clear()

    def _listen():
        server = HTTPServer(("localhost", 8080), _CallbackHandler)
        server.serve_forever()
        if _auth_code:
            try:
                _exchange_and_save(client_id, client_secret, _auth_code)
            except Exception as e:
                print(f"[kommo-mcp] Error al canjear tokens: {e}", file=sys.stderr)

    t = threading.Thread(target=_listen, daemon=True)
    t.start()


# ── VALIDACIÓN DE INPUTS ──────────────────────────────────────────────────────

def _validate(arguments: dict, rules: list[tuple]) -> str | None:
    """
    rules: lista de (campo, tipo, requerido)
    Retorna mensaje de error o None si todo OK.
    """
    for field, expected_type, required in rules:
        val = arguments.get(field)
        if val is None:
            if required:
                return f"Campo requerido faltante: `{field}`"
            continue
        if expected_type == int and not isinstance(val, int):
            return f"`{field}` debe ser entero, recibí: {type(val).__name__}"
        if expected_type == str and not isinstance(val, str):
            return f"`{field}` debe ser texto, recibí: {type(val).__name__}"
        if expected_type == str and isinstance(val, str) and not val.strip():
            return f"`{field}` no puede estar vacío"
        if expected_type == list and not isinstance(val, list):
            return f"`{field}` debe ser lista, recibí: {type(val).__name__}"
    return None


TOOL_RULES: dict[str, list[tuple]] = {
    "create_lead":         [("name", str, True)],
    "update_lead":         [("lead_id", int, True), ("fields", dict, True)],
    "move_lead_stage":     [("lead_id", int, True), ("stage_id", int, True)],
    "get_lead":            [("lead_id", int, True)],
    "create_contact":      [("name", str, True)],
    "update_contact":      [("contact_id", int, True), ("fields", dict, True)],
    "create_pipeline":     [("name", str, True)],
    "update_pipeline":     [("pipeline_id", int, True), ("name", str, True)],
    "list_stages":         [("pipeline_id", int, True)],
    "create_stage":        [("pipeline_id", int, True), ("name", str, True)],
    "update_stage":        [("pipeline_id", int, True), ("stage_id", int, True)],
    "create_task":         [("lead_id", int, True), ("text", str, True),
                            ("due_date", int, True)],
    "add_note":            [("lead_id", int, True), ("text", str, True)],
    "add_tag":             [("lead_id", int, True), ("tag_name", str, True)],
    "remove_tag":          [("lead_id", int, True), ("tag_name", str, True)],
    "create_custom_field": [("entity_type", str, True), ("field_type", str, True),
                            ("name", str, True)],
    "bulk_move_leads":     [("lead_ids", list, True), ("stage_id", int, True)],
    "bulk_add_tag":        [("lead_ids", list, True), ("tag_name", str, True)],
}


# ── SERVIDOR MCP ──────────────────────────────────────────────────────────────

app = Server("kommo-gmcapital")


def _ok(data) -> list[types.TextContent]:
    return [types.TextContent(type="text",
                              text=json.dumps(data, ensure_ascii=False, indent=2))]


def _err(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"Error: {msg}")]


def _need_setup() -> list[types.TextContent]:
    return [types.TextContent(type="text", text=(
        "Kommo no está configurado.\n"
        "Llama a `kommo_configure` con client_id, client_secret y subdomain para conectar."
    ))]


def _need_auth(auth_url: str = "") -> list[types.TextContent]:
    msg = "Kommo no está autenticado.\nLlama a `kommo_setup` para obtener el enlace de autorización."
    if auth_url:
        msg = f"Kommo no está autenticado.\nVisita este enlace para autorizar:\n{auth_url}"
    return [types.TextContent(type="text", text=msg)]


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # ── SETUP ──
        types.Tool(
            name="kommo_configure",
            description=(
                "PRIMER PASO: Configura credenciales de Kommo. "
                "OPCIÓN A (recomendada): pasa secret_key desde el panel de Kommo → tokens automáticos, sin browser. "
                "OPCIÓN B: pasa access_token directamente si ya tienes uno válido. "
                "OPCIÓN C: sin secret_key ni access_token → devuelve URL de autorización (requiere browser)."
            ),
            inputSchema={
                "type": "object",
                "required": ["client_id", "client_secret", "subdomain"],
                "properties": {
                    "client_id": {"type": "string", "description": "OAuth client_id de Kommo"},
                    "client_secret": {"type": "string", "description": "OAuth client_secret de Kommo"},
                    "subdomain": {"type": "string",
                                  "description": "Subdominio Kommo (ej: miempresa de miempresa.kommo.com)"},
                    "secret_key": {"type": "string",
                                   "description": "Código secreto desde panel Kommo → Settings → Integrations. Evita el browser completamente."},
                    "access_token": {"type": "string",
                                     "description": "Access token válido si ya lo tienes. Skip OAuth."},
                    "refresh_token": {"type": "string",
                                      "description": "Refresh token opcional junto con access_token."},
                },
            },
        ),
        types.Tool(
            name="kommo_setup",
            description=(
                "Renegocia tokens cuando expiraron. "
                "Pasa secret_key (del panel Kommo) para obtener tokens sin browser. "
                "Sin secret_key genera URL de autorización que requiere browser."
            ),
            inputSchema={"type": "object", "properties": {
                "secret_key": {"type": "string",
                               "description": "Código secreto desde panel Kommo. Evita el browser."},
            }},
        ),
        types.Tool(
            name="kommo_check_connection",
            description="Verifica si Kommo está conectado y listo.",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── LEADS ──
        types.Tool(
            name="list_leads",
            description="Listar leads. Filtros opcionales: pipeline_id, stage_id, tag, limit.",
            inputSchema={"type": "object", "properties": {
                "pipeline_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
                "tag": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            }},
        ),
        types.Tool(
            name="get_lead",
            description="Obtener lead completo con campos personalizados y tags.",
            inputSchema={"type": "object", "required": ["lead_id"], "properties": {
                "lead_id": {"type": "integer"},
            }},
        ),
        types.Tool(
            name="create_lead",
            description="Crear nuevo lead en Kommo.",
            inputSchema={"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
                "pipeline_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
                "custom_fields": {"type": "array", "items": {"type": "object"}},
                "tags": {"type": "array", "items": {"type": "string"}},
            }},
        ),
        types.Tool(
            name="update_lead",
            description="Actualizar campos de un lead existente.",
            inputSchema={"type": "object", "required": ["lead_id", "fields"], "properties": {
                "lead_id": {"type": "integer"},
                "fields": {"type": "object"},
            }},
        ),
        types.Tool(
            name="move_lead_stage",
            description="Mover lead a un stage específico del pipeline.",
            inputSchema={"type": "object", "required": ["lead_id", "stage_id"], "properties": {
                "lead_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
                "pipeline_id": {"type": "integer"},
            }},
        ),
        # ── CONTACTOS ──
        types.Tool(
            name="list_contacts",
            description="Buscar o listar contactos en Kommo.",
            inputSchema={"type": "object", "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            }},
        ),
        types.Tool(
            name="create_contact",
            description="Crear contacto nuevo en Kommo.",
            inputSchema={"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "custom_fields": {"type": "array", "items": {"type": "object"}},
            }},
        ),
        types.Tool(
            name="update_contact",
            description="Actualizar datos de un contacto existente.",
            inputSchema={"type": "object", "required": ["contact_id", "fields"], "properties": {
                "contact_id": {"type": "integer"},
                "fields": {"type": "object"},
            }},
        ),
        # ── PIPELINES Y STAGES ──
        types.Tool(
            name="list_pipelines",
            description="Listar todos los pipelines de Kommo con sus stages.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="create_pipeline",
            description="Crear pipeline nuevo en Kommo.",
            inputSchema={"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
            }},
        ),
        types.Tool(
            name="update_pipeline",
            description="Renombrar o actualizar un pipeline existente.",
            inputSchema={"type": "object", "required": ["pipeline_id", "name"], "properties": {
                "pipeline_id": {"type": "integer"},
                "name": {"type": "string"},
            }},
        ),
        types.Tool(
            name="list_stages",
            description="Listar stages de un pipeline específico.",
            inputSchema={"type": "object", "required": ["pipeline_id"], "properties": {
                "pipeline_id": {"type": "integer"},
            }},
        ),
        types.Tool(
            name="create_stage",
            description="Crear stage dentro de un pipeline. Color en formato hex (opcional).",
            inputSchema={"type": "object", "required": ["pipeline_id", "name"], "properties": {
                "pipeline_id": {"type": "integer"},
                "name": {"type": "string"},
                "color": {"type": "string", "default": "#99ccff"},
            }},
        ),
        types.Tool(
            name="update_stage",
            description="Renombrar o reordenar un stage existente.",
            inputSchema={"type": "object", "required": ["pipeline_id", "stage_id"], "properties": {
                "pipeline_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
                "name": {"type": "string"},
                "sort": {"type": "integer"},
            }},
        ),
        # ── TAREAS Y NOTAS ──
        types.Tool(
            name="create_task",
            description="Crear tarea de seguimiento para un lead. due_date es Unix timestamp.",
            inputSchema={"type": "object", "required": ["lead_id", "text", "due_date"], "properties": {
                "lead_id": {"type": "integer"},
                "text": {"type": "string"},
                "due_date": {"type": "integer", "description": "Unix timestamp"},
                "responsible_user_id": {"type": "integer"},
            }},
        ),
        types.Tool(
            name="list_tasks",
            description="Listar tareas de un lead o filtrar por vencidas.",
            inputSchema={"type": "object", "properties": {
                "lead_id": {"type": "integer"},
                "filter_overdue": {"type": "boolean", "default": False},
            }},
        ),
        types.Tool(
            name="add_note",
            description="Agregar nota de texto a un lead.",
            inputSchema={"type": "object", "required": ["lead_id", "text"], "properties": {
                "lead_id": {"type": "integer"},
                "text": {"type": "string"},
            }},
        ),
        # ── TAGS ──
        types.Tool(
            name="add_tag",
            description="Agregar tag a un lead. Ej: bajo_monto, mora_activa, reactivar_60d.",
            inputSchema={"type": "object", "required": ["lead_id", "tag_name"], "properties": {
                "lead_id": {"type": "integer"},
                "tag_name": {"type": "string"},
            }},
        ),
        types.Tool(
            name="remove_tag",
            description="Quitar tag de un lead.",
            inputSchema={"type": "object", "required": ["lead_id", "tag_name"], "properties": {
                "lead_id": {"type": "integer"},
                "tag_name": {"type": "string"},
            }},
        ),
        # ── CAMPOS PERSONALIZADOS ──
        types.Tool(
            name="list_custom_fields",
            description="Listar campos personalizados de leads o contactos.",
            inputSchema={"type": "object", "properties": {
                "entity_type": {"type": "string", "enum": ["leads", "contacts"],
                                "default": "leads"},
            }},
        ),
        types.Tool(
            name="create_custom_field",
            description="Crear campo personalizado. field_type: text, numeric, select, date, file.",
            inputSchema={
                "type": "object",
                "required": ["entity_type", "field_type", "name"],
                "properties": {
                    "entity_type": {"type": "string", "enum": ["leads", "contacts"]},
                    "field_type": {"type": "string",
                                   "enum": ["text", "numeric", "select", "date", "file"]},
                    "name": {"type": "string"},
                    "enum_values": {"type": "array", "items": {"type": "string"}},
                },
            },
        ),
        # ── OPERACIONES BULK (con aprobación) ──
        types.Tool(
            name="bulk_move_leads",
            description=(
                "Mover múltiples leads al mismo stage. "
                "Sin `confirmed: true` devuelve un resumen para revisión antes de ejecutar."
            ),
            inputSchema={
                "type": "object",
                "required": ["lead_ids", "stage_id"],
                "properties": {
                    "lead_ids": {"type": "array", "items": {"type": "integer"},
                                 "description": "Lista de IDs de leads a mover"},
                    "stage_id": {"type": "integer"},
                    "pipeline_id": {"type": "integer"},
                    "confirmed": {"type": "boolean", "default": False,
                                  "description": "true para ejecutar, false para solo previsualizar"},
                },
            },
        ),
        types.Tool(
            name="bulk_add_tag",
            description=(
                "Agregar un tag a múltiples leads. "
                "Sin `confirmed: true` devuelve resumen para revisión."
            ),
            inputSchema={
                "type": "object",
                "required": ["lead_ids", "tag_name"],
                "properties": {
                    "lead_ids": {"type": "array", "items": {"type": "integer"}},
                    "tag_name": {"type": "string"},
                    "confirmed": {"type": "boolean", "default": False},
                },
            },
        ),
    ]


def _build_auth_url(subdomain: str, client_id: str) -> str:
    params = urllib.parse.urlencode({"client_id": client_id, "state": "gmcapital"})
    return f"https://{subdomain}.kommo.com/oauth?{params}"


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # ── SETUP TOOLS ───────────────────────────────────────────────────────────
    if name == "kommo_configure":
        client_id = arguments["client_id"].strip()
        client_secret = arguments["client_secret"].strip()
        subdomain = arguments["subdomain"].strip().rstrip("/").split(".")[0]
        secret_key = (arguments.get("secret_key") or "").strip()
        access_token = (arguments.get("access_token") or "").strip()
        refresh_token = (arguments.get("refresh_token") or "").strip()

        set_key(ENV_PATH, "KOMMO_CLIENT_ID", client_id)
        set_key(ENV_PATH, "KOMMO_CLIENT_SECRET", client_secret)
        set_key(ENV_PATH, "KOMMO_SUBDOMAIN", subdomain)
        set_key(ENV_PATH, "KOMMO_REDIRECT_URI", REDIRECT_URI)

        # OPCIÓN A: secret_key → exchange directo sin browser
        if secret_key:
            try:
                _exchange_and_save(client_id, client_secret, secret_key)
                load_dotenv(ENV_PATH, override=True)
                c = _get_client()
                account = c.get("/account")
                return _ok({
                    "status": "connected",
                    "account": account.get("name", ""),
                    "subdomain": account.get("subdomain", ""),
                    "message": "Kommo conectado con secret_key. Todos los tools disponibles.",
                })
            except Exception as e:
                return _err(f"Error al canjear secret_key: {e}")

        # OPCIÓN B: access_token directo
        if access_token:
            set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", access_token)
            set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", refresh_token)
            load_dotenv(ENV_PATH, override=True)
            global _client
            _client = None
            try:
                c = _get_client()
                account = c.get("/account")
                return _ok({
                    "status": "connected",
                    "account": account.get("name", ""),
                    "subdomain": account.get("subdomain", ""),
                    "message": "Kommo conectado con access_token directo.",
                })
            except Exception as e:
                return _err(f"Token inválido: {e}")

        # OPCIÓN C: OAuth con browser (fallback)
        set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", "")
        set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", "")
        auth_url = _build_auth_url(subdomain, client_id)
        _start_callback_listener(client_id, client_secret)

        return _ok({
            "status": "credentials_saved",
            "next_step": "Visita el enlace de autorización para conectar Kommo",
            "auth_url": auth_url,
            "tip": "Para evitar el browser, obtén el 'Código secreto' en Kommo → Ajustes → Integraciones y pásalo como secret_key.",
            "instructions": (
                "1. Haz clic en el enlace de arriba\n"
                "2. Autoriza la integración\n"
                "3. Vuelve aquí y llama a `kommo_check_connection`"
            ),
        })

    if name == "kommo_setup":
        if not _is_configured():
            return _need_setup()

        load_dotenv(ENV_PATH, override=True)
        client_id = os.getenv("KOMMO_CLIENT_ID", "")
        client_secret = os.getenv("KOMMO_CLIENT_SECRET", "")
        subdomain = os.getenv("KOMMO_SUBDOMAIN", "")
        secret_key = (arguments.get("secret_key") or "").strip()

        if secret_key:
            try:
                _exchange_and_save(client_id, client_secret, secret_key)
                load_dotenv(ENV_PATH, override=True)
                c = _get_client()
                account = c.get("/account")
                return _ok({
                    "status": "connected",
                    "account": account.get("name", ""),
                    "subdomain": account.get("subdomain", ""),
                    "message": "Kommo reconectado con secret_key. Todos los tools disponibles.",
                })
            except Exception as e:
                return _err(f"Error al canjear secret_key: {e}")

        set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", "")
        set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", "")
        auth_url = _build_auth_url(subdomain, client_id)
        _start_callback_listener(client_id, client_secret)

        return _ok({
            "status": "awaiting_authorization",
            "auth_url": auth_url,
            "tip": "Para evitar el browser, obtén el 'Código secreto' en Kommo → Ajustes → Integraciones y pásalo como secret_key.",
            "instructions": (
                "1. Haz clic en el enlace de arriba\n"
                "2. Autoriza la integración en Kommo\n"
                "3. Vuelve aquí y llama a `kommo_check_connection`"
            ),
        })

    if name == "kommo_check_connection":
        if _oauth_ready.is_set() or _is_authenticated():
            try:
                c = _get_client()
                account = c.get("/account")
                return _ok({
                    "status": "connected",
                    "account": account.get("name", ""),
                    "subdomain": account.get("subdomain", ""),
                    "message": "Kommo conectado. Todos los tools están disponibles.",
                })
            except Exception as e:
                return _err(f"Tokens guardados pero error al conectar: {e}")
        return _ok({
            "status": "pending",
            "message": "Aún esperando autorización. Visita el enlace y autoriza en Kommo, luego vuelve a llamar este tool.",
        })

    # ── GUARDIA: todos los demás tools requieren auth ─────────────────────────
    if not _is_configured():
        return _need_setup()
    if not _is_authenticated():
        return _need_auth()

    # ── VALIDACIÓN DE INPUTS ──────────────────────────────────────────────────
    if name in TOOL_RULES:
        error = _validate(arguments, TOOL_RULES[name])
        if error:
            return _err(f"Validación: {error}")

    try:
        c = _get_client()
        match name:
            case "list_leads":
                return _ok(c.list_leads(**arguments))
            case "get_lead":
                return _ok(c.get_lead(arguments["lead_id"]))
            case "create_lead":
                return _ok(c.create_lead(**arguments))
            case "update_lead":
                return _ok(c.update_lead(arguments["lead_id"], arguments["fields"]))
            case "move_lead_stage":
                return _ok(c.move_lead_stage(**arguments))
            case "list_contacts":
                return _ok(c.list_contacts(**arguments))
            case "create_contact":
                return _ok(c.create_contact(**arguments))
            case "update_contact":
                return _ok(c.update_contact(arguments["contact_id"], arguments["fields"]))
            case "list_pipelines":
                return _ok(c.list_pipelines())
            case "create_pipeline":
                return _ok(c.create_pipeline(arguments["name"]))
            case "update_pipeline":
                return _ok(c.update_pipeline(arguments["pipeline_id"], arguments["name"]))
            case "list_stages":
                return _ok(c.list_stages(arguments["pipeline_id"]))
            case "create_stage":
                return _ok(c.create_stage(**arguments))
            case "update_stage":
                return _ok(c.update_stage(**arguments))
            case "create_task":
                return _ok(c.create_task(**arguments))
            case "list_tasks":
                return _ok(c.list_tasks(**arguments))
            case "add_note":
                return _ok(c.add_note(arguments["lead_id"], arguments["text"]))
            case "add_tag":
                return _ok(c.add_tag(arguments["lead_id"], arguments["tag_name"]))
            case "remove_tag":
                return _ok(c.remove_tag(arguments["lead_id"], arguments["tag_name"]))
            case "list_custom_fields":
                return _ok(c.list_custom_fields(arguments.get("entity_type", "leads")))
            case "create_custom_field":
                return _ok(c.create_custom_field(**arguments))

            # ── BULK OPS ──────────────────────────────────────────────────────
            case "bulk_move_leads":
                lead_ids: list = arguments["lead_ids"]
                stage_id: int = arguments["stage_id"]
                pipeline_id: int | None = arguments.get("pipeline_id")
                confirmed: bool = arguments.get("confirmed", False)

                if not confirmed:
                    return _ok({
                        "preview": True,
                        "action": "bulk_move_leads",
                        "leads_count": len(lead_ids),
                        "lead_ids": lead_ids,
                        "target_stage_id": stage_id,
                        "warning": f"Esta acción moverá {len(lead_ids)} leads al stage {stage_id}.",
                        "next_step": "Llama de nuevo con confirmed=true para ejecutar.",
                    })

                results = {"moved": [], "errors": []}
                for lid in lead_ids:
                    try:
                        c.move_lead_stage(lid, stage_id, pipeline_id)
                        results["moved"].append(lid)
                    except Exception as e:
                        results["errors"].append({"lead_id": lid, "error": str(e)})
                return _ok({
                    "status": "completed",
                    "moved": len(results["moved"]),
                    "errors": len(results["errors"]),
                    "detail": results,
                })

            case "bulk_add_tag":
                lead_ids = arguments["lead_ids"]
                tag_name: str = arguments["tag_name"]
                confirmed = arguments.get("confirmed", False)

                if not confirmed:
                    return _ok({
                        "preview": True,
                        "action": "bulk_add_tag",
                        "leads_count": len(lead_ids),
                        "lead_ids": lead_ids,
                        "tag": tag_name,
                        "warning": f"Se agregará el tag '{tag_name}' a {len(lead_ids)} leads.",
                        "next_step": "Llama de nuevo con confirmed=true para ejecutar.",
                    })

                results = {"tagged": [], "errors": []}
                for lid in lead_ids:
                    try:
                        c.add_tag(lid, tag_name)
                        results["tagged"].append(lid)
                    except Exception as e:
                        results["errors"].append({"lead_id": lid, "error": str(e)})
                return _ok({
                    "status": "completed",
                    "tagged": len(results["tagged"]),
                    "errors": len(results["errors"]),
                    "detail": results,
                })

            case _:
                return _err(f"Tool desconocido: {name}")
    except Exception as e:
        return _err(str(e))


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream,
                      app.create_initialization_options())


if __name__ == "__main__":
    # Si ya hay tokens, inicializar el cliente de inmediato
    load_dotenv(ENV_PATH, override=True)
    if os.getenv("KOMMO_ACCESS_TOKEN"):
        _client = KommoClient()
    asyncio.run(main())
