import os
import json
from dotenv import load_dotenv
import google.generativeai as genai
from google.generativeai.types import SafetySettingDict
import uuid
import requests  # 画像生成用

load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

safety_settings: list[SafetySettingDict] = [
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

CHARACTERS_DIR = "characters"
os.makedirs(CHARACTERS_DIR, exist_ok=True)

DEFAULT_CHARACTERS = {
    "通常": {
        "emoji": "💬",
        "persona": "あなたは親切で論理的なAIアシスタントです。",
        "values": [],
        "lex_filter": [],
        "style_template": {
            "ending": "",
            "tone": "ニュートラル",
            "structure": "簡潔に答えるが、短すぎないように。"
        },
        "config": {"temperature": 1.0}
    },
}

def save_character(name, data):
    filepath = os.path.join(CHARACTERS_DIR, f"{name}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_character(name):
    filepath = os.path.join(CHARACTERS_DIR, f"{name}.json")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_CHARACTERS.get(name, DEFAULT_CHARACTERS["通常"])

def build_prompt(history, char):
    persona = char["persona"]
    values = "\n".join(f"- {v}" for v in char.get("values", []))
    lex = ", ".join(char.get("lex_filter", []))
    style = char.get("style_template", {})
    dialogue = "\n".join([f"{turn['role']}: {turn['content']}" for turn in history])
    prompt = f"""{persona}
価値観:
{values}
語彙: {lex}
話し方: {style.get('tone', '普通')}、語尾「{style.get('ending', '')}」、{style.get('structure', '')}
{dialogue}
assistant:"""
    return prompt

def generate_reply(history, character_name):
    char = load_character(character_name)
    prompt = build_prompt(history, char)
    try:
        model = genai.GenerativeModel(
            "gemini-2.5-flash-lite",
            generation_config=char.get("config", {"temperature": 1.0}),
            safety_settings=safety_settings,
            system_instruction=char["persona"]
        )
        response = model.generate_content(prompt)
        reply = response.text.strip()
        reply = reply.replace("**", "").replace("__", "").strip()
        return reply
    except Exception as e:
        print("Gemini Error:", str(e))
        return f"Geminiエラー: {str(e)}"

# ファイル添付対応
def save_uploaded_file(file):
    if not file or not file.filename:
        return None
    filename = f"{uuid.uuid4()}_{file.filename}"
    filepath = os.path.join("uploads", filename)
    os.makedirs("uploads", exist_ok=True)
    file.save(filepath)
    return filepath

__all__ = ["generate_reply", "save_uploaded_file", "load_character"]
