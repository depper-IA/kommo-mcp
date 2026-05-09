import os
import time
import httpx
from dotenv import load_dotenv, set_key

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

# ── CACHÉ ─────────────────────────────────────────────────────────────────────

_cache: dict = {}

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["data"]
    _cache.pop(key, None)
    return None

def _cache_set(key: str, data, ttl: int):
    _cache[key] = {"data": data, "expires": time.time() + ttl}

def _cache_invalidate(*keys: str):
    for k in keys:
        _cache.pop(k, None)


def _reload_env():
    load_dotenv(ENV_PATH, override=True)


class KommoClient:
    def __init__(self):
        _reload_env()
        self.subdomain = os.getenv("KOMMO_SUBDOMAIN")
        self.client_id = os.getenv("KOMMO_CLIENT_ID")
        self.client_secret = os.getenv("KOMMO_CLIENT_SECRET")
        self.redirect_uri = os.getenv("KOMMO_REDIRECT_URI")
        self.access_token = os.getenv("KOMMO_ACCESS_TOKEN")
        self.refresh_token = os.getenv("KOMMO_REFRESH_TOKEN")
        self.base_url = f"https://{self.subdomain}.kommo.com/api/v4"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _refresh_tokens(self):
        resp = httpx.post(
            "https://kommo.com/oauth2/access_token",
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "redirect_uri": self.redirect_uri,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        set_key(ENV_PATH, "KOMMO_ACCESS_TOKEN", self.access_token)
        set_key(ENV_PATH, "KOMMO_REFRESH_TOKEN", self.refresh_token)

    def request(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.base_url}{endpoint}"
        resp = httpx.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            self._refresh_tokens()
            resp = httpx.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 204:
            return {}
        resp.raise_for_status()
        return resp.json()

    def get(self, endpoint: str, **kwargs) -> dict:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, json: dict, **kwargs) -> dict:
        return self.request("POST", endpoint, json=json, **kwargs)

    def patch(self, endpoint: str, json: dict, **kwargs) -> dict:
        return self.request("PATCH", endpoint, json=json, **kwargs)

    # ── LEADS ──────────────────────────────────────────────────────────

    def list_leads(self, pipeline_id: int = None, stage_id: int = None,
                   tag: str = None, limit: int = 50) -> list:
        params: dict = {"limit": limit, "with": "contacts,tags"}
        if pipeline_id:
            params["filter[pipeline_id]"] = pipeline_id
        if stage_id and pipeline_id:
            params["filter[statuses][0][pipeline_id]"] = pipeline_id
            params["filter[statuses][0][status_id]"] = stage_id
        if tag:
            params["filter[tags][0]"] = tag
        data = self.get("/leads", params=params)
        return data.get("_embedded", {}).get("leads", [])

    def get_lead(self, lead_id: int) -> dict:
        return self.get(f"/leads/{lead_id}",
                        params={"with": "contacts,tags,custom_fields_values"})

    def create_lead(self, name: str, pipeline_id: int = None, stage_id: int = None,
                    custom_fields: list = None, tags: list = None) -> dict:
        payload: dict = {"name": name}
        if pipeline_id:
            payload["pipeline_id"] = pipeline_id
        if stage_id:
            payload["status_id"] = stage_id
        if custom_fields:
            payload["custom_fields_values"] = custom_fields
        if tags:
            payload["_embedded"] = {"tags": [{"name": t} for t in tags]}
        data = self.post("/leads", json=[payload])
        return data.get("_embedded", {}).get("leads", [{}])[0]

    def update_lead(self, lead_id: int, fields: dict) -> dict:
        return self.patch(f"/leads/{lead_id}", json=fields)

    def move_lead_stage(self, lead_id: int, stage_id: int,
                        pipeline_id: int = None) -> dict:
        payload: dict = {"status_id": stage_id}
        if pipeline_id:
            payload["pipeline_id"] = pipeline_id
        return self.patch(f"/leads/{lead_id}", json=payload)

    # ── CONTACTOS ──────────────────────────────────────────────────────

    def list_contacts(self, query: str = None, limit: int = 50) -> list:
        params: dict = {"limit": limit}
        if query:
            params["query"] = query
        data = self.get("/contacts", params=params)
        return data.get("_embedded", {}).get("contacts", [])

    def create_contact(self, name: str, phone: str = None, email: str = None,
                       custom_fields: list = None) -> dict:
        payload: dict = {"name": name}
        cfv = list(custom_fields) if custom_fields else []
        if phone:
            cfv.append({"field_code": "PHONE",
                        "values": [{"value": phone, "enum_code": "WORK"}]})
        if email:
            cfv.append({"field_code": "EMAIL",
                        "values": [{"value": email, "enum_code": "WORK"}]})
        if cfv:
            payload["custom_fields_values"] = cfv
        data = self.post("/contacts", json=[payload])
        return data.get("_embedded", {}).get("contacts", [{}])[0]

    def update_contact(self, contact_id: int, fields: dict) -> dict:
        return self.patch(f"/contacts/{contact_id}", json=fields)

    # ── PIPELINES Y STAGES ─────────────────────────────────────────────

    def list_pipelines(self) -> list:
        cached = _cache_get("pipelines")
        if cached is not None:
            return cached
        data = self.get("/leads/pipelines")
        result = data.get("_embedded", {}).get("pipelines", [])
        _cache_set("pipelines", result, ttl=600)  # 10 min
        return result

    def create_pipeline(self, name: str) -> dict:
        data = self.post("/leads/pipelines",
                         json=[{"name": name, "is_main": False}])
        _cache_invalidate("pipelines")
        return data.get("_embedded", {}).get("pipelines", [{}])[0]

    def update_pipeline(self, pipeline_id: int, name: str) -> dict:
        _cache_invalidate("pipelines")
        return self.patch(f"/leads/pipelines/{pipeline_id}", json={"name": name})

    def list_stages(self, pipeline_id: int) -> list:
        key = f"stages_{pipeline_id}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = self.get(f"/leads/pipelines/{pipeline_id}/statuses")
        result = data.get("_embedded", {}).get("statuses", [])
        _cache_set(key, result, ttl=600)  # 10 min
        return result

    def create_stage(self, pipeline_id: int, name: str,
                     color: str = "#99ccff") -> dict:
        _cache_invalidate(f"stages_{pipeline_id}", "pipelines")
        data = self.post(f"/leads/pipelines/{pipeline_id}/statuses",
                         json=[{"name": name, "color": color}])
        return data.get("_embedded", {}).get("statuses", [{}])[0]

    def update_stage(self, pipeline_id: int, stage_id: int,
                     name: str = None, sort: int = None) -> dict:
        _cache_invalidate(f"stages_{pipeline_id}", "pipelines")
        payload: dict = {}
        if name:
            payload["name"] = name
        if sort:
            payload["sort"] = sort
        return self.patch(
            f"/leads/pipelines/{pipeline_id}/statuses/{stage_id}", json=payload)

    # ── TAREAS ─────────────────────────────────────────────────────────

    def create_task(self, lead_id: int, text: str, due_date: int,
                    responsible_user_id: int = None) -> dict:
        payload: dict = {
            "text": text,
            "complete_till": due_date,
            "entity_id": lead_id,
            "entity_type": "leads",
            "task_type_id": 1,
        }
        if responsible_user_id:
            payload["responsible_user_id"] = responsible_user_id
        data = self.post("/tasks", json=[payload])
        return data.get("_embedded", {}).get("tasks", [{}])[0]

    def list_tasks(self, lead_id: int = None, filter_overdue: bool = False) -> list:
        params: dict = {}
        if lead_id:
            params["filter[entity_id]"] = lead_id
            params["filter[entity_type]"] = "leads"
        if filter_overdue:
            params["filter[complete_till][to]"] = int(time.time())
            params["filter[is_completed]"] = 0
        data = self.get("/tasks", params=params)
        return data.get("_embedded", {}).get("tasks", [])

    # ── NOTAS ──────────────────────────────────────────────────────────

    def add_note(self, lead_id: int, text: str) -> dict:
        payload = {
            "entity_id": lead_id,
            "note_type": "common",
            "params": {"text": text},
        }
        data = self.post(f"/leads/{lead_id}/notes", json=[payload])
        return data.get("_embedded", {}).get("notes", [{}])[0]

    # ── TAGS ───────────────────────────────────────────────────────────

    def add_tag(self, lead_id: int, tag_name: str) -> dict:
        lead = self.get_lead(lead_id)
        existing = [t["name"] for t in lead.get("_embedded", {}).get("tags", [])]
        if tag_name not in existing:
            existing.append(tag_name)
        return self.patch(f"/leads/{lead_id}",
                          json={"_embedded": {"tags": [{"name": t} for t in existing]}})

    def remove_tag(self, lead_id: int, tag_name: str) -> dict:
        lead = self.get_lead(lead_id)
        existing = [t["name"] for t in lead.get("_embedded", {}).get("tags", [])]
        updated = [t for t in existing if t != tag_name]
        return self.patch(f"/leads/{lead_id}",
                          json={"_embedded": {"tags": [{"name": t} for t in updated]}})

    # ── CAMPOS PERSONALIZADOS ──────────────────────────────────────────

    def list_custom_fields(self, entity_type: str = "leads") -> list:
        key = f"custom_fields_{entity_type}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = self.get(f"/{entity_type}/custom_fields")
        result = data.get("_embedded", {}).get("custom_fields", [])
        _cache_set(key, result, ttl=3600)  # 1 hora
        return result

    def create_custom_field(self, entity_type: str, field_type: str, name: str,
                            enum_values: list = None) -> dict:
        _cache_invalidate(f"custom_fields_{entity_type}")
        payload: dict = {"name": name, "type": field_type}
        if enum_values:
            payload["enums"] = [{"value": v, "sort": i * 10}
                                for i, v in enumerate(enum_values)]
        data = self.post(f"/{entity_type}/custom_fields", json=[payload])
        return data.get("_embedded", {}).get("custom_fields", [{}])[0]
