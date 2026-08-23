import os
import re
import json
from flask import (
    Flask, render_template, render_template_string, request,
    redirect, url_for, session, jsonify, send_from_directory,
    make_response, abort
)
from supabase import create_client
from chat import generate_reply
from PIL import Image
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.parse
import zlib
import base64
import random
import string
import uuid
import secrets
import time
from datetime import datetime

# ─────────────────────────────────────────
# 基本設定
# ─────────────────────────────────────────
# -*- coding: utf-8 -*-
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "static", "icons")
POSTS_FILE = os.path.join(BASE_DIR, "posts.json")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
NOVELS_FILE = os.path.join(BASE_DIR, "novls.json") # ★新規：小説管理JSON
DM_FILE = os.path.join(BASE_DIR, "dm.json")
POST_REPORTS_FILE = os.path.join(BASE_DIR, "post_reports.json")
DM_CASES_FILE = os.path.join(BASE_DIR, "dm_cases.json")

# 管理者パスワード。必ず環境変数 ADMIN_PASSWORD で上書きしてください。
# 未設定のまま本番運用すると誰でも管理者になれてしまうため、起動時に警告します。
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = base64.b64encode(os.urandom(9)).decode()
    print("=" * 60)
    print("[警告] 環境変数 ADMIN_PASSWORD が未設定です。")
    print(f"今回だけ有効な仮パスワードを生成しました: {ADMIN_PASSWORD}")
    print("次回以降も使うパスワードは必ず環境変数 ADMIN_PASSWORD に設定してください。")
    print("=" * 60)

os.makedirs(ICON_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, template_folder="mysite/templates")
# secret_key はハードコードせず、環境変数 SECRET_KEY から読む。
# 未設定の場合は起動のたびにランダム生成（本番では必ず環境変数を設定して固定すること）
app.secret_key = os.environ.get("SECRET_KEY") or base64.b64encode(os.urandom(32)).decode()
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./.flask_session"
app.config["SESSION_PERMANENT"] = False

# アップロード総サイズの上限（ディスク枯渇によるDoSを防ぐ）
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

# セッションCookieの保護
app.config["SESSION_COOKIE_HTTPONLY"] = True   # JSからCookieを読めなくする（XSS時の被害軽減）
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # CSRF対策の一環
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
# ↑ Secure属性はHTTPS環境でのみCookieを送信させるためのもの。
#   ローカルのHTTP開発環境ではCookieが送信されなくなるため、
#   本番(HTTPS)では有効化・開発中は無効化されるようにしている。
#   本番運用時は環境変数 FLASK_ENV=production を必ず設定すること。

MEDIA_DIR = BASE_DIR
USER_SESSIONS = {}
access_count = 0

# ─────────────────────────────────────────
# 🔒 ブルートフォース対策（ログイン・管理者パネル共通）
# ─────────────────────────────────────────
LOGIN_ATTEMPTS = {}   # key: "username" -> [失敗時刻のリスト]
ADMIN_ATTEMPTS = {}   # key: IPアドレス -> [失敗時刻のリスト]

def is_rate_limited(store, key, max_attempts=5, window_seconds=300):
    now = time.time()
    attempts = [t for t in store.get(key, []) if now - t < window_seconds]
    store[key] = attempts
    return len(attempts) >= max_attempts

def record_failed_attempt(store, key):
    store.setdefault(key, []).append(time.time())

def clear_attempts(store, key):
    store.pop(key, None)

# --- ここから追加：Chromebookをブロックするおまじない ---
@app.before_request
def block_chromebook():
    from flask import request, abort
    # アクセスしてきた端末の情報を確認する
    user_agent = request.user_agent.string.lower()
    # もし情報の中に「cros (Chrome OS = Chromebook)」が含まれていたら
    if 'cros' in user_agent:
        abort(403, description="申し訳ありません。Chromebookからはアクセスできません。")
# --- ここまで追加 ---

# ─────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────
def encode_username_filename(username):
    return urllib.parse.quote(username, safe="") + ".png"

def decode_filename_to_username(filename):
    return urllib.parse.unquote(os.path.splitext(filename)[0])

def list_icons():
    icons = []
    for fname in os.listdir(ICON_DIR):
        if fname.lower().endswith(".png"):
            icons.append({"name": decode_filename_to_username(fname), "src": f"/static/icons/{fname}"})
    return icons

def get_drive_direct_url(url):
    import re
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        # view ではなく download に変更する（こちらの方が安定します）
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url

def save_uploaded_icon(file_storage, dest_path):
    """アップロードされたアイコンが本物の画像であることを確認してから保存する。
    拡張子は常に .png に固定して保存するが、中身がPNG/JPEG等の画像ファイルで
    なければ拒否する（HTML/SVG/スクリプトファイルを png と偽って保存させない）。"""
    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream).convert("RGBA")
        img.save(dest_path, format="PNG")
        return True
    except Exception:
        return False

def save_placeholder_icon(path):
    img = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
    img.save(path)

def compress_message(text: str) -> str:
    data = zlib.compress(text.encode("utf-8"))
    return base64.b64encode(data).decode("ascii")

def decompress_message(encoded: str) -> str:
    try:
        data = base64.b64decode(encoded)
        return zlib.decompress(data).decode("utf-8")
    except Exception:
        return "[デコードできません]"

def loadnovels():
    if not os.path.exists(NOVELS_FILE): return []
    with open(NOVELS_FILE, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return []

def normalize_message(text: str) -> str | None:
    if text is None:
        return None
    t = text.strip()
    return t if t else None

def random_filename(length=16, ext=".bin"):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length)) + ext

import requests


# --- ここから各データの読み書き（コードの既存部分を差し替え） ---
# ─────────────────────────────────────────
# ★ Supabase 接続（環境変数から読む）
# ─────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

print("=== 環境変数デバッグ ===")
print(f"SUPABASE_URL: {'設定あり' if SUPABASE_URL else '未設定'}")
print(f"SUPABASE_SERVICE_KEY: {'設定あり' if SUPABASE_SERVICE_KEY else '未設定'}")
if SUPABASE_SERVICE_KEY:
    print(f"  → 先頭5文字: {SUPABASE_SERVICE_KEY[:5]}...")

if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
else:
    supabase = None  # ← これを追加！
    print("⚠️ SUPABASE_URL または SUPABASE_SERVICE_KEY が未設定です。")
    
# ─────────────────────────────────────────
# ユーティリティ（再定義しておく）
# ─────────────────────────────────────────
def random_filename(length=16, ext=".bin"):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length)) + ext

def dm_pair_key(a, b):
    return "::".join(sorted([a, b]))

# ─────────────────────────────────────────
# ★ データ保存関数（すべてSupabase経由）
# ─────────────────────────────────────────

def loaddata():
    """users テーブル → {username: userdata} 辞書"""
    if supabase is None:
        return {}
    try:
        res = supabase.table("users").select("*").execute()
        data = {}
        for row in res.data:
            name = row.pop("name")
            data[name] = row
        return data
    except Exception as e:
        print("loaddataエラー:", e)
        return {}

def save_data(data):
    """users テーブルに upsert"""
    if supabase is None:
        return
    try:
        rows = []
        for name, info in data.items():
            rows.append({
                "name": name,
                "icon": info.get("icon"),
                "pwhash": info.get("pwhash"),
                "violation_count": info.get("violation_count", 0),
                "restricted": info.get("restricted", False),
                "pending_deletion": info.get("pending_deletion", False),
            })
        if rows:
            supabase.table("users").upsert(rows).execute()
    except Exception as e:
        print("save_dataエラー:", e)

