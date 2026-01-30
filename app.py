
from flask import Flask, request, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
import os
from pathlib import Path
import sqlite3
from datetime import datetime
import pandas as pd
from typing import List
from llmutils import generate_sql, configure_openai
from flask_cors import CORS

ALLOWED_EXTENSIONS = {"xls", "xlsx", "csv", "ods"}


def allowed_file(filename: str) -> bool:
	return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE_DIR = BASE_DIR / "template"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = str(TMP_DIR)
app.config["TEMPLATE_FOLDER"] = str(TEMPLATE_DIR)

# Enable CORS for the app (allow access from other origins)
CORS(app)

# Static folders: serve frontend assets (index.html, favicon, /assets/...)
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = STATIC_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.config["STATIC_FOLDER"] = str(STATIC_DIR)


@app.route("/browse", methods=["GET"])
def browse_tmp():
	"""Return a JSON list of filenames in the tmp folder."""
	p = Path(app.config["UPLOAD_FOLDER"])
	files = [f.name for f in p.iterdir() if f.is_file()]
	return jsonify({"files": files})


@app.route("/convert/<path:filename>", methods=["POST"])
def convert_to_sqlite(filename: str):
	"""Convert an Excel/CSV file in the tmp folder to a sqlite DB and write a metadata txt file.

	The created files are placed in the same `tmp/` folder and share the same base name:
	- `<name>.db` (SQLite DB)
	- `<name>.txt` (metadata)
	"""
	safe_name = secure_filename(filename)
	src_path = Path(app.config["UPLOAD_FOLDER"]) / safe_name
	if not src_path.exists():
		return jsonify({"error": "source file not found"}), 404

	suffix = src_path.suffix.lower()
	base = src_path.stem

	try:
		# For Excel files, read all sheets into a dict; for CSV read a single DataFrame
		if suffix in (".xls", ".xlsx"):
			sheets = pd.read_excel(src_path, sheet_name=None)
			# sheets is a dict: {sheet_name: DataFrame}
		elif suffix == ".csv":
			sheets = {"data": pd.read_csv(src_path)}
		else:
			return jsonify({"error": "unsupported file type for conversion"}), 400
	except Exception as e:
		return jsonify({"error": "failed to read source file", "detail": str(e)}), 500

	db_path = Path(app.config["UPLOAD_FOLDER"]) / f"{base}.db"
	try:
		conn = sqlite3.connect(db_path)
		# write each sheet as its own table, sanitizing table names
		table_names = []
		for sheet_name, df in sheets.items():
			# sanitize table name: keep alnum and underscore
			table = __import__('re').sub(r"[^0-9a-zA-Z_]+", "_", str(sheet_name))
			if table == "":
				table = "data"
			table_names.append((table, df))
			# write dataframe to sqlite under table name
			df.to_sql(table, conn, if_exists="replace", index=False)
		conn.commit()
		# after writing, gather schema info
		cur = conn.cursor()
		cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
		tables = cur.fetchall()

		schema_info = {}
		for name, create_sql in tables:
			# get columns info
			cur.execute(f"PRAGMA table_info('{name}')")
			cols = cur.fetchall()  # (cid, name, type, notnull, dflt_value, pk)
			cur.execute(f"SELECT COUNT(*) FROM \"{name}\"")
			row_count = cur.fetchone()[0]
			schema_info[name] = {
				"create_sql": create_sql,
				"rows": row_count,
				"columns": [{
					"cid": c[0],
					"name": c[1],
					"type": c[2],
					"notnull": bool(c[3]),
					"default": c[4],
					"pk": bool(c[5]),
				} for c in cols]
			}

		conn.close()
	except Exception as e:
		return jsonify({"error": "failed to write sqlite db or gather schema", "detail": str(e)}), 500

	# create metadata file with richer schema details
	meta_path = Path(app.config["UPLOAD_FOLDER"]) / f"{base}.txt"
	try:
		with open(meta_path, "w", encoding="utf-8") as mf:
			mf.write(f"source: {safe_name}\n")
			mf.write(f"created: {datetime.utcnow().isoformat()}Z\n")
			# overall summary
			total_rows = sum(info.get("rows", 0) for info in schema_info.values())
			mf.write(f"tables: {len(schema_info)}\n")
			mf.write(f"total_rows: {total_rows}\n")
			# per-table details
			for tname, info in schema_info.items():
				mf.write(f"\n[table] {tname}\n")
				mf.write(f"rows: {info.get('rows')}\n")
				mf.write(f"create_sql: {info.get('create_sql')}\n")
				mf.write(f"columns: {len(info.get('columns', []))}\n")
				mf.write("column_details:\n")
				for col in info.get('columns', []):
					mf.write(f"- {col['name']}: {col['type']}, pk={col['pk']}, notnull={col['notnull']}, default={col['default']}\n")
	except Exception as e:
		return jsonify({"error": "failed to write metadata file", "detail": str(e)}), 500

	return jsonify({
		"db": db_path.name,
		"metadata": meta_path.name,
		"tables": list(schema_info.keys()),
		"total_rows": total_rows,
	}), 201


