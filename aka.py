import os
import re
import json
from flask import (
    Flask, render_template, render_template_string, request,
    redirect, url_for, session, jsonify, send_from_directory,
    make_response, abort
)
from chat import generate_reply
from PIL import Image
from werkzeug.utils import secure_filename
import urllib.parse
import zlib
import base64
import random
import string
import uuid
from datetime import datetime

# ─────────────────────────────────────────
# 基本設定
# ─────────────────────────────────────────
# -*- coding: utf-8 -*-
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "static", "icns")
POSTS_FILE = os.path.join(BASE_DIR, "posts.json")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
NOVELS_FILE = os.path.join(BASE_DIR, "novls.json") # ★新規：小説管理JSON

os.makedirs(ICON_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, template_folder="mysite/templates")
app.secret_key = "akai"
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./.flask_session"
app.config["SESSION_PERMANENT"] = False

MEDIA_DIR = BASE_DIR
USER_SESSIONS = {}
access_count = 0


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

def loaddata():
    if not os.path.exists(DATA_FILE): return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def loadposts():
    if not os.path.exists(POSTS_FILE): return []
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return []

def saveposts(posts):
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=4)


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
        display_title = "学校でyoutubeを見る方法 〜完全版〜"
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

    # プレイヤーの出し分け (UIはaka.pyに合わせて width="640" に統一)
    if "mega.nz/embed" in filename:
        content = f'<iframe width="640" height="360" frameborder="0" src="{filename}" allowfullscreen tabindex="-1"></iframe>'
    elif filename.startswith("http"):
        content = f'<video controls autoplay width="640" tabindex="-1"><source src="{filename}" type="video/mp4"></video>'
    else:
        src = f"/static/uploads/{filename[len('__upload__'):]}" if filename.startswith("__upload__") else f"/media/{filename}"
        content = f'<video controls autoplay width="640" tabindex="-1"><source src="{src}" type="video/mp4"></video>'

    # 元の aka.py らしい飾らないHTML構造 [cite: 1]
    html = f'''
    <h2>🎬 再生中: {display_title}</h2>
    {content}
    <p><a href="/full">← 戻る</a></p>
    <p>アクセス数: {access_count}</p>
    {extra_script}
    '''

    return render_template_string(html)

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
                classroom_videos.append({"value": tagged, "text": "[classroom] " + f})
            elif ext in (".jpg", ".jpeg", ".png", ".gif"):
                image_files.append(tagged)

    # ★ここで合流（ローカル名前順 + 外部テキスト順 + クラスルーム順）
    video_files = external_videos + local_videos + classroom_videos

    html = r'''
    <h2>🎧 音声ファイル一覧</h2>
    <form method="get" action="/play">
      <select name="filename">
        {% for f in audio %}
          <option value="{{ f }}">{{ f.replace("__upload__","[classroom] ") }}</option>
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
          <option value="{{ f }}">{{ f.replace("__upload__","[classroom] ") }}</option>
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
    <a href="/classroom">
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
    return f'<audio controls autoplay><source src="{src}" type="audio/mpeg"></audio><p><a href="/full">← 戻る</a></p><p>アクセス数: {access_count}</p>'

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
    return f'<img src="{src}" alt="{filename}" style="max-width:480px; height:auto;"><p><a href="/full">← 戻る</a></p><p>アクセス数: {access_count}</p>'

@app.route("/media/<filename>")
def media(filename):
    global access_count
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
    filename = request.args.get("filename")
    target_media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
    file_path = os.path.join(target_media_dir, filename)

    if not os.path.exists(file_path):
        return f"ファイルが見つかりません: {file_path}", 404

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
@app.route("/classroom", methods=["GET", "POST"])
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
            icon_file.save(os.path.join(ICON_DIR, fname))
            user["icon_filename"] = fname
            data = loaddata()
            name = user["student_name"]
            if name in data:
                data[name]["icon"] = fname
                save_data(data)
            return flask.jsonify({"status": "ok", "icon": fname})

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
            name = flask.request.form.get("username")
            data = loaddata()
            if mode == "register":
                icon_file = flask.request.files.get("icon")
                if not icon_file or name in data:
                    return flask.jsonify({"status": "error", "error": "register failed"}), 400
                fname = random_filename(16, ".png")
                icon_file.save(os.path.join("static", "icons", fname))
                data[name] = {"icon": fname}
                save_data(data)
            else:
                fname = data.get(name, {}).get("icon")
                if not fname:
                    ps = loadposts()
                    fname = next((p["icon"] for p in reversed(ps) if p.get("user") == name), "default.png")
            new_sid = str(uuid.uuid4())[:8]
            USER_SESSIONS[new_sid] = {"student_name": name, "icon_filename": fname}
            return flask.jsonify({"status": "ok", "sid": new_sid, "icon": fname})

        # 投稿処理 (フロント追加オプション機能対応版)
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
                
                # 新機能パラメータの受け取り
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

                    if ext in (".mp4", ".webm"): ftype = "video"
                    elif ext in (".mp3", ".wav"): ftype = "audio"
                    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"): ftype = "image"
                    else: ftype = "other"

                    new_post["file"] = {"save_name": sv_name, "type": ftype, "ext": ext, "original_name": post_file.filename}

                ps.append(new_post)
                saveposts(ps)
                return flask.jsonify({"status": "ok"})

            except Exception as e:
                print("投稿エラー:", str(e))
                return flask.jsonify({"status": "error", "error": str(e)}), 500

        # その他のアクション
        else:
            return flask.jsonify({"status": "error", "error": "不明なaction_type"}), 400

    # GET処理
    get_type = flask.request.args.get("get_type")
    user_dict = loaddata()
    all_posts = loadposts()

    if get_type == "json":
        formatted_list = []
        for p in all_posts:
            item = p.copy()
            item["likes"] = int(item.get("likes", 0))
            if "type" in item: del item["type"]
            formatted_list.append(item)

        for name, info in user_dict.items():
            formatted_list.append({"type": "user", "user": name, "icon": info.get("icon", "default.png")})

        valid_hot_posts = [p for p in all_posts if int(p.get("likes", 0)) > 0]
        hot_posts = sorted(valid_hot_posts, key=lambda x: int(x.get("likes", 0)), reverse=True)[:5]

        return flask.jsonify({"all": formatted_list, "hot": hot_posts, "access_count": access_count})

    # 通常のHTMLレンダリング
    sid = flask.request.args.get("sid")
    user_session = USER_SESSIONS.get(sid) if sid else None
    reg_list = list(user_dict.keys())
    access_count += 1
    return flask.render_template(
        "main.html",
        sid=sid,
        username=user_session["student_name"] if user_session else "Guest",
        user_icon_filename=user_session["icon_filename"] if user_session else "default.png",
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
    action = data.get("action")
    ps = loadposts()

    for p in ps:
        if str(p.get("id")) == str(post_id):
            current_likes = int(p.get("likes", 0))
            if action == "plus":
                p["likes"] = current_likes + 1
            elif action == "minus":
                p["likes"] = max(0, current_likes - 1)
            break

    saveposts(ps)
    return flask.jsonify({"status": "ok"})


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
      <title>OPPAI チャット</title>
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
        <h2>🧠 OPPAI チャット</h2>
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