def loadposts():
    """posts テーブル → リスト"""
    if supabase is None:
        return []
    try:
        res = supabase.table("posts").select("*").execute()
        return res.data
    except Exception as e:
        print("loadpostsエラー:", e)
        return []

def saveposts(posts):
    """posts テーブルに upsert（idベース）"""
    if supabase is None:
        return
    try:
        if posts:
            supabase.table("posts").upsert(posts).execute()
    except Exception as e:
        print("savepostsエラー:", e)

def load_dms():
    """dm_messages テーブル → {pair_key: [messages]}"""
    if supabase is None:
        return {}
    try:
        res = supabase.table("dm_messages").select("*").execute()
        dms = {}
        for row in res.data:
            key = row.get("pair_key")
            if key not in dms:
                dms[key] = []
            dms[key].append({
                "id": row.get("id"),
                "sender": row.get("sender"),
                "text": row.get("text"),
                "time": row.get("time"),
            })
        return dms
    except Exception as e:
        print("load_dmsエラー:", e)
        return {}

def save_dms(dms):
    """dms を dm_messages テーブルに全入れ替え"""
    if supabase is None:
        return
    try:
        try:
            supabase.table("dm_messages").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        except:
            pass
        rows = []
        for pair_key, msgs in dms.items():
            for msg in msgs:
                rows.append({
                    "id": msg.get("id", str(uuid.uuid4())),
                    "pair_key": pair_key,
                    "sender": msg.get("sender"),
                    "text": msg.get("text"),
                    "time": msg.get("time"),
                })
        if rows:
            supabase.table("dm_messages").insert(rows).execute()
    except Exception as e:
        print("save_dmsエラー:", e)

def load_json_list(path):
    """post_reports / dm_cases を読み込む"""
    if supabase is None:
        return []
    try:
        if "post_reports" in path:
            res = supabase.table("post_reports").select("*").execute()
            return res.data
        elif "dm_cases" in path:
            res = supabase.table("dm_cases").select("*").execute()
            return res.data
        return []
    except Exception as e:
        print("load_json_listエラー:", e)
        return []

def save_json_list(path, items):
    """post_reports / dm_cases を全入れ替え保存"""
    if supabase is None:
        return
    try:
        if "post_reports" in path:
            try:
                supabase.table("post_reports").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            except:
                pass
            if items:
                supabase.table("post_reports").insert(items).execute()
        elif "dm_cases" in path:
            try:
                supabase.table("dm_cases").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            except:
                pass
            if items:
                supabase.table("dm_cases").insert(items).execute()
    except Exception as e:
        print("save_json_listエラー:", e)

def save_uploaded_icon(file_storage, filename):
    """Supabase Storage の 'icons' バケットに画像をアップロード（第2引数はファイル名だけ）"""
    if supabase is None:
        return False
    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream).convert("RGBA")
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        bucket_name = "icons"
        supabase.storage.from_(bucket_name).upload(
            filename, buf.getvalue(), {"content-type": "image/png"}
        )
        return True
    except Exception as e:
        print("アイコンアップロードエラー:", e)
        return False

def get_icon_url(filename):
    """Supabase Storage の公開URLを取得"""
    # "default.png" はローカルの static/icons に置かれた固定アイコンであり、
    # Supabaseバケットにはアップロードされていないため、ここで必ずローカルパスを返す。
    if supabase is None or not filename or filename == "default.png":
        return "/static/icons/default.png"
    try:
        bucket_name = "icons"
        return supabase.storage.from_(bucket_name).get_public_url(filename)
    except:
        return "/static/icons/default.png"

def list_icons():
    """icons バケット内のファイル一覧を取得"""
    if supabase is None:
        return []
    try:
        bucket_name = "icons"
        res = supabase.storage.from_(bucket_name).list()
        icons = []
        for file_obj in res:
            name = os.path.splitext(file_obj["name"])[0]
            try:
                display_name = urllib.parse.unquote(name)
            except:
                display_name = name
            icons.append({
                "name": display_name,
                "src": supabase.storage.from_(bucket_name).get_public_url(file_obj["name"])
            })
        return icons
    except Exception as e:
        print("list_iconsエラー:", e)
        return []

# 全てのリクエストの前に実行されるチェック
@app.before_request
def block_chromebook():
    user_agent = request.user_agent.string.lower()
    # User-Agentに 'cros' (Chrome OS) が含まれている場合は403エラーを返す
    if 'cros' in user_agent:
        # または render_template で専用のアクセス拒否ページを返しても良いです
        abort(403, description="Chromebookからのアクセスは許可されていません。")
# ─────────────────────────────────────────
# /watch 動画再生
# ─────────────────────────────────────────
@app.route("/watch")
def watch():
    global access_count
    access_count += 1
    filename = request.args.get("filename")

    if not filename:
        return "filename が指定されていません", 400

    # 最新機能: iframeタグが直接渡された場合の保険
    src_match = re.search(r'src=["\']([^"\']+)["\']', filename)
    if src_match:
        filename = src_match.group(1)

    # 最新機能: タイトルの決定（videos.txtからの抽出とエラー回避）
    display_title = None
    if filename == "youtube.mp4":
        display_title = "youtubeを見る方法 徹底解説！(wwwww)"
    else:
        txt_path = os.path.join(BASE_DIR, 'videos.txt')
        if os.path.exists(txt_path) and ("http" in filename or "mega.nz" in filename):
            try:
                with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = [line.strip() for line in f.readlines() if line.strip()]
                for i, line in enumerate(lines):
                    if filename in line:
                        if i > 0 and not lines[i-1].startswith('<iframe') and 'http' not in lines[i-1]:
                            display_title = lines[i-1]
                        break
            except Exception as e:
                print("watch用タイトル取得エラー:", e)

        if not display_title:
            clean_name = filename.replace("__upload__", "")
            if clean_name.startswith("http"):
                display_title = "外部動画"
            else:
                display_title = os.path.splitext(clean_name)[0]

    # URLスキームの簡易ホワイトリスト（javascript: 等の危険なスキームを弾く）
    def is_safe_url(u: str) -> bool:
        return u.startswith("http://") or u.startswith("https://")

    # プレイヤーの種類だけをサーバー側で決定し、実際のURL/ファイル名は
    # 必ずJinja2のオートエスケープを通して埋め込む（テンプレート文字列自体には
    # 一切ユーザー入力を混ぜない = SSTI対策）
    if "mega.nz/embed" in filename and is_safe_url(filename):
        player_kind = "iframe"
        media_src = filename
    elif filename.startswith("http"):
        if not is_safe_url(filename):
            return "不正なURLです", 400
        player_kind = "video"
        media_src = filename
    else:
        player_kind = "video"
        media_src = f"/static/uploads/{filename[len('__upload__'):]}" if filename.startswith("__upload__") else f"/media/{filename}"

    # HTMLテンプレートは固定の文字列（ユーザー入力を一切含まない）とし、
    # 変数は render_template_string の第二引数以降として渡すことで
    # Jinja2の自動エスケープが必ず効くようにする
    html = '''
    <h2>🎬 再生中: {{ display_title }}</h2>
    {% if player_kind == "iframe" %}
    <iframe width="640" height="360" frameborder="0" src="{{ media_src }}" allowfullscreen tabindex="-1"></iframe>
    {% else %}
    <video controls autoplay width="640" tabindex="-1"><source src="{{ media_src }}" type="video/mp4"></video>
    {% endif %}
    <p><a href="/full">← 戻る</a></p>
    <p>アクセス数: {{ access_count }}</p>
    '''

    return render_template_string(
        html,
        display_title=display_title,
        player_kind=player_kind,
        media_src=media_src,
        access_count=access_count,
    )