@app.route("/sql/<path:filename>", methods=["POST"])
def execute_sql(filename: str):
	"""Execute a read-only SELECT SQL statement against a sqlite DB in `tmp/`.

	Request JSON body: {"query": "SELECT ..."}
	Only statements beginning with SELECT are allowed (read-only).
	Returns JSON: {"columns": [...], "rows": [{col: val, ...}, ...]}
	"""
	safe_name = secure_filename(filename)
	db_path = Path(app.config["UPLOAD_FOLDER"]) / safe_name
	if not db_path.exists():
		return jsonify({"error": "db file not found"}), 404

	body = request.get_json(silent=True) or {}
	query = body.get("query") if isinstance(body, dict) else None
	if not query:
		query = request.values.get("query")
	if not query:
		return jsonify({"error": "missing query"}), 400

	qs = query.strip()
	if not qs.lower().startswith("select"):
		return jsonify({"error": "only SELECT queries are allowed"}), 400

	try:
		conn = sqlite3.connect(db_path)
		conn.row_factory = sqlite3.Row
		cur = conn.cursor()
		cur.execute(qs)
		rows = cur.fetchall()
		cols: List[str]
		if cur.description:
			cols = [d[0] for d in cur.description]
		else:
			cols = []
		result = [dict(r) for r in rows]
		conn.close()
	except Exception as e:
		return jsonify({"error": "query failed", "detail": str(e)}), 500

	return jsonify({"columns": cols, "rows": result})


@app.route("/llm/generate/<path:db_filename>", methods=["POST"])
def llm_generate(db_filename: str):
	"""Generate SQL via the LLM for a given DB filename.

	Request JSON: {"request": "natural language request", "table": "optional_table_name"}
	Returns JSON from `generate_sql` (sql, raw, ok, error).
	"""
	safe_name = secure_filename(db_filename)
	db_path = Path(app.config["UPLOAD_FOLDER"]) / safe_name
	if not db_path.exists():
		return jsonify({"error": "db file not found"}), 404

	body = request.get_json(silent=True) or {}
	user_request = body.get("request") or body.get("query") or request.values.get("request")
	if not user_request:
		return jsonify({"error": "missing request"}), 400

	table = body.get("table") or "data"

	# Read metadata plaintext if available
	meta_path = Path(app.config["UPLOAD_FOLDER"]) / f"{Path(safe_name).stem}.txt"
	if meta_path.exists():
		try:
			metadata_text = meta_path.read_text(encoding="utf-8")
		except Exception as e:
			return jsonify({"error": "failed to read metadata file", "detail": str(e)}), 500
	else:
		# Fallback: construct simple metadata from sqlite schema
		try:
			conn = sqlite3.connect(db_path)
			cur = conn.cursor()
			cur.execute(f"PRAGMA table_info('{table}')")
			cols = cur.fetchall()
			cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
			try:
				row_count = cur.fetchone()[0]
			except Exception:
				row_count = 0
			conn.close()
			col_names = ", ".join([c[1] for c in cols])
			metadata_text = f"source: {safe_name}\nrows: {row_count}\ncolumns: {len(cols)}\ncolumn_names: {col_names}\n"
		except Exception as e:
			metadata_text = f"source: {safe_name}\n(could not read schema: {e})\n"

	# ensure llm client configured
	try:
		configure_openai()
	except Exception:
		pass

	result = generate_sql(table, metadata_text, user_request)
	return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
	return jsonify({"status": "ok"})


@app.route("/upload", methods=["POST"])
def upload_file():
	if "file" not in request.files:
		return jsonify({"error": "no file part"}), 400
	file = request.files["file"]
	if file.filename == "":
		return jsonify({"error": "no selected file"}), 400
	if not allowed_file(file.filename):
		return jsonify({"error": "file type not allowed"}), 400
	filename = secure_filename(file.filename)
	save_path = Path(app.config["UPLOAD_FOLDER"]) / filename
	file.save(save_path)
	return jsonify({"filename": filename}), 201


@app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename: str):
	safe_name = secure_filename(filename)
	file_path = Path(app.config["UPLOAD_FOLDER"]) / safe_name
	if not file_path.exists():
		abort(404)
	return send_from_directory(app.config["UPLOAD_FOLDER"], safe_name, as_attachment=True)


@app.route("/template", methods=["GET"])
def download_default_template():
	name = "template.xlsx"
	file_path = Path(app.config["TEMPLATE_FOLDER"]) / name
	if not file_path.exists():
		abort(404)
	return send_from_directory(app.config["TEMPLATE_FOLDER"], name, as_attachment=True)


@app.route("/template/<path:filename>", methods=["GET"])
def download_template(filename: str):
	safe_name = secure_filename(filename)
	file_path = Path(app.config["TEMPLATE_FOLDER"]) / safe_name
	if not file_path.exists():
		abort(404)
	return send_from_directory(app.config["TEMPLATE_FOLDER"], safe_name, as_attachment=True)


# Serve index.html from the static folder
@app.route("/", methods=["GET"])
def serve_index():
	index_path = Path(app.config.get("STATIC_FOLDER", "")) / "index.html"
	print(index_path)
	if not index_path.exists():
		abort(404)
	return send_from_directory(app.config.get("STATIC_FOLDER"), "index.html")


# Serve favicon.ico
@app.route("/favicon.ico", methods=["GET"])
def serve_favicon():
	fav_path = Path(app.config.get("STATIC_FOLDER", "")) / "favicon.ico"
	if not fav_path.exists():
		abort(404)
	return send_from_directory(app.config.get("STATIC_FOLDER"), "favicon.ico")


# Serve files under static/assets/
@app.route("/assets/<path:filename>", methods=["GET"])
def serve_assets(filename: str):
	safe_name = secure_filename(filename)
	file_path = Path(app.config.get("STATIC_FOLDER", "")) / "assets" / safe_name
	if not file_path.exists():
		abort(404)
	return send_from_directory(str(Path(app.config.get("STATIC_FOLDER", "")) / "assets"), safe_name)


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=5000, debug=True)
