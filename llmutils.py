"""
llmutils.py

Utilities to ask an LLM to produce a read-only SQLite `SELECT` statement
based on provided table metadata and a user search request.

This module will look for LLM configuration in environment variables or a
`llm_config.json` file placed next to this module. Recognized values are
`api_key`, `api_base` (base URL), and optional `model`.

Functions:
 - configure_openai(api_base=None, api_key=None, model=None)
 - build_prompt(table_name, metadata, user_request)
 - extract_sql(text)
 - generate_sql(table_name, metadata, user_request, model=None)
 - validate_sql(sql, table_name)
 - run_sqlite_query(db_path, sql)
"""
from typing import Dict, Any, Optional, List, Union
import re
import json
import os
from pathlib import Path
from openai import OpenAI
CLIENT: Optional[OpenAI] = None

# --- Configuration ---
# Default fallbacks; overridden by environment variables or `llm_config.json`.
API_BASE = ""
API_KEY = ""
DEFAULT_MODEL = "gpt-5.2"

# Config file (optional) placed next to this module. Example JSON:
# { "api_key": "...", "api_base": "https://.../v1", "model": "gpt-5.2" }
CONFIG_FILE = Path(__file__).resolve().parent / "llm_config.json"


def _load_llm_config() -> Dict[str, Optional[str]]:
    """Load LLM settings from environment variables or the config file.

    Environment variables (preferred): `OPENAI_API_KEY`, `OPENAI_API_BASE`, `LLM_MODEL`.
    Config file fallback: `llm_config.json` with keys `api_key`, `api_base`, `model`.
    """
    cfg: Dict[str, Optional[str]] = {"api_key": None, "api_base": None, "model": None}
    # env overrides
    cfg["api_key"] = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    cfg["api_base"] = os.getenv("OPENAI_API_BASE") or os.getenv("LLM_API_BASE")
    cfg["model"] = os.getenv("LLM_MODEL")

    if any(cfg.values()):
        return cfg

    # try config file
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg["api_key"] = data.get("api_key") or data.get("apikey")
            cfg["api_base"] = data.get("api_base") or data.get("base_url") or data.get("api_base_url")
            cfg["model"] = data.get("model")
    except Exception:
        # ignore config parse errors and fall back to defaults
        pass

    return cfg


def configure_openai(api_base: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None):
    """Configure the OpenAI client using explicit args, environment, or config file.

    Priority order for `api_key` and `api_base`:
    1. Explicit function arguments
    2. Environment variables (`OPENAI_API_KEY`, `OPENAI_API_BASE`)
    3. `llm_config.json` file next to this module
    4. Module defaults (`API_KEY`, `API_BASE`)

    If `model` is provided (or available via env/config) it will override
    the module `DEFAULT_MODEL` for subsequent calls.
    """
    global CLIENT, DEFAULT_MODEL
    cfg = _load_llm_config()

    base = api_base or cfg.get("api_base") or API_BASE
    key = api_key or cfg.get("api_key") or API_KEY
    # model preference: explicit > env/config > DEFAULT_MODEL
    chosen_model = model or cfg.get("model")
    if chosen_model:
        DEFAULT_MODEL = chosen_model

    CLIENT = OpenAI(api_key=key, base_url=base)


def build_prompt(table_name: str, metadata: Union[Dict[str, Any], str], user_request: str) -> str:
    """Create a clear instruction prompt for the LLM.

    `metadata` may be either a parsed dict (old format) or a plaintext string
    representing the metadata file contents (e.g. the `tmp/*.txt` files).

    When given plaintext, this function will attempt a lightweight parse to
    extract `column_names` if present and include both the raw metadata and
    a short parsed view in the prompt to help the LLM.
    """
    # If metadata is a plain string, attempt to parse column names and rows
    parsed_cols: List[str] = []
    meta_text = ""
    if isinstance(metadata, str):
        meta_text = metadata
        # look for a line like: column_names: a, b, c
        m = re.search(r"column_names:\s*(.*)", metadata, re.I)
        if m:
            parsed_cols = [c.strip() for c in m.group(1).split(",") if c.strip()]
    elif isinstance(metadata, dict):
        # keep compatibility with existing dict-shaped metadata
        meta_text = json.dumps(metadata, ensure_ascii=False)
        cols = metadata.get("columns") or metadata.get("column_names") or []
        parsed_cols = [c["name"] if isinstance(c, dict) and c.get("name") else c for c in cols]

    columns_text = "\n".join([f"- {c}" for c in parsed_cols]) if parsed_cols else "(no column list available)"

    prompt = (
        "You are an assistant that writes precise SQLite SELECT statements.\n"
        "The user will provide a search request describing what data they want.\n"
        "Constraints:\n"
        "- Output ONLY a single valid SQLite `SELECT` statement and nothing else.\n"
        "- Do not include any surrounding explanation, markdown, or code fences.\n"
        "- The statement must be read-only (SELECT). No INSERT/UPDATE/DELETE/PRAGMA/ATTACH.\n"
        "- Use the table name exactly as provided.\n"
        "- Use SQLite-compatible syntax.\n"
        "- Prefer explicit column lists (not SELECT *), unless user requests all columns.\n"
        "Here is the table metadata (raw):\n"
        f"{meta_text}\n\n"
        "Parsed columns:\n"
        f"{columns_text}\n\n"
        f"User request: {user_request}\n\n"
        "Return a single SELECT statement that answers the user request." 
    )
    return prompt