# ─────────────────────────────────────────
# /full メディア一覧 (外部動画は最新順・ローカルは名前順)
# ─────────────────────────────────────────
@app.route("/")
@app.route("/full")
def full():
    global access_count
    access_count += 1

    files = os.listdir(MEDIA_DIR) if os.path.exists(MEDIA_DIR) else []
    audio_files  = [f for f in files if f.endswith((".mp3", ".wav"))]
    image_files  = [f for f in files if f.endswith((".jpg", ".jpeg", ".png", ".gif"))]

    local_videos = []
    external_videos = []  # ★videos.txt用を独立させる
    classroom_videos = []

    # 1. ローカルの動画ファイル
    for f in files:
        if f.endswith((".mp4", ".webm")):
            local_videos.append({"value": f, "text": f})

    # ★ローカル動画だけを名前順にソートする
    local_videos.sort(key=lambda x: str(x["text"]).lower())

    # 2. videos.txtの外部動画をリストに追加 (バグ修正＆文字コード対策)
    txt_path = os.path.join(BASE_DIR, 'videos.txt')
    if os.path.exists(txt_path):
        try:
            with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]

            i = 0
            while i < len(lines):
                line = lines[i]

                # パターンA: 行がURLやiframeの場合
                if line.startswith('<iframe') or 'http' in line:
                    url = line
                    import re
                    match = re.search(r'src=["\']([^"\']+)["\']', line)
                    if match:
                        url = match.group(1)

                    title = "外部動画"
                    if i > 0 and not lines[i-1].startswith('<iframe') and 'http' not in lines[i-1]:
                        title = lines[i-1]

                    external_videos.append({"value": url, "text": title})  # ★externalへ
                    i += 1

                # パターンB: 行がタイトルの場合
                else:
                    title = line
                    i += 1
                    if i < len(lines):
                        next_line = lines[i]
                        if next_line.startswith('<iframe') or 'http' in next_line:
                            url = next_line
                            import re
                            match = re.search(r'src=["\']([^"\']+)["\']', next_line)
                            if match:
                                url = match.group(1)
                            external_videos.append({"value": url, "text": title})  # ★externalへ
                            i += 1
                        else:
                            pass
        except Exception as e:
            print("videos.txt 読み込みエラー:", e)

    # ★「3. ソート」を削除（external_videosはソートしないため、テキストの並び順のまま保持されます）

    target_media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
    if os.path.exists(target_media_dir):
        html_files = [f for f in os.listdir(target_media_dir) if f.endswith((".html", ".htm"))]
        text_files = [f for f in os.listdir(target_media_dir) if f.endswith((".txt", ".md"))]
    else:
        html_files = []
        text_files = []

    if os.path.exists(UPLOAD_DIR):
        for f in os.listdir(UPLOAD_DIR):
            ext = os.path.splitext(f)[1].lower()
            tagged = "__upload__" + f
            if ext in (".mp3", ".wav"):
                audio_files.append(tagged)
            elif ext in (".mp4", ".webm"):
                classroom_videos.append({"value": tagged, "text": "[buildvi] " + f})
            elif ext in (".jpg", ".jpeg", ".png", ".gif"):
                image_files.append(tagged)

    # ★ここで合流（ローカル名前順 + 外部テキスト順 + クラスルーム順）
    video_files = external_videos + local_videos + classroom_videos

    html = r'''
    <h2>🎧 音声ファイル一覧</h2>
    <form method="get" action="/play">
      <select name="filename">
        {% for f in audio %}
          <option value="{{ f }}">{{ f.replace("__upload__","[buildvi] ") }}</option>
        {% endfor %}
      </select>
      <input type="submit" value="再生">
    </form>

    <h2>🎬 動画ファイル一覧</h2>
    <form method="get" action="/watch">
      <select name="filename">
        {% for v in video %}
          <option value="{{ v.value }}">{{ v.text }}</option>
        {% endfor %}
      </select>
      <input type="submit" value="再生">
    </form>

    <h2>🖼️ 画像ファイル一覧</h2>
    <form method="get" action="/view">
      <select name="filename">
        {% for f in images %}
          <option value="{{ f }}">{{ f.replace("__upload__","[buildvi] ") }}</option>
        {% endfor %}
      </select>
      <input type="submit" value="表示">

    </form>

    <h2>🌐 HTMLファイル一覧</h2>
    <form method="get" action="/view_html">
      <select name="filename">
        {% for f in html_list %}
          <option value="{{ f }}">{{ f }}</option>
        {% endfor %}
      </select>
      <input type="submit" value="表示">
    </form>

    <h2>💬 次世代 OOIAI</h2>
    <form method="get" action="/chat">
      <button style="padding: 0.5em 1em; font-size: 1em;">🧠 チャットページへ</button>
    </form>

    <h2>🌍 みんなのチャット (NEW!!)</h2>
    <a href="/buildvi">
      <button style="padding: 0.5em 1em; font-size: 1em;">🖼️ 投稿一覧ページへ</button>
    </a>

    <h2>📄 文章を読む場所</h2>
    <a href="/novel">
        <button style="padding: 0.5em 1em; font-size: 1em;">📄 小説一覧ページへ</button>
    </a>

    <h2>🔍 動画検索（専用）</h2>
    <input type="text" id="videoSearch" placeholder="動画名で検索..." onkeyup="searchVideos()"><br><br>
    <div id="searchResults"></div>

    <script>
      const videoData = [
        {% for v in video %}
          { value: {{ v.value | tojson }}, text: {{ v.text | tojson }} },
        {% endfor %}
      ];

      function searchVideos() {
        const input = document.getElementById("videoSearch").value.toLowerCase();
        const resultsDiv = document.getElementById("searchResults");
        resultsDiv.innerHTML = "";

        if (input.trim() === "") return;

        videoData.forEach(function(v) {
          if (v.text.toLowerCase().includes(input)) {
            const link = document.createElement("a");
            link.href = "/watch?filename=" + encodeURIComponent(v.value);
            link.textContent = "▶ 再生: " + v.text;
            link.style.display = "block";
            link.style.marginBottom = "10px";
            resultsDiv.appendChild(link);
          }
        });
      }

      document.addEventListener("keydown", function(e) {
        if (e.target.tagName === "INPUT") return;

        if (e.key === " ") { location.href = "/"; }
        if (e.key === "Enter") { document.body.innerHTML = ""; document.body.style.background = "#fff"; }
      });
    </script>

    <div style="height:5000px; background:linear-gradient(to bottom,#eef,#ddf,#ccf); margin-top:40px;">
      <p style="text-align:center; padding-top:200px; font-size:24px; color:#00f;">顔色を徐々に悪くする ↓</p>
    </div>
    <p>アクセス数: {{ count }}</p>
    '''
    return render_template_string(html, audio=audio_files, video=video_files,
                                  images=image_files, html_list=html_files,
                                  text_list=text_files, count=access_count)
# ─────────────────────────────────────────
# 各種ファイル配信・表示
# ─────────────────────────────────────────

@app.route("/play")
def play():
    global access_count
    access_count += 1
    filename = request.args.get("filename")
    if not filename: return "filename が指定されていません", 400
    src = f"/static/uploads/{filename[len('__upload__'):]}" if filename.startswith("__upload__") else f"/media/{filename}"
    return render_template_string(
        '<audio controls autoplay><source src="{{ src }}" type="audio/mpeg"></audio>'
        '<p><a href="/full">← 戻る</a></p><p>アクセス数: {{ access_count }}</p>',
        src=src, access_count=access_count
    )

