
Flask Excel upload/download example
==================================

<img width="1861" height="896" alt="QQ_1769761048408" src="https://github.com/user-attachments/assets/1f558096-8de6-44c4-96cd-3b2ea9374aea" />
first upload the excel <br>
then hit convert to convert exvel to db <br>
then choose one to ask ai for a sql to query <br>
you need an opanai endpoint in the config yaml <br>
then choose the db, do query, get result <br>

Install dependencies:
```powershell
pip install -r requirements.txt
```
Run the server:
```powershell
python app.py
```
Upload an Excel file (curl example):
```bash
curl -F "file=@/path/to/your/file.xlsx" http://localhost:5000/upload
```
Download a file:
```bash
curl -O http://localhost:5000/download/file.xlsx
```
Notes:
- Files are stored in the `tmp/` directory next to `app.py`.
- Allowed extensions: xls, xlsx, csv, ods.

Flask Excel upload/download example
==================================
<img width="1861" height="896" alt="QQ_1769761048408" src="https://github.com/user-attachments/assets/1f558096-8de6-44c4-96cd-3b2ea9374aea" />
先上传文档 <br>
点击转化<br>
选择一个db，问ai要它写个sql给你<br>
需要config yaml里面加上openai的api，支持应该就行 <br>
然后你可以选一个db分开执行 <br>

Install dependencies:

```powershell
pip install -r requirements.txt
````
Flask Excel upload/download example
==================================
Flask Excel 上传/下载 示例

Install dependencies:
安装依赖：

```powershell
pip install Flask Werkzeug
```

中文说明：运行 `pip install Flask Werkzeug` 来安装依赖。

Run the server:
运行服务器：

```powershell
python app.py
```

中文说明：运行 `python app.py` 启动服务器，默认在 http://localhost:5000 监听。

Upload an Excel file (curl example):
上传 Excel 文件（curl 示例）：

```bash
curl -F "file=@/path/to/your/file.xlsx" http://localhost:5000/upload
```

中文说明：将本地文件 `/path/to/your/file.xlsx` 上传到 `http://localhost:5000/upload`。

Download a file:
下载文件：

```bash
curl -O http://localhost:5000/download/file.xlsx
```

中文说明：从 `http://localhost:5000/download/file.xlsx` 下载文件到当前目录。

Using uv:
使用 `uv`：

```bash
uv init .
uv add -r requirements.txt
uv run app.py
```

中文说明：
- `uv init .`：在当前目录初始化 `uv` 项目。
- `uv add -r requirements.txt`：从 `requirements.txt` 批量添加依赖到 `uv` 项目。
- `uv run app.py`：使用 `uv` 运行 `app.py` 启动项目/服务。

Notes:
注意：
- Files are stored in the `tmp/` directory next to `app.py`.
- 文件会存储在与 `app.py` 同级的 `tmp/` 目录中。
- Allowed extensions: xls, xlsx, csv, ods.
- 允许的扩展名：xls、xlsx、csv、ods。
```
