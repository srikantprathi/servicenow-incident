# ServiceNow MCP Server (Incident Management)

An [MCP](https://modelcontextprotocol.io) server that exposes ServiceNow
**incident management** as tools, using the ServiceNow Table API with a local
login (username/password basic auth).

Instance: `https://inmorphisservicespvtltddemo16.service-now.com`

## Tools

| Tool | Description |
|------|-------------|
| `create_incident` | Create a new incident |
| `get_incident` | Fetch one incident by number (`INC...`) or sys_id |
| `search_incidents` | Search with a ServiceNow encoded query |
| `update_incident` | Update arbitrary fields on an incident |
| `add_comment` | Add a public comment or internal work note |
| `resolve_incident` | Resolve with close code + close notes |

## Setup

```bash
cd servicenow-mcp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your ServiceNow username and password
```

The login account needs the `itil` role (or equivalent) and read/write access
to the `incident` table.

## Run / test locally

Load env vars and launch the MCP Inspector:

```bash
set -a; source .env; set +a
mcp dev server.py
```

Or run the server directly over stdio:

```bash
set -a; source .env; set +a
python server.py
```

## Connect to Claude Code

Add it to your MCP config (`.mcp.json` in a project, or via `claude mcp add`):

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "/Users/srikantprathi/Documents/servicenow-mcp/.venv/bin/python",
      "args": ["/Users/srikantprathi/Documents/servicenow-mcp/server.py"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://inmorphisservicespvtltddemo16.service-now.com",
        "SERVICENOW_USERNAME": "your_username",
        "SERVICENOW_PASSWORD": "your_password"
      }
    }
  }
}
```

## Example queries (encoded query syntax)

- All active P1s: `active=true^priority=1`
- Unassigned, in progress: `assigned_toISEMPTY^state=2`
- Opened by you today: `opened_by=javascript:gs.getUserID()^opened_atONToday@...`

## Notes

- `state` accepts friendly names (`new`, `in progress`, `on hold`, `resolved`,
  `closed`, `canceled`) or numeric codes.
- Records are returned with **display values** so references like
  `assignment_group` and `caller_id` are human-readable.
- Credentials are read from env vars only and never logged. Keep `.env` out of
  version control (it's gitignored).