@app.route("/view_html")
def view_html():
    filename = request.args.get("filename", "")
    target_media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
    target_path = os.path.join(target_media_dir, filename)
    if os.path.exists(target_path) and os.path.isfile(target_path):
        return send_from_directory(target_media_dir, filename)
    return abort(404)

@app.route("/view")
def view_image():
    global access_count
    access_count += 1
    filename = request.args.get("filename")
    if not filename: return "filename が指定されていません", 400
    src = f"/static/uploads/{filename[len('__upload__'):]}" if filename.startswith("__upload__") else f"/media/{filename}"
    return render_template_string(
        '<img src="{{ src }}" alt="{{ filename }}" style="max-width:480px; height:auto;">'
        '<p><a href="/full">← 戻る</a></p><p>アクセス数: {{ access_count }}</p>',
        src=src, filename=filename, access_count=access_count
    )

SAFE_MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mp3", ".wav", ".jpg", ".jpeg", ".png", ".gif"
}

@app.route("/media/<filename>")
def media(filename):
    global access_count
    # MEDIA_DIR は以前 BASE_DIR（アプリのルート）そのものだったため、
    # aka.py / data.json / posts.json / novls.json / chat.py などの
    # ソースコードや個人情報まで誰でもダウンロードできてしまっていた。
    # ここでは拡張子をメディア系だけに限定し、それ以外は一切配信しない。
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SAFE_MEDIA_EXTENSIONS:
        return abort(403)
    access_count += 1
    return send_from_directory(MEDIA_DIR, filename)
from flask import request, redirect, render_template_string
import json
import os

# 一覧ページと検索機能
@app.route("/novel")
def novel_list():
    target_media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
    selected_series = request.args.get("series")

    txt_files = [f for f in os.listdir(target_media_dir) if f.lower().endswith('.txt')] if os.path.exists(target_media_dir) else []

    novels = []
    for f in txt_files:
        base_name = os.path.splitext(f)[0]
        # ルール: 彁の前がタイトル、後ろがシリーズ名
        if "彁" in base_name:
            parts = base_name.split("彁", 1)
            title = parts[0]
            series = parts[1]
        else:
            title = base_name
            series = "その他"

        mtime = os.path.getmtime(os.path.join(target_media_dir, f))
        novels.append({
            "series": series,
            "title": title,
            "file_name": f,
            "created_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
            "icon": f"/static/icons/{series}.png"
        })

    novels.sort(key=lambda x: x["created_at"], reverse=True)

    html = r'''
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>小説一覧</title>
        <style>
            body { font-family: sans-serif; background: #fff; color: #333; max-width: 800px; margin: auto; padding: 40px 20px; }
            h1 { font-size: 1.8em; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 30px; }

            /* シリーズヘッダー（アイコンとシリーズ名） */
            .series-header { display: flex; align-items: center; gap: 20px; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 2px solid #eee; }
            .series-icon { width: 80px; height: 80px; border-radius: 12px; object-fit: cover; }

            /* 小説リスト */
            .novel-list { list-style: none; padding: 0; }
            .novel-item { padding: 15px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
            .novel-item a { text-decoration: none; color: #333; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1><a href="/novel" style="text-decoration:none; color:inherit;">小説一覧</a></h1>

        {% if selected_series %}
        <div class="series-header">
            <img src="/static/icons/{{ selected_series }}.png" onerror="this.src='/static/icons/default.png'" class="series-icon">
            <h2>{{ selected_series }}</h2>
        </div>
        {% endif %}

        <ul class="novel-list">
            {% for n in novels if not selected_series or n.series == selected_series %}
            <li class="novel-item">
                <a href="/novel/read?filename={{ n.file_name }}">{{ n.title }}</a>
                <span style="color: #aaa; font-size: 0.9em;">
                    {% if not selected_series %}
                        <a href="/novel?series={{ n.series }}" style="color:#888; font-size:0.8em; margin-right: 10px;">{{ n.series }}</a>
                    {% endif %}
                    {{ n.created_at }}
                </span>
            </li>
            {% endfor %}
        </ul>
    </body>
    </html>
    '''
    return render_template_string(html, novels=novels, selected_series=selected_series)
# 小説の読み込みページ
@app.route("/novel/read")
def novel_read():
    filename = request.args.get("filename", "")
    target_media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")

    # パストラバーサル対策:
    # secure_filename() は日本語ファイル名（小説タイトル）を壊してしまうため使わず、
    # 区切り文字や ".." を含む入力を拒否したうえで、解決後のパスが
    # 必ず target_media_dir の内側にあることを確認する（多重防御）
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return abort(400)
    abs_media_dir = os.path.abspath(target_media_dir)
    file_path = os.path.normpath(os.path.join(abs_media_dir, filename))
    if not (file_path == abs_media_dir or file_path.startswith(abs_media_dir + os.sep)):
        return abort(403)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return "ファイルが見つかりません", 404

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    html = r'''
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>{{ filename }}</title>
        <style>
            body { background-color: #fcfcf9; color: #333; line-height: 1.8; margin: 0; padding: 40px 20px; font-family: sans-serif; }
            .container { max-width: 680px; margin: 0 auto; }
            .back-link { display: inline-block; color: #555; text-decoration: none; margin-bottom: 24px; }
            h1 { font-size: 1.4em; border-bottom: 1px solid #e6e6e2; padding-bottom: 8px; }
            .content { white-space: pre-wrap; word-wrap: break-word; }
        </style>
    </head>
    <body>
        <div class="container">
            <a href="/novel" class="back-link">← 一覧に戻る</a>
            <h1>{{ filename }}</h1>
            <div class="content">{{ content }}</div>
        </div>
    </body>
    </html>
    '''
    return render_template_string(html, filename=filename, content=content)

