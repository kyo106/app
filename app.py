import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ==================== 1. 環境變數與全域設定 ====================
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

live_mushrooms = []
notified_ids = set()

# 地名座標快取
LOCATION_COORD_CACHE = {
    "kolkata": (22.572646, 88.363895),
    "加爾各答": (22.572646, 88.363895),
    "philadelphia": (39.952584, -75.165222),
    "費城": (39.952584, -75.165222),
    "valparaiso": (-33.047238, -71.612688),
    "瓦爾帕萊索": (-33.047238, -71.612688),
    "david": (8.4273, -82.4309),
    "戴維": (8.4273, -82.4309),
    "sucre": (-19.047725, -65.259431),
    "蘇克雷": (-19.047725, -65.259431),
    "tegucigalpa": (14.072275, -87.192136),
    "德古西加巴": (14.072275, -87.192136),
    "pecs": (46.0727, 18.2323),
    "佩奇": (46.0727, 18.2323),
    "managua": (12.114993, -86.236174),
    "馬拿瓜": (12.114993, -86.236174),
    "manila": (14.599512, 120.984222),
    "馬尼拉": (14.599512, 120.984222),
    "da nang": (16.054407, 108.202167),
    "峴港": (16.054407, 108.202167),
    "houston": (29.760427, -95.369803),
    "休士頓": (29.760427, -95.369803),
    "salto": (-31.383333, -57.966667),
    "薩爾托": (-31.383333, -57.966667),
    "portland": (45.5152, -122.6784),
    "波特蘭": (45.5152, -122.6784),
    "daegu": (35.87028, 128.59111),
    "大邱": (35.87028, 128.59111),
    "christchurch": (-43.53333, 172.63333),
    "基督城": (-43.53333, 172.63333),
    "seoul": (37.566, 126.9784),
    "首爾": (37.566, 126.9784),
    "busan": (35.10168, 129.03004),
    "釜山": (35.10168, 129.03004)
}

LEVEL_MAP = {
    3: "大蘑菇",
    4: "巨大蘑菇"
}

TYPE_MAP = {
    "2": "紅色", "6": "黃色", "3": "灰色", "8": "紫色",
    "7": "白色", "9": "粉紅色", "5": "藍色",
    "10": "神秘活動", "19": "活動特殊", "22": "活動特殊", "23": "活動特殊",
    "17": "電", "12": "水", "11": "火", "13": "水晶",
    "18": "毒", "ice": "冰藍", "26": "冰藍",
    "event": "神秘活動", "mystery": "神秘活動"
}

# 【純元素目標白名單】：已移除 26 與 ice (不推播冰藍)，僅保留 電、水、火、水晶、毒
TARGET_SPECIAL_TYPES = {"11", "12", "13", "17", "18"}

# ==================== 2. 地理位置解析與概略座標計算 ====================
def extract_location_text(item):
    country = str(item.get("location_country") or "").strip()
    city = str(item.get("location_city") or "").strip()

    if country and city:
        return f"{country}-{city}"
    elif country:
        return country
    elif city:
        return city

    for key in ["location", "place", "area", "address"]:
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    return "未知區域"

def geocode_location_to_coords(location_text):
    if not location_text or location_text == "未知區域":
        return None, None

    if location_text in LOCATION_COORD_CACHE:
        return LOCATION_COORD_CACHE[location_text]

    english_part = re.sub(r'[\u4e00-\u9fff\-]', ' ', location_text).strip()
    english_part = re.sub(r'\s+', ' ', english_part).lower()

    chinese_part = re.sub(r'[a-zA-Z\-]', ' ', location_text).strip()
    chinese_part = re.sub(r'\s+', ' ', chinese_part)

    # 查離線字典
    if english_part and english_part in LOCATION_COORD_CACHE:
        res = LOCATION_COORD_CACHE[english_part]
        LOCATION_COORD_CACHE[location_text] = res
        return res

    if chinese_part:
        for p in chinese_part.split():
            if p in LOCATION_COORD_CACHE:
                res = LOCATION_COORD_CACHE[p]
                LOCATION_COORD_CACHE[location_text] = res
                return res

    # 快速線上查詢 (Open-Meteo)
    search_terms = []
    if len(english_part) >= 3:
        search_terms.append(english_part)
    if chinese_part:
        search_terms.append(chinese_part.split()[-1])

    for term in search_terms:
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            resp = requests.get(url, params={"name": term, "count": 1, "language": "zh"}, timeout=2.0)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    lat = round(float(results[0]["latitude"]), 6)
                    lng = round(float(results[0]["longitude"]), 6)
                    LOCATION_COORD_CACHE[location_text] = (lat, lng)
                    return lat, lng
        except Exception:
            pass

    LOCATION_COORD_CACHE[location_text] = (None, None)
    return None, None

# ==================== 3. Discord Webhook 發送函式 =================
