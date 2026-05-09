import os
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv, set_key
import httpx

load_dotenv()

CLIENT_ID = os.getenv("KOMMO_CLIENT_ID")
CLIENT_SECRET = os.getenv("KOMMO_CLIENT_SECRET")
SUBDOMAIN = os.getenv("KOMMO_SUBDOMAIN")
REDIRECT_URI = os.getenv("KOMMO_REDIRECT_URI")
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Autorizado. Puedes cerrar esta ventana.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no se recibio codigo de autorizacion.")

    def log_message(self, format, *args):
        pass


def get_auth_url():
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "state": "gmcapital",
        "mode": "popup",
    })
    return f"https://{SUBDOMAIN}.kommo.com/oauth?{params}"


def exchange_code(code: str) -> dict:
    resp = httpx.post(
        "https://kommo.com/oauth2/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    return resp.json()


def save_tokens(data: dict):
    set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", data["access_token"])
    set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", data["refresh_token"])
    print(f"Tokens guardados en .env")
    print(f"access_token expira en: {data.get('expires_in', '?')} segundos")


def main():
    auth_url = get_auth_url()
    print(f"\nAbriendo navegador para autorizar Kommo...")
    print(f"URL: {auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    print("Esperando callback en http://localhost:8080/callback ...")
    server.handle_request()

    if not auth_code:
        print("No se recibio codigo. Intenta de nuevo.")
        return

    print(f"Codigo recibido. Intercambiando por tokens...")
    data = exchange_code(auth_code)
    save_tokens(data)
    print("\nAutenticacion completada. Ya puedes usar el MCP.")


if __name__ == "__main__":
    main()