# ─────────────────────────────────────────
# /ビルドビ
# ─────────────────────────────────────────
@app.route("/buildvi", methods=["GET", "POST"])
def classroom_page():
    global access_count
    import flask
    if flask.request.method == "POST":
        action_type = flask.request.form.get("action_type")

        # アイコン変更
        if action_type == "change_icon":
            sid = flask.request.form.get("sid")
            user = USER_SESSIONS.get(sid)
            if not user:
                return flask.jsonify({"status": "error", "error": "Unauthorized"}), 403
            icon_file = flask.request.files.get("icon")
            if not icon_file:
                return flask.jsonify({"status": "error", "error": "no file"}), 400
            fname = random_filename(16, ".png")
            if not save_uploaded_icon(icon_file, fname):
                return flask.jsonify({"status": "error", "error": "画像ファイルとして認識できません"}), 400
            user["icon_filename"] = fname
            data = loaddata()
            name = user["student_name"]
            if name in data:
                data[name]["icon"] = fname
                save_data(data)
            return flask.jsonify({"status": "ok", "icon": get_icon_url(fname)})

        # 投稿削除
        elif action_type == "delete":
            sid = flask.request.form.get("sid")
            post_id = flask.request.form.get("post_id")
            user = USER_SESSIONS.get(sid)
            if not user:
                return flask.jsonify({"status": "error", "error": "Unauthorized"}), 403
            ps = loadposts()
            ps = [p for p in ps if not (str(p.get("id")) == str(post_id) and p.get("user") == user["student_name"])]
            saveposts(ps)
            return flask.jsonify({"status": "ok"})

        # ログイン・登録
        elif action_type in ["login", "register"]:
            mode = flask.request.form.get("mode")
            name = (flask.request.form.get("username") or "").strip()
            password = flask.request.form.get("password") or ""
            data = loaddata()

            if not name:
                return flask.jsonify({"status": "error", "error": "ユーザー名を入力してください"}), 400

            if mode == "register":
                icon_file = flask.request.files.get("icon")
                if not icon_file or name in data:
                    return flask.jsonify({"status": "error", "error": "register failed"}), 400
                if len(password) < 4:
                    return flask.jsonify({"status": "error", "error": "パスワードは4文字以上にしてください"}), 400
                fname = random_filename(16, ".png")
                if not save_uploaded_icon(icon_file, fname):
                    return flask.jsonify({"status": "error", "error": "画像ファイルとして認識できません"}), 400
                data[name] = {
                    "icon": fname,
                    "pwhash": generate_password_hash(password),
                    "violation_count": 0,
                    "restricted": False,
                    "pending_deletion": False,
                    "birthdate": flask.request.form.get("birthdate"),  # ← 追加
                }
                save_data(data)
            else:
                # ブルートフォース対策
                if is_rate_limited(LOGIN_ATTEMPTS, name):
                    return flask.jsonify({
                        "status": "error",
                        "error": "ログイン試行回数が多すぎます。しばらく待ってから再度お試しください。"
                    }), 429

                user_record = data.get(name)
                if not user_record or "pwhash" not in user_record:
                    record_failed_attempt(LOGIN_ATTEMPTS, name)
                    return flask.jsonify({"status": "error", "error": "ユーザー名またはパスワードが違います"}), 401
                if not check_password_hash(user_record["pwhash"], password):
                    record_failed_attempt(LOGIN_ATTEMPTS, name)
                    return flask.jsonify({"status": "error", "error": "ユーザー名またはパスワードが違います"}), 401
                clear_attempts(LOGIN_ATTEMPTS, name)

                if user_record.get("pending_deletion"):
                    del data[name]
                    save_data(data)
                    ps = loadposts()
                    ps = [p for p in ps if p.get("user") != name]
                    saveposts(ps)
                    return flask.jsonify({
                        "status": "error",
                        "error": "通報内容が認定されたため、このアカウントは削除されました。",
                        "account_deleted": True
                    }), 403

                if user_record.get("restricted"):
                    return flask.jsonify({
                        "status": "error",
                        "error": "違反が繰り返されたため、このアカウントは投稿が制限されています。管理者にお問い合わせください。"
                    }), 403

                fname = user_record.get("icon")
                if not fname:
                    ps = loadposts()
                    fname = next((p["icon"] for p in reversed(ps) if p.get("user") == name), "default.png")

            new_sid = secrets.token_hex(24)
            USER_SESSIONS[new_sid] = {"student_name": name, "icon_filename": fname}
            return flask.jsonify({"status": "ok", "sid": new_sid, "icon": get_icon_url(fname)})

        # アカウント削除（← 新規追加）
        elif action_type == "delete_account":
            sid = flask.request.form.get("sid")
            user = USER_SESSIONS.get(sid)
            if not user:
                return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
            name = user["student_name"]
            data = loaddata()
            if name not in data:
                return flask.jsonify({"status": "error", "error": "ユーザーが見つかりません"}), 404
            # ユーザー削除
            del data[name]
            save_data(data)
            # 投稿も削除
            ps = loadposts()
            ps = [p for p in ps if p.get("user") != name]
            saveposts(ps)
            # セッション削除
            USER_SESSIONS.pop(sid, None)
            return flask.jsonify({"status": "ok"})

        # 投稿処理
        elif action_type == "post":
            try:
                sid = flask.request.form.get("sid")
                user = USER_SESSIONS.get(sid)
                if not user:
                    return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401

                ps = loadposts()
                post_file = flask.request.files.get("post_file")
                msg = flask.request.form.get("message", "").strip()
                reply_to = flask.request.form.get("reply_to", "").strip() or None
                thread = flask.request.form.get("thread", "").strip()
                content_restriction = flask.request.form.get("content_restriction", "none").strip()
                is_highlight = flask.request.form.get("is_highlight", "false")

                if not msg and not (post_file and post_file.filename):
                    return flask.jsonify({"status": "error", "error": "メッセージかファイルを入力してください"}), 400

                new_post = {
                    "id": str(uuid.uuid4()),
                    "user": user["student_name"],
                    "message": msg,
                    "icon": user["icon_filename"],
                    "time": datetime.now().strftime("%m/%d %H:%M"),
                    "file": None,
                    "likes": 0,
                    "views": 0,
                    "reply_to": reply_to,
                    "thread": thread,
                    "content_restriction": content_restriction,
                    "is_highlight": is_highlight
                }

                if post_file and post_file.filename:
                    ext = os.path.splitext(post_file.filename)[1].lower()
                    raw_base = os.path.splitext(post_file.filename)[0]
                    safe_base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', raw_base).strip() or "file"
                    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                    sv_name = f"{safe_base}_{suffix}{ext}"
                    post_file.save(os.path.join(UPLOAD_DIR, sv_name))

                    if ext in (".mp4", ".webm"):
                        ftype = "video"
                    elif ext in (".mp3", ".wav"):
                        ftype = "audio"
                    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                        ftype = "image"
                    else:
                        ftype = "other"

                    new_post["file"] = {"save_name": sv_name, "type": ftype, "ext": ext, "original_name": post_file.filename}

                ps.append(new_post)
                saveposts(ps)
                return flask.jsonify({"status": "ok"})

            except Exception as e:
                print("投稿エラー:", str(e))
                return flask.jsonify({"status": "error", "error": str(e)}), 500

        else:
            return flask.jsonify({"status": "error", "error": "不明なaction_type"}), 400

    # GET処理
    get_type = flask.request.args.get("get_type")
    user_dict = loaddata()
    all_posts = loadposts()

    if get_type == "json":
        req_sid = flask.request.args.get("sid")
        req_user = USER_SESSIONS.get(req_sid)
        req_username = req_user["student_name"] if req_user else None

        formatted_list = []
        for p in all_posts:
            item = p.copy()
            item["likes"] = int(item.get("likes", 0))
            if "type" in item:
                del item["type"]
            liked_by = item.pop("liked_by", None) or []
            item["liked"] = bool(req_username and req_username in liked_by)
            item["is_approved"] = (item.get("report_status") == "approved")
            item["is_reported"] = (item.get("report_status") == "pending")
            item["icon"] = get_icon_url(item.get("icon"))
            formatted_list.append(item)

        for name, info in user_dict.items():
            formatted_list.append({"type": "user", "user": name, "icon": get_icon_url(info.get("icon", "default.png"))})

        valid_hot_posts = [p for p in all_posts if int(p.get("likes", 0)) > 0]
        hot_posts = sorted(valid_hot_posts, key=lambda x: int(x.get("likes", 0)), reverse=True)[:5]

        # ★ 成人判定をレスポンスに追加（← ここを修正）
        is_adult = False
        birthdate_required = False
        if req_user:
            name = req_user["student_name"]
            user_data = loaddata().get(name, {})
            birthdate = user_data.get("birthdate")
            if birthdate:
                try:
                    birth_year = int(birthdate.split("-")[0])
                    is_adult = (datetime.now().year - birth_year >= 18)
                except:
                    pass
            else:
                birthdate_required = True

        return flask.jsonify({
            "all": formatted_list,
            "hot": hot_posts,
            "access_count": access_count,
            "is_adult": is_adult,
            "birthdate_required": birthdate_required
        })

    sid = flask.request.args.get("sid")
    user_session = USER_SESSIONS.get(sid) if sid else None
    reg_list = list(user_dict.keys())
    access_count += 1
    return flask.render_template(
        "main.html",
        sid=sid,
        username=user_session["student_name"] if user_session else "Guest",
        user_icon_filename=get_icon_url(user_session["icon_filename"] if user_session else "default.png"),
        registered_users=reg_list,
        access_count=access_count,
    )