def extract_sql(text: str) -> Optional[str]:
    """Extract an SQL statement from the model output.

    Accept code fences with sql or plain SQL. Return the first SQL-looking statement.
    """
    if not text:
        return None

    # strip leading/trailing whitespace
    s = text.strip()

    # If model returned code fences, extract inner content
    m = re.search(r"```(?:sql)?\n(.*?)```", s, re.S | re.I)
    if m:
        candidate = m.group(1).strip()
    else:
        candidate = s

    # If there are multiple statements, take the first one ending with a semicolon or newline
    # Normalize whitespace
    candidate = candidate.strip()
    # remove trailing semicolon(s)
    candidate = candidate.rstrip(';')

    # Sometimes model may include an explanation line; find the first line that starts with SELECT
    lines = [ln.strip() for ln in candidate.splitlines() if ln.strip()]
    for i in range(len(lines)):
        if re.match(r"^(select)\b", lines[i], re.I):
            sql = " ".join(lines[i:])
            return sql.strip()

    # fallback: if the whole candidate starts with select
    if re.match(r"^select\b", candidate, re.I):
        return candidate

    return None


def validate_sql(sql: str, table_name: str) -> bool:
    """Basic validation: ensure it's a SELECT, no forbidden keywords, and uses table name."""
    if not sql:
        return False
    s = sql.strip()
    if not re.match(r"^select\b", s, re.I):
        return False

    # disallow write or schema-changing statements
    forbidden = re.compile(r"\b(drop|delete|insert|update|alter|create|attach|detach|pragma)\b", re.I)
    if forbidden.search(s):
        return False

    # table_name presence (simple check)
    if table_name not in s:
        # allow quoted table names
        if f'"{table_name}"' not in s and f"'{table_name}'" not in s:
            return False

    return True


def generate_sql(table_name: str, metadata: Union[Dict[str, Any], str], user_request: str, model: Optional[str] = None, max_tokens: int = 256) -> Dict[str, Any]:
    """Ask the LLM to produce a SELECT SQL statement.

    Returns a dict: {"sql": str or None, "raw": str (model output), "ok": bool, "error": str}
    """
    model_to_use = model or DEFAULT_MODEL
    prompt = build_prompt(table_name, metadata, user_request)

    # messages style for chat completion
    messages = [
        {"role": "system", "content": "You generate only a single SQLite SELECT statement based on user instructions and table metadata."},
        {"role": "user", "content": prompt},
    ]

    try:
        global CLIENT
        if CLIENT is None:
            configure_openai()
        resp = CLIENT.chat.completions.create(model=model_to_use, messages=messages, max_tokens=max_tokens, temperature=0)
        raw = resp.choices[0].message.content
    except Exception as e:
        return {"sql": None, "raw": "", "ok": False, "error": f"LLM request failed: {e}"}

    sql = extract_sql(raw)
    if not sql:
        return {"sql": None, "raw": raw, "ok": False, "error": "could not extract SQL from model output"}

    if not validate_sql(sql, table_name):
        return {"sql": sql, "raw": raw, "ok": False, "error": "SQL failed validation (non-SELECT, forbidden keywords, or table name missing)"}

    return {"sql": sql, "raw": raw, "ok": True, "error": ""}





# Module is intended to be imported and used as a helper; no CLI/demo code.
