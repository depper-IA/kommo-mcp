<div align="center">

# [Kommo MCP Server]

**A Model Context Protocol (MCP) server connecting AI Assistants to the Kommo CRM API v4.**
*Manage leads, contacts, pipelines, tasks, and more—directly from your AI Assistant conversations.*

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Kommo API](https://img.shields.io/badge/Kommo-API_v4-0052cc.svg)](https://www.kommo.com/)

</div>

---

## Features

- [*] **26 Tools:** Complete coverage for the Kommo CRM workflow.
- [*] **Interactive OAuth Setup:** Authenticate directly within the AI client—no terminal needed.
- [*] **Smart Caching:** Fast responses (pipelines cached 10m, custom fields 1h).
- [*] **Input Validation:** Prevents bad requests with clear error messages before hitting the API.
- [*] **Bulk Operations with Approval:** Preview changes before executing mass updates safely.
- [*] **Auto Token Refresh:** Access tokens are renewed automatically upon expiry.
- [*] **Zero Manual Steps:** Smooth sailing after initial setup.

---

## Requirements

- **Python 3.10+**
- A **Kommo account** with an OAuth integration created.
- **Any MCP-compatible client** (e.g., Claude Code, Cursor, OpenCode, Kiro, Windsurf).

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/depper-IA/kommo-mcp.git
   cd kommo-mcp
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in your Kommo OAuth credentials:
   ```env
   KOMMO_CLIENT_ID=your_client_id
   KOMMO_CLIENT_SECRET=your_client_secret
   KOMMO_SUBDOMAIN=yoursubdomain
   KOMMO_REDIRECT_URI=http://localhost:8080/callback
   ```
   > [!] **How to get credentials:** Go to your Kommo account -> **Settings** -> **Integrations** -> **Create Integration**. Set the redirect URI to `http://localhost:8080/callback`.

---

## Setup in an MCP Client

Add the server to your project's `.mcp.json` or your client's MCP configuration:

```json
{
  "mcpServers": {
    "kommo": {
      "command": "python3",
      "args": ["/absolute/path/to/kommo-mcp/kommo_mcp.py"]
    }
  }
}
```

Restart your client. The `kommo` MCP server will be available immediately!

---

## Authentication

Authentication happens interactively inside the AI Assistant. 

**First time:**
1. In the chat, say: *"Connect my Kommo account"*
2. The AI calls `kommo_configure` with your credentials.
3. Click the generated authorization URL.
4. Authorize in Kommo (one click).
5. Return to the chat and say: *"Check Kommo connection"*
6. The AI confirms: [OK] **Connected**

**Re-authentication** (if tokens expire):
Say *"Reconnect Kommo"* — The AI will call `kommo_setup` and provide a new authorization link.

---

## Tools Reference

### Setup
| Tool | Description |
|---|---|
| `kommo_configure` | Save credentials and start OAuth flow. Call this first. |
| `kommo_setup` | Re-authenticate using saved credentials. |
| `kommo_check_connection` | Verify the connection is active and working. |

### Leads
| Tool | Description | Key Parameters |
|---|---|---|
| `list_leads` | List leads with optional filters | `pipeline_id`, `stage_id`, `tag`, `limit` |
| `get_lead` | Get a lead with custom fields & tags | `lead_id` |
| `create_lead` | Create a new lead | `name`, `pipeline_id`, `stage_id`, `tags`, `custom_fields` |
| `update_lead` | Update any lead field | `lead_id`, `fields` |
| `move_lead_stage` | Move a lead to a specific stage | `lead_id`, `stage_id` |

### Contacts
| Tool | Description | Key Parameters |
|---|---|---|
| `list_contacts` | Search or list contacts | `query`, `limit` |
| `create_contact` | Create a new contact | `name`, `phone`, `email` |
| `update_contact` | Update a contact's data | `contact_id`, `fields` |

### Pipelines & Stages
| Tool | Description | Key Parameters |
|---|---|---|
| `list_pipelines` | List all pipelines with stages | — |
| `create_pipeline` | Create a new pipeline | `name` |
| `update_pipeline` | Rename an existing pipeline | `pipeline_id`, `name` |
| `list_stages` | List stages for a pipeline | `pipeline_id` |
| `create_stage` | Add a stage to a pipeline | `pipeline_id`, `name`, `color` |
| `update_stage` | Rename or reorder a stage | `pipeline_id`, `stage_id`, `name`, `sort` |

### Tasks & Notes
| Tool | Description | Key Parameters |
|---|---|---|
| `create_task` | Create a follow-up task on a lead | `lead_id`, `text`, `due_date` (Unix timestamp) |
| `list_tasks` | List tasks for a lead or filter overdue | `lead_id`, `filter_overdue` |
| `add_note` | Append a note to a lead | `lead_id`, `text` |

### Tags & Custom Fields
| Tool | Description | Key Parameters |
|---|---|---|
| `add_tag` | Add a tag to a lead | `lead_id`, `tag_name` |
| `remove_tag` | Remove a tag from a lead | `lead_id`, `tag_name` |
| `list_custom_fields` | List all custom fields | `entity_type` (`leads` or `contacts`) |
| `create_custom_field` | Create a custom field | `entity_type`, `field_type`, `name`, `enum_values` |
*Supported field types: `text`, `numeric`, `select`, `date`, `file`*

### Bulk Operations
Bulk tools use a **preview -> confirm** pattern to prevent accidental mass changes.

| Tool | Description | Key Parameters |
|---|---|---|
| `bulk_move_leads` | Move multiple leads to a stage | `lead_ids`, `stage_id`, `confirmed` |
| `bulk_add_tag` | Add a tag to multiple leads | `lead_ids`, `tag_name`, `confirmed` |

<details>
<summary><b>How bulk approval works</b> (Click to expand)</summary>

```python
# Step 1 — Preview (confirmed not set or false)
bulk_move_leads(lead_ids=[101, 102, 103], stage_id=456)
# Returns: { "preview": true, "leads_count": 3, "warning": "Will move 3 leads to stage 456" }

# Step 2 — Execute
bulk_move_leads(lead_ids=[101, 102, 103], stage_id=456, confirmed=true)
# Returns: { "status": "completed", "moved": 3, "errors": 0 }
```
</details>

---

## Caching Behavior

| Resource | TTL | Invalidated when |
|---|---|---|
| **Pipelines** | 10 minutes | Pipeline or stage created/updated |
| **Stages** (per pipeline) | 10 minutes | Stage created/updated |
| **Custom fields** | 1 hour | Custom field created |

---

## Project Structure

```text
kommo-mcp/
├── kommo_mcp.py       # MCP server — 26 tools, OAuth flow, validation
├── kommo_client.py    # Kommo API v4 client — HTTP, auth refresh, caching
├── auth.py            # Standalone OAuth script (optional, manual use)
├── .env               # Your credentials (gitignored)
├── .env.example       # Credential template
└── requirements.txt   # Python dependencies
```

---

## Manual Re-authentication (Optional)

If you prefer a terminal-based auth flow over the chat integration:
```bash
python3 auth.py
```
This opens a browser, captures the OAuth callback, and saves tokens directly to your `.env` file.

---

## Contributing

Pull requests are always welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## Authors

Developed by:
- **Sam Wilkie** - [LinkedIn](https://www.linkedin.com/in/samu-wilkie/) | [Website](https://sam.wilkiedevs.com)
- **Juliana Urbano** - [LinkedIn](https://www.linkedin.com/in/ummel/)

---

<div align="center">
  <i>Released under the <a href="https://opensource.org/licenses/MIT">MIT License</a>.</i>
</div>