# ─────────────────────────────────────────
# 🛠️ 【補完】いいね用非同期ルート
# ─────────────────────────────────────────
@app.route("/like", methods=["POST"])
def like_post():
    import flask
    data = flask.request.json or {}
    post_id = data.get("id")
    sid = data.get("sid")

    # ログインしていないユーザーはいいねできない（以前は誰でも無認証で
    # /like に直接POSTしていいね数を無制限に操作できてしまっていた）
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    username = user["student_name"]

    ps = loadposts()
    result_liked = None

    for p in ps:
        if str(p.get("id")) == str(post_id):
            liked_by = p.get("liked_by") or []
            # クライアントが送ってくる action は信用せず、
            # サーバー側で「すでにこのユーザーがいいね済みか」だけを見て
            # トグルする（1ユーザー1いいねをサーバー側で強制）
            if username in liked_by:
                liked_by.remove(username)
                result_liked = False
            else:
                liked_by.append(username)
                result_liked = True
            p["liked_by"] = liked_by
            p["likes"] = len(liked_by)
            break
    else:
        return flask.jsonify({"status": "error", "error": "投稿が見つかりません"}), 404

    saveposts(ps)
    return flask.jsonify({"status": "ok", "liked": result_liked})


# ─────────────────────────────────────────
# 💬 DM（ダイレクトメッセージ）: サーバー側保存
# ─────────────────────────────────────────
def dm_history_visible_to(username, other, limit=200):
    """指定した2人の会話履歴を返す。management(管理者)からは絶対に呼ばれない関数であり、
    本人同士のDM取得専用。管理者は別ルート(report_dm経由で提出された特定メッセージのみ)しか見られない。"""
    dms = load_dms()
    key = dm_pair_key(username, other)
    return dms.get(key, [])[-limit:]

@app.route("/dm/send", methods=["POST"])
def dm_send():
    import flask
    sid = flask.request.form.get("sid")
    target = (flask.request.form.get("target") or "").strip()
    text = normalize_message(flask.request.form.get("text"))
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    if not target or target == user["student_name"]:
        return flask.jsonify({"status": "error", "error": "宛先が不正です"}), 400
    if not text:
        return flask.jsonify({"status": "error", "error": "メッセージを入力してください"}), 400

    data = loaddata()
    if target not in data:
        return flask.jsonify({"status": "error", "error": "宛先ユーザーが存在しません"}), 404

    dms = load_dms()
    key = dm_pair_key(user["student_name"], target)
    dms.setdefault(key, [])
    msg = {
        "id": str(uuid.uuid4()),
        "sender": user["student_name"],
        "text": text,
        "time": datetime.now().strftime("%m/%d %H:%M"),
    }
    dms[key].append(msg)
    save_dms(dms)
    return flask.jsonify({"status": "ok", "message": msg})

@app.route("/dm/list")
def dm_list():
    import flask
    sid = flask.request.args.get("sid")
    target = (flask.request.args.get("target") or "").strip()
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    if not target:
        return flask.jsonify({"status": "error", "error": "宛先が不正です"}), 400
    logs = dm_history_visible_to(user["student_name"], target)
    return flask.jsonify({"status": "ok", "messages": logs})


# ─────────────────────────────────────────
# 🚨 投稿の通報
# ─────────────────────────────────────────
@app.route("/report_post", methods=["POST"])
def report_post():
    import flask
    sid = flask.request.form.get("sid")
    post_id = flask.request.form.get("post_id")
    reason = normalize_message(flask.request.form.get("reason"))
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    if not reason:
        return flask.jsonify({"status": "error", "error": "通報理由を入力してください"}), 400

    ps = loadposts()
    target_post = next((p for p in ps if str(p.get("id")) == str(post_id)), None)
    if not target_post:
        return flask.jsonify({"status": "error", "error": "投稿が見つかりません"}), 404

    # 一度「問題なし」と管理者が認定した投稿は、再度通報されても隠さない
    if target_post.get("report_status") == "approved":
        return flask.jsonify({"status": "ok", "already_approved": True})

    target_post["report_status"] = "pending"
    saveposts(ps)

    reports = load_json_list(POST_REPORTS_FILE)
    reports.append({
        "id": str(uuid.uuid4()),
        "post_id": post_id,
        "reporter": user["student_name"],
        "reported_user": target_post.get("user"),
        "message_snapshot": target_post.get("message", ""),
        "reason": reason,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending",  # pending / approved / removed
    })
    save_json_list(POST_REPORTS_FILE, reports)
    return flask.jsonify({"status": "ok"})


# ─────────────────────────────────────────
# 🚨 DMメッセージの通報（DM全体ではなく、通報対象の1メッセージのみを提出する）
# ─────────────────────────────────────────
@app.route("/report_dm", methods=["POST"])
def report_dm():
    import flask
    sid = flask.request.form.get("sid")
    target = (flask.request.form.get("target") or "").strip()   # 会話相手（＝通報される側）
    message_id = flask.request.form.get("message_id")
    reason = normalize_message(flask.request.form.get("reason"))
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    if not target or not reason:
        return flask.jsonify({"status": "error", "error": "入力内容が不正です"}), 400

    dms = load_dms()
    key = dm_pair_key(user["student_name"], target)
    logs = dms.get(key, [])
    msg = next((m for m in logs if m.get("id") == message_id), None)
    if not msg:
        return flask.jsonify({"status": "error", "error": "対象のメッセージが見つかりません"}), 404

    # 重要: DM全体ではなく、通報対象として選ばれた「そのメッセージ1件」だけを
    # 証拠として提出する。管理者はこの提出された内容以外のDMを閲覧できない。
    cases = load_json_list(DM_CASES_FILE)
    case = {
        "id": str(uuid.uuid4()),
        "reporter": user["student_name"],
        "accused": msg.get("sender"),
        "conversation_with": target,
        "reported_message": {
            "text": msg.get("text"),
            "sender": msg.get("sender"),
            "time": msg.get("time"),
        },
        "reason": reason,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "pending_review",  # pending_review / protest_submitted / resolved_ok / resolved_removed
        "protest_text": None,
    }
    cases.append(case)
    save_json_list(DM_CASES_FILE, cases)
    return flask.jsonify({"status": "ok"})

@app.route("/dm_case/mine")
def dm_case_mine():
    """自分が通報された案件のうち、まだ抗議(講義)していないものを返す"""
    import flask
    sid = flask.request.args.get("sid")
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    cases = load_json_list(DM_CASES_FILE)
    mine = [
        {"id": c["id"], "reason": c["reason"], "reported_message": c["reported_message"], "time": c["time"]}
        for c in cases
        if c.get("accused") == user["student_name"] and c.get("status") == "pending_review"
    ]
    return flask.jsonify({"status": "ok", "cases": mine})

@app.route("/dm_case/protest", methods=["POST"])
def dm_case_protest():
    import flask
    sid = flask.request.form.get("sid")
    case_id = flask.request.form.get("case_id")
    protest_text = normalize_message(flask.request.form.get("protest_text"))
    user = USER_SESSIONS.get(sid)
    if not user:
        return flask.jsonify({"status": "error", "error": "ログインしてください"}), 401
    if not protest_text:
        return flask.jsonify({"status": "error", "error": "抗議内容を入力してください"}), 400

    cases = load_json_list(DM_CASES_FILE)
    case = next((c for c in cases if c.get("id") == case_id), None)
    if not case or case.get("accused") != user["student_name"]:
        return flask.jsonify({"status": "error", "error": "対象の案件が見つかりません"}), 404

    case["protest_text"] = protest_text
    case["status"] = "protest_submitted"
    save_json_list(DM_CASES_FILE, cases)
    return flask.jsonify({"status": "ok"})


# ─────────────────────────────────────────
# 🛡️ 管理者パネル
# 認証はパスワード必須。ここから閲覧できるDM関連情報は、
# 通報時に提出された「該当メッセージ1件」と、本人が提出した「抗議文」だけであり、
# 会話履歴全体(dm.json)を管理者が自由に閲覧できる経路は一切用意していない。
# ─────────────────────────────────────────
ADMIN_HTML = '''
<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>管理者パネル</title>
<style>
body{font-family:sans-serif;background:#f4f6f8;margin:0;padding:20px;color:#333;}
h1{font-size:1.3em;} h2{font-size:1.05em;border-bottom:2px solid #00bcd4;padding-bottom:6px;margin-top:30px;}
.card{background:#fff;border-radius:8px;padding:14px 18px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);}
.meta{font-size:0.8em;color:#888;margin-bottom:6px;}
.msg{background:#f9f9f9;border-left:4px solid #ff9800;padding:8px 12px;margin:8px 0;font-size:0.9em;white-space:pre-wrap;}
.protest{background:#fff8e1;border-left:4px solid #ffc107;padding:8px 12px;margin:8px 0;font-size:0.9em;white-space:pre-wrap;}
button{border:none;border-radius:6px;padding:7px 14px;font-weight:bold;cursor:pointer;margin-right:6px;}
.btn-ok{background:#4caf50;color:#fff;} .btn-danger{background:#e53935;color:#fff;} .btn-neutral{background:#607d8b;color:#fff;}
.empty{color:#999;font-size:0.9em;}
.login-box{max-width:320px;margin:80px auto;background:#fff;padding:24px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,0.1);}
input[type=password]{width:100%;padding:8px;box-sizing:border-box;margin-bottom:10px;border:1px solid #ccc;border-radius:6px;}
.err{color:#e53935;font-size:0.85em;}
table{border-collapse:collapse;width:100%;font-size:0.85em;} td,th{padding:6px 8px;border-bottom:1px solid #eee;text-align:left;}
</style></head><body>
{% if not authed %}
  <div class="login-box">
    <h1>🛡️ 管理者ログイン</h1>
    <form method="post">
      <input type="hidden" name="do" value="login">
      <input type="password" name="admin_password" placeholder="管理者パスワード" autofocus>
      <button type="submit" class="btn-ok" style="width:100%;">ログイン</button>
    </form>
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
  </div>
{% else %}
  <h1>🛡️ 管理者パネル</h1>

  <h2>🚨 通報された投稿（{{ post_reports|length }}件）</h2>
  {% if not post_reports %}<p class="empty">現在ありません。</p>{% endif %}
  {% for r in post_reports %}
    <div class="card">
      <div class="meta">通報者: {{ r.reporter }} ／ 対象ユーザー: {{ r.reported_user }} ／ {{ r.time }}</div>
      <div class="msg">{{ r.message_snapshot }}</div>
      <div class="meta">通報理由: {{ r.reason }}</div>
      <form method="post" style="display:inline;">
        <input type="hidden" name="do" value="post_approve"><input type="hidden" name="report_id" value="{{ r.id }}">
        <button type="submit" class="btn-ok">✅ 問題なし（以後非表示にしない）</button>
      </form>
      <form method="post" style="display:inline;">
        <input type="hidden" name="do" value="post_remove"><input type="hidden" name="report_id" value="{{ r.id }}">
        <button type="submit" class="btn-danger" onclick="return confirm('この投稿を削除しますか？');">🗑 削除する</button>
      </form>
    </div>
  {% endfor %}

  <h2>💬 通報されたDMメッセージ（{{ dm_cases|length }}件）</h2>
  <p class="empty">※ここで見えるのは通報者が提出した該当メッセージ1件のみです。DM全体の履歴は表示されません。</p>
  {% if not dm_cases %}<p class="empty">現在ありません。</p>{% endif %}
  {% for c in dm_cases %}
    <div class="card">
      <div class="meta">通報者: {{ c.reporter }} ／ 対象ユーザー(送信者): {{ c.accused }} ／ {{ c.time }} ／ 状態: {{ c.status }}</div>
      <div class="msg">「{{ c.reported_message.text }}」（送信: {{ c.reported_message.sender }} / {{ c.reported_message.time }}）</div>
      <div class="meta">通報理由: {{ c.reason }}</div>
      {% if c.status == 'protest_submitted' %}
        <div class="protest">📝 本人からの抗議: {{ c.protest_text }}</div>
      {% elif c.status == 'pending_review' %}
        <p class="empty">まだ本人からの抗議はありません。抗議があった場合のみここに表示されます。</p>
      {% endif %}
      <form method="post" style="display:inline;">
        <input type="hidden" name="do" value="dm_dismiss"><input type="hidden" name="case_id" value="{{ c.id }}">
        <button type="submit" class="btn-neutral">問題なしとして却下</button>
      </form>
      <form method="post" style="display:inline;">
        <input type="hidden" name="do" value="dm_punish"><input type="hidden" name="case_id" value="{{ c.id }}">
        <button type="submit" class="btn-danger" onclick="return confirm('このユーザーのアカウントを次回ログイン時に削除対象にしますか？');">🔨 処分（次回ログイン時にアカウント削除）</button>
      </form>
    </div>
  {% endfor %}

  <h2>⚠️ 違反回数・利用制限中のユーザー</h2>
  <table>
    <tr><th>ユーザー名</th><th>違反回数</th><th>投稿制限</th><th>操作</th></tr>
    {% for u in flagged_users %}
    <tr>
      <td>{{ u.name }}</td><td>{{ u.violation_count }}</td><td>{{ "制限中" if u.restricted else "-" }}</td>
      <td>
        {% if u.restricted %}
        <form method="post" style="display:inline;">
          <input type="hidden" name="do" value="unrestrict"><input type="hidden" name="username" value="{{ u.name }}">
          <button type="submit" class="btn-ok">制限解除</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>

  <p style="margin-top:30px;"><a href="?logout=1">ログアウト</a></p>
{% endif %}
</body></html>
'''

VIOLATION_RESTRICT_THRESHOLD = 3  # この回数だけ削除処分を受けると自動的に投稿制限をかける

@app.route("/adminkonngyokimjonnunnwatasihakannrisyadesuahahahaha", methods=["GET", "POST"])
def admin_panel():
    import flask
    client_ip = flask.request.remote_addr or "unknown"

    if flask.request.method == "GET" and flask.request.args.get("logout"):
        session.pop("is_admin", None)
        return flask.redirect(flask.url_for("admin_panel"))

    error = None
    if flask.request.method == "POST" and flask.request.form.get("do") == "login":
        if is_rate_limited(ADMIN_ATTEMPTS, client_ip, max_attempts=5, window_seconds=600):
            error = "試行回数が多すぎます。しばらく待ってから再度お試しください。"
        else:
            submitted = flask.request.form.get("admin_password", "")
            # タイミング攻撃対策として定数時間比較を使う
            import hmac
            if hmac.compare_digest(submitted, ADMIN_PASSWORD):
                session["is_admin"] = True
                clear_attempts(ADMIN_ATTEMPTS, client_ip)
                return flask.redirect(flask.url_for("admin_panel"))
            else:
                record_failed_attempt(ADMIN_ATTEMPTS, client_ip)
                error = "パスワードが違います。"

    if not session.get("is_admin"):
        return render_template_string(ADMIN_HTML, authed=False, error=error)

    # ── 管理者アクションの処理 ──
    if flask.request.method == "POST":
        do = flask.request.form.get("do")
        data = loaddata()

        if do == "post_approve":
            report_id = flask.request.form.get("report_id")
            reports = load_json_list(POST_REPORTS_FILE)
            rep = next((r for r in reports if r.get("id") == report_id), None)
            if rep:
                rep["status"] = "approved"
                save_json_list(POST_REPORTS_FILE, reports)
                ps = loadposts()
                for p in ps:
                    if str(p.get("id")) == str(rep["post_id"]):
                        p["report_status"] = "approved"
                saveposts(ps)

        elif do == "post_remove":
            report_id = flask.request.form.get("report_id")
            reports = load_json_list(POST_REPORTS_FILE)
            rep = next((r for r in reports if r.get("id") == report_id), None)
            if rep:
                rep["status"] = "removed"
                save_json_list(POST_REPORTS_FILE, reports)
                ps = loadposts()
                ps = [p for p in ps if str(p.get("id")) != str(rep["post_id"])]
                saveposts(ps)
                offender = rep.get("reported_user")
                if offender and offender in data:
                    data[offender]["violation_count"] = data[offender].get("violation_count", 0) + 1
                    if data[offender]["violation_count"] >= VIOLATION_RESTRICT_THRESHOLD:
                        data[offender]["restricted"] = True
                    save_data(data)

        elif do == "dm_dismiss":
            case_id = flask.request.form.get("case_id")
            cases = load_json_list(DM_CASES_FILE)
            case = next((c for c in cases if c.get("id") == case_id), None)
            if case:
                case["status"] = "resolved_ok"
                save_json_list(DM_CASES_FILE, cases)

        elif do == "dm_punish":
            case_id = flask.request.form.get("case_id")
            cases = load_json_list(DM_CASES_FILE)
            case = next((c for c in cases if c.get("id") == case_id), None)
            if case:
                case["status"] = "resolved_removed"
                save_json_list(DM_CASES_FILE, cases)
                offender = case.get("accused")
                if offender and offender in data:
                    # 通報が認定された場合、即座には消さず「次回ログイン時に削除」フラグを立てる
                    # （その間にお知らせを出すため。本来はメール送信すべき部分）
                    data[offender]["pending_deletion"] = True
                    save_data(data)

        elif do == "unrestrict":
            username = flask.request.form.get("username")
            if username in data:
                data[username]["restricted"] = False
                data[username]["violation_count"] = 0
                save_data(data)

    # ── ダッシュボード表示 ──
    post_reports = [r for r in load_json_list(POST_REPORTS_FILE) if r.get("status") == "pending"]
    dm_cases = [c for c in load_json_list(DM_CASES_FILE) if c.get("status") in ("pending_review", "protest_submitted")]
    data = loaddata()
    flagged_users = [
        {"name": name, "violation_count": u.get("violation_count", 0), "restricted": u.get("restricted", False)}
        for name, u in data.items()
        if u.get("violation_count", 0) > 0 or u.get("restricted", False)
    ]

    return render_template_string(
        ADMIN_HTML, authed=True, error=None,
        post_reports=post_reports, dm_cases=dm_cases, flagged_users=flagged_users
    )


@app.route("/api/files")
def api_files():
    import flask
    items = []
    if os.path.exists(UPLOAD_DIR):
        for fname in os.listdir(UPLOAD_DIR):
            ext = os.path.splitext(fname)[1].lower()
            if ext in (".mp4", ".webm"): ftype = "video"
            elif ext in (".mp3", ".wav"): ftype = "audio"
            elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"): ftype = "image"
            else: ftype = "other"
            items.append({"save_name": fname, "type": ftype, "ext": ext, "url": f"/static/uploads/{fname}"})
    return flask.jsonify({"files": items})

# ─────────────────────────────────────────
# /chat AI チャット
# ─────────────────────────────────────────
# ====================== AIチャット ======================
@app.route("/chat")
def AII():
    html = '''
    <!DOCTYPE html>
    <html lang="ja">
    <head>
      <meta charset="utf-8">
      <title>gemini チャット</title>
      <style>
        body { font-family: sans-serif; max-width: 700px; margin: auto; padding: 2em; background: #f8f9fa; }
        .container { background: white; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); padding: 20px; }
        #chatLog { border: 1px solid #ddd; padding: 15px; height: 400px; overflow-y: scroll; margin-bottom: 15px; background: #f9f9f9; border-radius: 8px; }
        #chatLog p { margin: 8px 0; }
        textarea { width: 100%; padding: 12px; border: 1px solid #ccc; border-radius: 8px; resize: vertical; }
        button { margin: 5px 5px 5px 0; padding: 10px 16px; border: none; border-radius: 6px; cursor: pointer; }
        .send { background: #00bcd4; color: white; }
        .clear { background: #f44336; color: white; }
        .file-btn { background: #4caf50; color: white; }
      </style>
    </head>
    <body>
      <div class="container">
        <h2>🧠 geminiAI チャット</h2>
        <label>キャラクター選択: </label>
        <select id="characterSelect">
          <option value="通常">💬 通常</option>
        </select>
        <div id="chatLog"></div>

        <textarea id="userInput" placeholder="質問を入力..." rows="3"></textarea><br>

        <input type="file" id="chatFile" style="display:none;" accept="image/*">
        <button class="file-btn" onclick="document.getElementById('chatFile').click()">📎 ファイル添付</button>
        <button class="send" onclick="sendMessage()">送信</button>
        <button class="clear" onclick="clearChat()">履歴クリア</button>
        <button onclick="location.href='/'">← 戻る</button>
      </div>

      <script>
        let isSending = false;
        const fileInput = document.getElementById("chatFile");

        async function sendMessage() {
          if (isSending) return;
          isSending = true;

          const inputBox = document.getElementById("userInput");
          const text = inputBox.value.trim();
          const character = document.getElementById("characterSelect").value;
          const chatLog = document.getElementById("chatLog");

          const formData = new FormData();
          formData.append("message", text);
          formData.append("character", character);

          if (fileInput.files.length > 0) {
            formData.append("file", fileInput.files[0]);
            chatLog.innerHTML += `<p><strong>あなた:</strong> [ファイル添付]</p>`;
          } else if (text) {
            chatLog.innerHTML += `<p><strong>あなた:</strong> ${text}</p>`;
            inputBox.value = "";
          } else {
            isSending = false;
            return;
          }

          // 考え中
          const thinking = document.createElement("p");
          thinking.id = "thinking";
          thinking.innerHTML = `<strong>${character}:</strong> 考え中...`;
          chatLog.appendChild(thinking);
          chatLog.scrollTop = chatLog.scrollHeight;

          try {
            const response = await fetch("/api/chat", {
              method: "POST",
              body: formData
            });
            const data = await response.json();

            thinking.remove();
            chatLog.innerHTML += `<p><strong>${character}:</strong> ${data.reply}</p>`;
            chatLog.scrollTop = chatLog.scrollHeight;
          } catch(e) {
            thinking.remove();
            chatLog.innerHTML += `<p><strong>${character}:</strong> エラー</p>`;
          }

          isSending = false;
          fileInput.value = ""; // リセット
        }
      </script>
    </body>
    </html>
    '''
    return render_template_string(html)



@app.route("/api/chat", methods=["POST"])
def chi():
    try:
        # ファイル対応
        message = request.form.get("message", "")
        character = request.form.get("character", "通常")
        file = request.files.get("file")

        if file and file.filename:
            # ファイル処理（必要なら保存）
            print(f"ファイル受信: {file.filename}")

        # Geminiで返信生成
        reply = generate_reply([{"role": "user", "content": message}], character)

        return jsonify({"reply": reply})

    except Exception as e:
        print("Chat Error:", str(e))
        return jsonify({"reply": f"エラー: {str(e)}"})

application = app
