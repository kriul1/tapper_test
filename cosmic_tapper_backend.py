"""
╔══════════════════════════════════════════════════════════════════════════╗
║        🌌 COSMIC TAPPER v4 ULTIMATE — TWA Backend                      ║
║  FastAPI + python-telegram-bot + aiosqlite                              ║
║  Вся игровая логика на сервере — защита от читов                        ║
╚══════════════════════════════════════════════════════════════════════════╝

УСТАНОВКА:
  pip install python-telegram-bot==20.7 aiosqlite fastapi uvicorn python-dotenv

ЗАПУСК:
  python cosmic_tapper_backend.py
  Или задать переменные окружения: BOT_TOKEN, WEBAPP_URL
"""

import os, sys, json, time, atexit, asyncio, logging, aiosqlite, math, random, hashlib, hmac
from urllib.parse import unquote, parse_qsl

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ─── КОНФИГУРАЦИЯ ────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.getenv("BOT_TOKEN",  "8925431626:AAF_MIKtKgQWNP8ygxTo-ON59rNF7yTr2Jg")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://unwrapped-succulent-envy.ngrok-free.dev")  # URL фронтенда

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(_BASE_DIR, "cosmic_tapper_v4.db")
LOCK_PATH  = os.path.join(_BASE_DIR, ".cosmic_tapper_v4.lock")
API_PORT   = int(os.getenv("API_PORT", "8000"))

# Лимиты прогрессии
MAX_REBIRTH   = 150
MAX_PRESTIGE  = 70
MAX_ASCENSION = 35
TAP_COOLDOWN  = 0.05   # минимальный интервал тапа (сек)

# ─── SINGLE-INSTANCE LOCK ────────────────────────────────────────────────────
def _pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if h: ctypes.windll.kernel32.CloseHandle(h); return True
        return False
    try: os.kill(pid, 0)
    except OSError: return False
    return True

def acquire_lock():
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH) as f: old = int(f.read().strip())
        except: old = 0
        if _pid_alive(old):
            raise RuntimeError(f"Бот уже запущен (PID {old}).")
        os.remove(LOCK_PATH)
    with open(LOCK_PATH, "w") as f: f.write(str(os.getpid()))
    def _rel():
        try:
            if os.path.exists(LOCK_PATH):
                with open(LOCK_PATH) as f:
                    if f.read().strip() == str(os.getpid()): os.remove(LOCK_PATH)
        except: pass
    atexit.register(_rel)

# ─── ТАБЛИЦЫ БОНУСОВ ────────────────────────────────────────────────────────
def _gen_rebirth_table(mx=MAX_REBIRTH):
    return {i: (1.45**i)*(1+i*0.18) if i else 1.0 for i in range(mx+1)}

def _gen_prestige_table(mx=MAX_PRESTIGE):
    return {i: (2.0**i)*(1+i*0.3) if i else 1.0 for i in range(mx+1)}

REBIRTH_BONUS_TABLE  = _gen_rebirth_table()
PRESTIGE_BONUS_TABLE = _gen_prestige_table()

PRESTIGE_UNLOCKS = {
    1:"🔓 Автокликер I", 2:"🔓 Комбо", 3:"🔓 Крит-тап",
    5:"🔓 Пассивный доход I", 8:"🔓 Легендарные (H)",
    10:"🔓 Квесты", 15:"🔓 Пассивный доход II", 20:"🔓 Вознесение",
    25:"🔓 Омега (I)", 30:"🔓 Месячные квесты", 35:"🔓 Экстрим (J)",
    40:"🔓 Бесконечность (K)", 50:"🔓 МегаСтак (L)",
    60:"🔓 Берсерк (M)", 65:"🔓 Маска (N) + РЕЖИМ БОГА",
}

ASCENSION_BONUSES = {
    1:(  "Мощь тапа ×20",       20,   1),
    2:(  "Офлайн доход ×10",     1,  10),
    3:(  "Цена улучшений −70%",  1,   1),
    4:(  "Шанс крита +40%",      1,   1),
    5:(  "БЕСКОНЕЧНЫЙ АВТОТАП",  1,   1),
    10:( "Все доходы ×100",    100, 100),
    15:( "Экспа за квесты ×3",   1,   1),
    20:( "Крит урон ×999",       1,   1),
    25:( "АБСОЛЮТНЫЙ РЕЖИМ",  1000,1000),
    35:( "РЕЖИМ СОЗДАТЕЛЯ ×∞",99999,99999),
}

# ─── УЛУЧШЕНИЯ (14 веток) ───────────────────────────────────────────────────
_MAX_COST = 10**300

def _sc(base, growth, level):
    try:
        v = base*(growth**level)
        return _MAX_COST if v>_MAX_COST else int(v)
    except OverflowError: return _MAX_COST

def _sb(base, growth, level):
    try: return min(base*(growth**(level-1)), 1e300)
    except OverflowError: return 1e300

def _make_upgrades(mx=150):
    u={}
    for i in range(1,mx+1):
        u[f"tap_{i}"]       ={"name":f"Тап Мощь {i}",    "tap_bonus":_sb(2,2,i)*(1+i*0.15),            "cost":_sc(100,3.5,i-1),        "branch":"A","emoji":"👆","prestige_req":0}
        u[f"auto_{i}"]      ={"name":f"Автокликер {i}",   "auto_bonus":_sb(1,3,i)*(1+i*0.25),           "cost":_sc(300,3.8,i-1),        "branch":"B","emoji":"🤖","prestige_req":1}
        u[f"mult_{i}"]      ={"name":f"Суперсила {i}",    "mult_bonus":1+(0.35*i),                       "cost":_sc(800,4.2,i-1),        "branch":"C","emoji":"⚡","prestige_req":0}
        u[f"crit_{i}"]      ={"name":f"Удача {i}",        "crit_bonus":0.008*i,"crit_mult":1.5+(0.7*i),  "cost":_sc(600,4.0,i-1),        "branch":"D","emoji":"🎯","prestige_req":3}
        u[f"passive_{i}"]   ={"name":f"Золотой поток {i}","passive_bonus":_sb(50,3,i),                   "cost":_sc(2000,4.5,i-1),       "branch":"E","emoji":"💰","prestige_req":5}
        u[f"offline_{i}"]   ={"name":f"Ночной доход {i}", "offline_mult":1+(0.18*i),                     "cost":_sc(1500,4.3,i-1),       "branch":"F","emoji":"😴","prestige_req":0}
        u[f"combo_{i}"]     ={"name":f"Комбо {i}",        "combo_bonus":0.08*i,                          "cost":_sc(1200,4.1,i-1),       "branch":"G","emoji":"🔥","prestige_req":2}
        u[f"legend_{i}"]    ={"name":f"Легенда {i}",      "tap_bonus":_sb(5,5,i)*1.5,                    "cost":_sc(10000,5.0,i-1),      "branch":"H","emoji":"🌟","prestige_req":8}
        u[f"omega_{i}"]     ={"name":f"Омега {i}",        "tap_bonus":_sb(10,10,i)*2,"auto_bonus":_sb(5,5,i)*1.5, "cost":_sc(50000,6.0,i-1),  "branch":"I","emoji":"🌌","prestige_req":25}
        u[f"extreme_{i}"]   ={"name":f"Экстрим {i}",      "tap_bonus":_sb(20,20,i)*3,"mult_bonus":2+(i*0.5),       "cost":_sc(100000,8.0,i-1), "branch":"J","emoji":"💥","prestige_req":35}
        u[f"infinity_{i}"]  ={"name":f"Бесконечность {i}","tap_bonus":_sb(100,100,i)*5,"auto_bonus":_sb(50,50,i)*3,"mult_bonus":5+(i*1.0),"cost":_sc(999999,10.0,i-1),"branch":"K","emoji":"∞","prestige_req":40}
        u[f"megastack_{i}"] ={"name":f"МегаСтак {i}",     "tap_bonus":_sb(500,500,i)*10,"passive_bonus":_sb(10000,5,i),"mult_bonus":10+(i*2),"cost":_sc(10000000,12.0,i-1),"branch":"L","emoji":"🧱","prestige_req":50}
        u[f"berserk_{i}"]   ={"name":f"Берсерк {i}",      "tap_bonus":_sb(2000,2000,i)*25,"crit_bonus":0.01*i,"crit_mult":10+(i*3),"cost":_sc(100000000,15.0,i-1),"branch":"M","emoji":"🪓","prestige_req":60}
        u[f"mask_{i}"]      ={"name":f"Маска {i}",        "tap_bonus":_sb(10000,10000,i)*50,"auto_bonus":_sb(5000,5000,i)*30,"passive_bonus":_sb(500000,8,i),"mult_bonus":50+(i*5),"cost":_sc(1000000000,20.0,i-1),"branch":"N","emoji":"🎭","prestige_req":65}
    return u

UPGRADES = _make_upgrades()

BRANCH_INFO = {
    "A":("👆","Мощь тапа",     "tap_",      0),
    "B":("🤖","Автокликер",    "auto_",     1),
    "C":("⚡","Множитель",     "mult_",     0),
    "D":("🎯","Крит-шанс",     "crit_",     3),
    "E":("💰","Пассив. доход", "passive_",  5),
    "F":("😴","Офлайн доход",  "offline_",  0),
    "G":("🔥","Комбо",         "combo_",    2),
    "H":("🌟","Легендарные",   "legend_",   8),
    "I":("🌌","Омега",         "omega_",   25),
    "J":("💥","Экстрим",       "extreme_", 35),
    "K":("∞", "Бесконечность","infinity_",40),
    "L":("🧱","МегаСтак",     "megastack_",50),
    "M":("🪓","Берсерк",      "berserk_",  60),
    "N":("🎭","Маска",         "mask_",    65),
}

# ─── ДОСТИЖЕНИЯ ──────────────────────────────────────────────────────────────
ACHIEVEMENTS = {
    "first_tap":    {"name":"Первый шаг",       "desc":"Сделайте первый тап",          "reward":100,       "cond":lambda p:p.get("taps",0)>=1},
    "tapper_100":   {"name":"Тапер",            "desc":"100 тапов",                    "reward":1000,      "cond":lambda p:p.get("taps",0)>=100},
    "tapper_1k":    {"name":"Кликер",           "desc":"1 000 тапов",                  "reward":5000,      "cond":lambda p:p.get("taps",0)>=1000},
    "tapper_10k":   {"name":"Гипер-кликер",     "desc":"10 000 тапов",                 "reward":30000,     "cond":lambda p:p.get("taps",0)>=10000},
    "tapper_100k":  {"name":"Ультра-кликер",    "desc":"100 000 тапов",                "reward":200000,    "cond":lambda p:p.get("taps",0)>=100000},
    "tapper_1m":    {"name":"БЕСКОНЕЧНЫЙ ТАП",  "desc":"1 000 000 тапов",              "reward":5000000,   "cond":lambda p:p.get("taps",0)>=1000000},
    "earn_1k":      {"name":"Копилка",          "desc":"Заработайте 1 000 монет",      "reward":500,       "cond":lambda p:p.get("earned",0)>=1000},
    "earn_1m":      {"name":"Миллионер",        "desc":"Заработайте 1M монет",         "reward":10000,     "cond":lambda p:p.get("earned",0)>=1000000},
    "earn_1b":      {"name":"Миллиардер",       "desc":"Заработайте 1B монет",         "reward":1000000,   "cond":lambda p:p.get("earned",0)>=1000000000},
    "earn_1t":      {"name":"ТРИЛИОНЕР",        "desc":"Заработайте 1T монет",         "reward":50000000,  "cond":lambda p:p.get("earned",0)>=1000000000000},
    "rebirth_1":    {"name":"Возрождение",      "desc":"Первый ребирт",                "reward":2000,      "cond":lambda p:p.get("rebirth",0)>=1},
    "rebirth_10":   {"name":"Мастер ребиртов",  "desc":"10 ребиртов",                  "reward":25000,     "cond":lambda p:p.get("rebirth",0)>=10},
    "rebirth_50":   {"name":"Ветеран",          "desc":"50 ребиртов",                  "reward":500000,    "cond":lambda p:p.get("rebirth",0)>=50},
    "rebirth_max":  {"name":"ФИНАЛЬНЫЙ РЕБИРТ", "desc":f"{MAX_REBIRTH} ребиртов",      "reward":10000000,  "cond":lambda p:p.get("rebirth",0)>=MAX_REBIRTH},
    "prestige_1":   {"name":"Элита",            "desc":"Первый престиж",               "reward":50000,     "cond":lambda p:p.get("prestige",0)>=1},
    "prestige_5":   {"name":"Ван-Пис",          "desc":"5 престижей",                  "reward":200000,    "cond":lambda p:p.get("prestige",0)>=5},
    "prestige_20":  {"name":"Трансцендент",     "desc":"20 престижей",                 "reward":2000000,   "cond":lambda p:p.get("prestige",0)>=20},
    "prestige_max": {"name":"БОЖЕСТВЕННЫЙ",     "desc":f"{MAX_PRESTIGE} престижей",    "reward":100000000, "cond":lambda p:p.get("prestige",0)>=MAX_PRESTIGE},
    "ascend_1":     {"name":"Вознесение",       "desc":"Первое вознесение",            "reward":5000000,   "cond":lambda p:p.get("ascension",0)>=1},
    "ascend_max":   {"name":"СОЗДАТЕЛЬ",        "desc":f"{MAX_ASCENSION} вознесений",  "reward":999999999, "cond":lambda p:p.get("ascension",0)>=MAX_ASCENSION},
    "speed_demon":  {"name":"Демон скорости",   "desc":"5 критов подряд",              "reward":15000,     "cond":lambda p:p.get("crit_streak",0)>=5},
    "crit_master":  {"name":"Повелитель крита", "desc":"50 критов подряд",             "reward":500000,    "cond":lambda p:p.get("crit_streak",0)>=50},
    "combo_king":   {"name":"Король комбо",     "desc":"Комбо ×50",                    "reward":100000,    "cond":lambda p:p.get("combo",0)>=50},
    "boss_slain":   {"name":"Убийца боссов",    "desc":"Победите первого босса",       "reward":20000,     "cond":lambda p:p.get("bosses_killed",0)>=1},
    "boss_master":  {"name":"Мастер боссов",    "desc":"Победите 10 боссов",           "reward":200000,    "cond":lambda p:p.get("bosses_killed",0)>=10},
    "clan_member":  {"name":"Клановый воин",    "desc":"Вступите в клан",              "reward":10000,     "cond":lambda p:p.get("clan_id") is not None},
}

# ─── СОБЫТИЯ ─────────────────────────────────────────────────────────────────
EVENTS = [
    {"name":"☄️ Метеоритный дождь", "desc":"Все доходы ×10 на 30 мин!",        "tap_mult":10,"auto_mult":10,"passive_mult":10,"duration":1800,"emoji":"☄️"},
    {"name":"⚡ Грозовой шторм",    "desc":"Мощь тапа ×5 на 15 мин!",           "tap_mult":5,                                  "duration":900, "emoji":"⚡"},
    {"name":"💎 Алмазный дождь",    "desc":"Пассив. доход ×50 на 15 мин!",                             "passive_mult":50,    "duration":900, "emoji":"💎"},
    {"name":"🌙 Лунная власть",     "desc":"Офлайн доход ×100 на час!",                                "offline_mult":100,   "duration":3600,"emoji":"🌙"},
    {"name":"🌀 Космический вихрь", "desc":"Автодоход ×25 на 20 мин!",                   "auto_mult":25,                        "duration":1200,"emoji":"🌀"},
    {"name":"🔮 Кристальный пульс", "desc":"Шанс крита 100% на 10 мин!",        "crit_override":1.0,                           "duration":600, "emoji":"🔮"},
    {"name":"🌈 Радужная волна",    "desc":"Все доходы ×30 на 10 мин!",         "tap_mult":30,"auto_mult":30,"passive_mult":30,"duration":600, "emoji":"🌈"},
    {"name":"💫 Звёздный шквал",   "desc":"Мощь тапа ×20, комбо не сбрасывается 5 мин!", "tap_mult":20,"combo_freeze":True,"duration":300,"emoji":"💫"},
]

# ─── БОССЫ ───────────────────────────────────────────────────────────────────
BOSSES = {
    "slime":     {"name":"Космический слизень",  "hp":500,           "reward":5000,          "emoji":"🟢","prestige_req":0},
    "goblin":    {"name":"Звёздный гоблин",      "hp":5000,          "reward":50000,         "emoji":"👹","prestige_req":0},
    "dragon":    {"name":"Галактический дракон", "hp":100000,        "reward":1000000,       "emoji":"🐉","prestige_req":1},
    "titan":     {"name":"Небесный титан",       "hp":1000000,       "reward":20000000,      "emoji":"🗿","prestige_req":3},
    "void_lord": {"name":"Лорд Пустоты",         "hp":50000000,      "reward":500000000,     "emoji":"🌑","prestige_req":8},
    "time_god":  {"name":"Бог Времени",          "hp":1000000000,    "reward":10000000000,   "emoji":"⏰","prestige_req":20},
    "cosmic_king":{"name":"Космический Царь",    "hp":99999999999,   "reward":999999999999,  "emoji":"👑","prestige_req":50},
}

# ─── КВЕСТЫ ──────────────────────────────────────────────────────────────────
DAILY_QUESTS = [
    {"name":"Заработок 10K","type":"earn","target":10000,"reward":5000,"emoji":"💰"},
    {"name":"100 тапов","type":"taps","target":100,"reward":3000,"emoji":"👆"},
    {"name":"Купить 3 улучшения","type":"upgrade","target":3,"reward":4000,"emoji":"🛠"},
    {"name":"Крит × 10","type":"crits","target":10,"reward":6000,"emoji":"🎯"},
    {"name":"500 тапов","type":"taps","target":500,"reward":10000,"emoji":"👆"},
    {"name":"Заработок 1M","type":"earn","target":1000000,"reward":50000,"emoji":"💸"},
]
HOURLY_QUESTS = [
    {"name":"50 быстрых тапов","type":"fast_taps","target":50,"reward":1000,"emoji":"⚡"},
    {"name":"Заработок 5K","type":"earn","target":5000,"reward":1500,"emoji":"💵"},
    {"name":"3 крита подряд","type":"crit_combo","target":3,"reward":2000,"emoji":"🔥"},
]
MONTHLY_QUESTS = [
    {"name":"Заработать 100M монет","type":"earn","target":100000000,"reward":500000,"emoji":"💎"},
    {"name":"5 000 тапов","type":"taps","target":5000,"reward":300000,"emoji":"👆"},
    {"name":"Ребирт 10 раз","type":"rebirths","target":10,"reward":1000000,"emoji":"🔄"},
    {"name":"500 критов","type":"crits","target":500,"reward":400000,"emoji":"💥"},
    {"name":"Победить 5 боссов","type":"boss_kill","target":5,"reward":2000000,"emoji":"🐉"},
]

# ─── БД ──────────────────────────────────────────────────────────────────────
DB: aiosqlite.Connection | None = None
_USER_LOCKS: dict[int, asyncio.Lock] = {}
APP: Application | None = None

def get_lock(uid: int) -> asyncio.Lock:
    if uid not in _USER_LOCKS: _USER_LOCKS[uid] = asyncio.Lock()
    return _USER_LOCKS[uid]

async def init_db():
    global DB
    DB = await aiosqlite.connect(DB_PATH)
    await DB.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY, username TEXT, data TEXT NOT NULL)""")
    await DB.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
            members TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, level INTEGER DEFAULT 1)""")
    await DB.commit()

# ─── ИГРОК ───────────────────────────────────────────────────────────────────
def default_player() -> dict:
    now = time.time()
    return {
        "coins":0,"earned":0,"rebirth":0,"prestige":0,"ascension":0,
        "taps":0,"last_tap":0.0,"last_seen":now,
        "combo":0,"crit_streak":0,"total_crits":0,
        "upgrades":{},
        "achievements":[],
        "event_end":0.0,"event_tap_mult":1.0,"event_auto_mult":1.0,
        "event_passive_mult":1.0,"event_offline_mult":1.0,
        "event_crit_override":-1.0,"event_combo_freeze":False,
        "boss_key":None,"boss_hp":0,"bosses_killed":0,
        "daily_quest":None,"hourly_quest":None,"monthly_quest":None,
        "daily_progress":{},"hourly_progress":{},"monthly_progress":{},
        "quest_reset_daily":0.0,"quest_reset_hourly":0.0,"quest_reset_monthly":0.0,
        "clan_id":None,"session_start":now,
    }

def _migrate(p: dict) -> dict:
    for k,v in default_player().items():
        if k not in p: p[k]=v
    return p

async def load_player(uid: int) -> dict:
    async with DB.execute("SELECT data FROM players WHERE user_id=?", (uid,)) as cur:
        row = await cur.fetchone()
    return _migrate(json.loads(row[0])) if row else default_player()

async def save_player(uid: int, p: dict, username: str|None=None):
    await DB.execute(
        "INSERT OR REPLACE INTO players (user_id,username,data) VALUES(?,?,?)",
        (uid, username, json.dumps(p)))
    await DB.commit()

# ─── ВЫЧИСЛЕНИЯ (СЕРВЕРНАЯ ЛОГИКА) ───────────────────────────────────────────
def _upgrades_sum(p, field):
    total=0.0
    for k,lv in p.get("upgrades",{}).items():
        if lv<=0: continue
        u=UPGRADES.get(k)
        if u and field in u: total+=u[field]*lv
    return total

def _upgrades_mult(p, field):
    mult=1.0
    for k,lv in p.get("upgrades",{}).items():
        if lv<=0: continue
        u=UPGRADES.get(k)
        if u and field in u: mult*=(1+u[field])**lv
    return mult

def calc_tap_power(p: dict) -> float:
    base=1.0+_upgrades_sum(p,"tap_bonus")
    base*=REBIRTH_BONUS_TABLE.get(min(p.get("rebirth",0),MAX_REBIRTH),1.0)
    base*=PRESTIGE_BONUS_TABLE.get(min(p.get("prestige",0),MAX_PRESTIGE),1.0)
    asc=p.get("ascension",0)
    if asc>=1:
        ab=ASCENSION_BONUSES.get(max(k for k in ASCENSION_BONUSES if k<=asc),(None,1,1))
        base*=ab[1]
    base*=_upgrades_mult(p,"mult_bonus")
    if time.time()<p.get("event_end",0): base*=p.get("event_tap_mult",1.0)
    return base

def calc_auto_per_sec(p: dict) -> float:
    t=_upgrades_sum(p,"auto_bonus")
    if time.time()<p.get("event_end",0): t*=p.get("event_auto_mult",1.0)
    return t

def calc_passive_per_sec(p: dict) -> float:
    t=_upgrades_sum(p,"passive_bonus")
    if time.time()<p.get("event_end",0): t*=p.get("event_passive_mult",1.0)
    return t

def calc_total_income(p: dict) -> float:
    return calc_auto_per_sec(p)+calc_passive_per_sec(p)

def calc_crit_chance(p: dict) -> float:
    if time.time()<p.get("event_end",0) and p.get("event_crit_override",-1)>=0: return 1.0
    return min(_upgrades_sum(p,"crit_bonus"), 0.95)

def calc_crit_mult(p: dict) -> float:
    t=2.0+_upgrades_sum(p,"crit_mult")
    if p.get("ascension",0)>=20: t*=999
    return t

def calc_combo_mult(p: dict) -> float:
    cb=_upgrades_sum(p,"combo_bonus")
    c=p.get("combo",0)
    return 1.0 if c<=1 else 1.0+cb*(c-1)

def _offline_mult(p: dict) -> float:
    b=1.0
    for k,lv in p.get("upgrades",{}).items():
        if lv<=0: continue
        u=UPGRADES.get(k)
        if u and "offline_mult" in u: b*=u["offline_mult"]**lv
    if time.time()<p.get("event_end",0): b*=p.get("event_offline_mult",1.0)
    return b

def apply_offline_income(p: dict) -> int:
    now=time.time()
    delta=min(now-p.get("last_seen",now),86400)
    income=int(calc_total_income(p)*_offline_mult(p)*delta)
    if income>0: p["coins"]+=income; p["earned"]+=income
    p["last_seen"]=now
    return income

def upgrade_cost(key: str, p: dict) -> int:
    u=UPGRADES.get(key)
    if not u: return 0
    lv=p.get("upgrades",{}).get(key,0)
    try: c=min(int(u["cost"]*(1.15**lv)),_MAX_COST)
    except OverflowError: c=_MAX_COST
    if p.get("ascension",0)>=3: c=int(c*0.3)
    return c

def fmt(n: float|int) -> str:
    n=float(n)
    if n<0: return f"-{fmt(-n)}"
    tiers=[(1e18,"Qi"),(1e15,"Q"),(1e12,"T"),(1e9,"B"),(1e6,"M"),(1e3,"K")]
    for t,s in tiers:
        if n>=t: return f"{n/t:.2f}{s}"
    return str(int(n))

def _rebirth_cost(rb): return int(10000*(4**rb))
def _prestige_cost(pr): return 5+pr*2
def _ascension_cost(asc): return 20+asc*5

def _ensure_quests(p: dict):
    now=time.time()
    if not p.get("daily_quest"):
        p["daily_quest"]=random.choice(DAILY_QUESTS).copy()
        p["daily_progress"]={}; p["quest_reset_daily"]=now+86400
    if not p.get("hourly_quest"):
        p["hourly_quest"]=random.choice(HOURLY_QUESTS).copy()
        p["hourly_progress"]={}; p["quest_reset_hourly"]=now+3600
    if p.get("prestige",0)>=30 and not p.get("monthly_quest"):
        p["monthly_quest"]=random.choice(MONTHLY_QUESTS).copy()
        p["monthly_progress"]={}; p["quest_reset_monthly"]=now+2592000

async def _update_quest(uid: int, p: dict, prog_type: str, amount: int=1):
    now=time.time()
    if p.get("daily_quest") and now>p.get("quest_reset_daily",0): p["daily_quest"]=None; p["daily_progress"]={}
    if p.get("hourly_quest") and now>p.get("quest_reset_hourly",0): p["hourly_quest"]=None; p["hourly_progress"]={}
    if p.get("monthly_quest") and now>p.get("quest_reset_monthly",0): p["monthly_quest"]=None; p["monthly_progress"]={}
    def _chk(qk,pk):
        q=p.get(qk)
        if not q or q["type"]!=prog_type: return
        pr=p.get(pk,{}); pr[prog_type]=pr.get(prog_type,0)+amount; p[pk]=pr
        if pr[prog_type]>=q["target"]:
            p["coins"]+=q["reward"]; p["earned"]+=q["reward"]; p[qk]=None
            asyncio.ensure_future(_notify_quest(uid,q["name"],q["reward"]))
    _chk("daily_quest","daily_progress")
    _chk("hourly_quest","hourly_progress")
    _chk("monthly_quest","monthly_progress")

async def _notify_quest(uid: int, name: str, reward: int):
    try: await APP.bot.send_message(uid,f"📋 *КВЕСТ ВЫПОЛНЕН!*\n*{name}*\n💰 +{fmt(reward)} монет!",parse_mode="Markdown")
    except: pass

async def check_achievements(uid: int, p: dict):
    for ach_id,ach in ACHIEVEMENTS.items():
        if ach_id in p.get("achievements",[]): continue
        try:
            if ach["cond"](p):
                p["achievements"].append(ach_id)
                p["coins"]+=ach["reward"]; p["earned"]+=ach["reward"]
                try: await APP.bot.send_message(uid,f"🏆 *ДОСТИЖЕНИЕ!*\n*{ach['name']}*\n💰 +{fmt(ach['reward'])} монет!",parse_mode="Markdown")
                except: pass
        except: pass

# ─── TELEGRAM INIT DATA VALIDATION ──────────────────────────────────────────
def validate_init_data(init_data_str: str) -> dict|None:
    """Проверяет подлинность данных от Telegram WebApp."""
    try:
        vals = dict(parse_qsl(init_data_str, keep_blank_values=True))
        recv_hash = vals.pop("hash", "")
        # Ключ для HMAC
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        data_check = "\n".join(f"{k}={v}" for k,v in sorted(vals.items()))
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, recv_hash):
            return None
        user = json.loads(unquote(vals.get("user", "{}")))
        return user
    except Exception as e:
        logger.warning(f"init_data validation error: {e}")
        return None

# ─── FASTAPI APP ─────────────────────────────────────────────────────────────
api = FastAPI(title="Cosmic Tapper API")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
from fastapi.staticfiles import StaticFiles
api.mount("/", StaticFiles(directory=_BASE_DIR, html=True), name="static")

async def _get_uid(request: Request) -> tuple[int, str]:
    """Извлекает uid из initData или тестового заголовка."""
    body = await request.json()
    init_data = body.get("initData", "")
    # В продакшене — только через validate_init_data
    user = validate_init_data(init_data)
    if not user:
        # Для локального теста без WebApp — принимаем uid напрямую (ТОЛЬКО DEV!)
        test_uid = body.get("_test_uid")
        if test_uid and os.getenv("DEV_MODE","0")=="1":
            return int(test_uid), body.get("_test_name","Tester")
        raise HTTPException(403, "Invalid initData")
    return user["id"], user.get("username") or user.get("first_name","")

@api.post("/api/profile")
async def api_profile(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        offline = apply_offline_income(p)
        _ensure_quests(p)
        await save_player(uid, p, uname)
    return {
        "coins": p["coins"], "earned": p["earned"],
        "rebirth": p["rebirth"], "prestige": p["prestige"], "ascension": p["ascension"],
        "taps": p["taps"], "total_crits": p.get("total_crits",0),
        "bosses_killed": p.get("bosses_killed",0),
        "combo": p.get("combo",0),
        "tap_power": calc_tap_power(p), "income_per_sec": calc_total_income(p),
        "crit_chance": calc_crit_chance(p), "crit_mult": calc_crit_mult(p),
        "achievements": p.get("achievements",[]),
        "achievements_total": len(ACHIEVEMENTS),
        "clan_id": p.get("clan_id"),
        "boss_key": p.get("boss_key"), "boss_hp": p.get("boss_hp",0),
        "event_active": time.time()<p.get("event_end",0),
        "event_end": p.get("event_end",0),
        "event_tap_mult": p.get("event_tap_mult",1),
        "offline_earned": offline,
        "rebirth_cost": _rebirth_cost(p["rebirth"]),
        "prestige_cost": _prestige_cost(p["prestige"]),
        "ascension_cost": _ascension_cost(p["ascension"]),
        "max_rebirth": MAX_REBIRTH, "max_prestige": MAX_PRESTIGE, "max_ascension": MAX_ASCENSION,
    }

@api.post("/api/tap")
async def api_tap(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        now = time.time()
        if now - p["last_tap"] < TAP_COOLDOWN:
            return {"ok":False, "msg":"Слишком быстро!"}
        # Комбо
        freeze = p.get("event_combo_freeze") and now<p.get("event_end",0)
        if now-p["last_tap"]<2.0 or freeze: p["combo"]=p.get("combo",0)+1
        else: p["combo"]=1
        gain = calc_tap_power(p) * calc_combo_mult(p)
        # Крит
        is_crit = random.random()<calc_crit_chance(p)
        if is_crit:
            gain*=calc_crit_mult(p)
            p["crit_streak"]=p.get("crit_streak",0)+1
            p["total_crits"]=p.get("total_crits",0)+1
        else: p["crit_streak"]=0
        gain=int(gain)
        p["coins"]+=gain; p["earned"]+=gain
        p["taps"]+=1; p["last_tap"]=now
        await _update_quest(uid,p,"taps")
        await _update_quest(uid,p,"earn",gain)
        if is_crit: await _update_quest(uid,p,"crits")
        await check_achievements(uid,p)
        await save_player(uid,p,uname)
    return {
        "ok":True,"gain":gain,"is_crit":is_crit,
        "combo":p["combo"],"coins":p["coins"],
        "tap_power":calc_tap_power(p)
    }

@api.post("/api/upgrades")
async def api_upgrades(request: Request):
    uid, _ = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
    result = []
    for branch_id, (emoji, name, prefix, req) in BRANCH_INFO.items():
        unlocked = p.get("prestige",0)>=req
        keys = [k for k,v in UPGRADES.items() if v["branch"]==branch_id]
        items = []
        for k in keys[:20]:  # первые 20 для превью
            u=UPGRADES[k]; lv=p.get("upgrades",{}).get(k,0)
            c=upgrade_cost(k,p) if unlocked else u["cost"]
            items.append({"key":k,"name":u["name"],"emoji":u["emoji"],"level":lv,
                          "cost":c,"can_buy":p["coins"]>=c and unlocked})
        result.append({"branch":branch_id,"emoji":emoji,"name":name,
                       "unlocked":unlocked,"prestige_req":req,"items":items})
    return result

@api.post("/api/buy_upgrade")
async def api_buy_upgrade(request: Request):
    uid, uname = await _get_uid(request)
    body = await request.json()
    key = body.get("key","")
    async with get_lock(uid):
        p = await load_player(uid)
        u = UPGRADES.get(key)
        if not u: raise HTTPException(400,"Unknown upgrade")
        if p.get("prestige",0)<u.get("prestige_req",0): return {"ok":False,"msg":"Нет нужного престижа"}
        c=upgrade_cost(key,p)
        if p["coins"]<c: return {"ok":False,"msg":f"Нужно {fmt(c)} монет"}
        p["coins"]-=c; p["upgrades"][key]=p.get("upgrades",{}).get(key,0)+1
        await _update_quest(uid,p,"upgrade")
        await check_achievements(uid,p)
        await save_player(uid,p,uname)
    return {"ok":True,"key":key,"level":p["upgrades"][key],"coins":p["coins"]}

@api.post("/api/boss_attack")
async def api_boss_attack(request: Request):
    uid, uname = await _get_uid(request)
    body = await request.json()
    boss_key = body.get("boss_key","")
    async with get_lock(uid):
        p = await load_player(uid)
        boss = BOSSES.get(boss_key)
        if not boss: raise HTTPException(400,"Unknown boss")
        if p.get("prestige",0)<boss.get("prestige_req",0): return {"ok":False,"msg":"Нет нужного престижа"}
        if p.get("boss_key")!=boss_key: p["boss_key"]=boss_key; p["boss_hp"]=boss["hp"]
        damage=int(calc_tap_power(p)); p["boss_hp"]-=damage
        killed=False
        if p["boss_hp"]<=0:
            killed=True
            p["coins"]+=boss["reward"]; p["earned"]+=boss["reward"]
            p["boss_key"]=None; p["bosses_killed"]=p.get("bosses_killed",0)+1
            await _update_quest(uid,p,"boss_kill")
            await check_achievements(uid,p)
        await save_player(uid,p,uname)
    return {"ok":True,"killed":killed,"damage":damage,
            "boss_hp":max(0,p["boss_hp"]),"boss_max_hp":boss["hp"],
            "reward":boss["reward"] if killed else 0,"coins":p["coins"]}

@api.post("/api/rebirth")
async def api_rebirth(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        rb=p.get("rebirth",0)
        if rb>=MAX_REBIRTH: return {"ok":False,"msg":"Максимум ребиртов!"}
        need=_rebirth_cost(rb)
        if p["coins"]<need: return {"ok":False,"msg":f"Нужно {fmt(need)} монет"}
        p["coins"]=0; p["upgrades"]={}; p["combo"]=0; p["rebirth"]=rb+1
        await _update_quest(uid,p,"rebirths"); await check_achievements(uid,p)
        await save_player(uid,p,uname)
    return {"ok":True,"rebirth":p["rebirth"],"bonus":REBIRTH_BONUS_TABLE.get(p["rebirth"],1.0)}

@api.post("/api/prestige")
async def api_prestige(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        pr=p.get("prestige",0)
        if pr>=MAX_PRESTIGE: return {"ok":False,"msg":"Максимум престижей!"}
        need_rb=_prestige_cost(pr)
        if p.get("rebirth",0)<need_rb: return {"ok":False,"msg":f"Нужно {need_rb} ребиртов"}
        p["coins"]=0; p["upgrades"]={}; p["rebirth"]=0; p["combo"]=0; p["prestige"]=pr+1
        unlock=PRESTIGE_UNLOCKS.get(p["prestige"],"")
        await check_achievements(uid,p); await save_player(uid,p,uname)
    return {"ok":True,"prestige":p["prestige"],"unlock":unlock}

@api.post("/api/ascension")
async def api_ascension(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        asc=p.get("ascension",0)
        if asc>=MAX_ASCENSION: return {"ok":False,"msg":"Максимум вознесений!"}
        if p.get("prestige",0)<20: return {"ok":False,"msg":"Нужен Престиж 20"}
        need_pr=_ascension_cost(asc)
        if p.get("prestige",0)<need_pr: return {"ok":False,"msg":f"Нужно {need_pr} престижей"}
        p["coins"]=0; p["upgrades"]={}; p["rebirth"]=0; p["prestige"]=0; p["combo"]=0; p["ascension"]=asc+1
        bonus=ASCENSION_BONUSES.get(p["ascension"],("Бонус",1,1))[0]
        await check_achievements(uid,p); await save_player(uid,p,uname)
    return {"ok":True,"ascension":p["ascension"],"bonus":bonus}

@api.post("/api/quests")
async def api_quests(request: Request):
    uid, _ = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
        _ensure_quests(p)
    now=time.time()
    def qinfo(qk,pk,rk):
        q=p.get(qk); return None if not q else {
            "name":q["name"],"type":q["type"],"emoji":q["emoji"],
            "target":q["target"],"reward":q["reward"],
            "progress":p.get(pk,{}).get(q["type"],0),
            "reset_in":max(0,p.get(rk,0)-now)
        }
    return {
        "daily": qinfo("daily_quest","daily_progress","quest_reset_daily"),
        "hourly": qinfo("hourly_quest","hourly_progress","quest_reset_hourly"),
        "monthly": qinfo("monthly_quest","monthly_progress","quest_reset_monthly") if p.get("prestige",0)>=30 else None,
        "monthly_locked": p.get("prestige",0)<30,
    }

@api.post("/api/leaderboard")
async def api_leaderboard(request: Request):
    async with DB.execute("SELECT username,data FROM players") as cur:
        rows=await cur.fetchall()
    def key(r):
        try: d=json.loads(r[1]); return (d.get("ascension",0),d.get("prestige",0),d.get("rebirth",0),d.get("earned",0))
        except: return (0,0,0,0)
    rows.sort(key=key,reverse=True)
    result=[]
    for i,(un,raw) in enumerate(rows[:20],1):
        d=json.loads(raw)
        result.append({"rank":i,"name":un or f"Игрок{i}",
                       "ascension":d.get("ascension",0),"prestige":d.get("prestige",0),
                       "rebirth":d.get("rebirth",0),"earned":d.get("earned",0)})
    return result

@api.post("/api/clans")
async def api_clans(request: Request):
    uid, _ = await _get_uid(request)
    async with get_lock(uid):
        p = await load_player(uid)
    async with DB.execute("SELECT clan_id,name,members FROM clans ORDER BY clan_id LIMIT 20") as cur:
        rows=await cur.fetchall()
    return {
        "my_clan_id": p.get("clan_id"),
        "clans":[{"id":r[0],"name":r[1],"members":len(json.loads(r[2]))} for r in rows]
    }

@api.post("/api/clan_join")
async def api_clan_join(request: Request):
    uid, uname = await _get_uid(request)
    body = await request.json()
    clan_id=body.get("clan_id")
    async with get_lock(uid):
        p=await load_player(uid)
        if p.get("clan_id"): return {"ok":False,"msg":"Вы уже в клане"}
        async with DB.execute("SELECT members FROM clans WHERE clan_id=?",(clan_id,)) as cur:
            row=await cur.fetchone()
        if not row: return {"ok":False,"msg":"Клан не найден"}
        mems=json.loads(row[0])
        if uid not in mems: mems.append(uid)
        await DB.execute("UPDATE clans SET members=? WHERE clan_id=?",(json.dumps(mems),clan_id))
        await DB.commit(); p["clan_id"]=clan_id
        await check_achievements(uid,p); await save_player(uid,p,uname)
    return {"ok":True}

@api.post("/api/clan_leave")
async def api_clan_leave(request: Request):
    uid, uname = await _get_uid(request)
    async with get_lock(uid):
        p=await load_player(uid)
        cid=p.get("clan_id")
        if cid:
            async with DB.execute("SELECT members FROM clans WHERE clan_id=?",(cid,)) as cur:
                row=await cur.fetchone()
            if row:
                mems=[m for m in json.loads(row[0]) if m!=uid]
                await DB.execute("UPDATE clans SET members=? WHERE clan_id=?",(json.dumps(mems),cid))
                await DB.commit()
        p["clan_id"]=None; await save_player(uid,p,uname)
    return {"ok":True}

@api.post("/api/clan_create")
async def api_clan_create(request: Request):
    uid, uname = await _get_uid(request)
    body = await request.json()
    name=(body.get("name","")).strip()[:32]
    if not name: raise HTTPException(400,"Нужно имя")
    async with get_lock(uid):
        p=await load_player(uid)
        if p.get("clan_id"): return {"ok":False,"msg":"Сначала покиньте текущий клан"}
        try:
            await DB.execute("INSERT INTO clans(name,owner_id,members,created_at) VALUES(?,?,?,?)",
                             (name,uid,json.dumps([uid]),time.time()))
            await DB.commit()
            async with DB.execute("SELECT last_insert_rowid()") as cur:
                new_id=(await cur.fetchone())[0]
            p["clan_id"]=new_id
            await check_achievements(uid,p); await save_player(uid,p,uname)
        except Exception: return {"ok":False,"msg":"Имя занято"}
    return {"ok":True,"clan_id":new_id}

@api.get("/api/bosses_list")
async def api_bosses_list():
    return [{"key":k,"name":v["name"],"hp":v["hp"],"reward":v["reward"],
             "emoji":v["emoji"],"prestige_req":v["prestige_req"]} for k,v in BOSSES.items()]

# ─── ФОНОВЫЕ ЗАДАЧИ ──────────────────────────────────────────────────────────
async def _passive_loop():
    while True:
        await asyncio.sleep(5)
        try:
            async with DB.execute("SELECT user_id,data FROM players") as cur:
                rows=await cur.fetchall()
            upd=[]
            for uid,raw in rows:
                p=json.loads(raw); gain=int(calc_total_income(p)*5)
                if gain>0: p["coins"]+=gain; p["earned"]+=gain; upd.append((json.dumps(p),uid))
            if upd: await DB.executemany("UPDATE players SET data=? WHERE user_id=?",upd); await DB.commit()
        except Exception as e: logger.error(f"[passive_loop] {e}")

async def _event_loop(app: Application):
    while True:
        await asyncio.sleep(600)
        try:
            async with DB.execute("SELECT user_id,data FROM players") as cur:
                rows=await cur.fetchall()
            upd=[]
            for uid,raw in rows:
                p=json.loads(raw)
                if time.time()-p.get("last_seen",0)>86400: continue
                if random.random()<0.08:
                    ev=random.choice(EVENTS); end=time.time()+ev["duration"]
                    p.update({"event_end":end,"event_tap_mult":ev.get("tap_mult",1.0),
                               "event_auto_mult":ev.get("auto_mult",1.0),
                               "event_passive_mult":ev.get("passive_mult",1.0),
                               "event_offline_mult":ev.get("offline_mult",1.0),
                               "event_crit_override":ev.get("crit_override",-1.0),
                               "event_combo_freeze":ev.get("combo_freeze",False)})
                    upd.append((json.dumps(p),uid))
                    try: await app.bot.send_message(uid,f"🎉 *СОБЫТИЕ!*\n{ev['emoji']} *{ev['name']}*\n{ev['desc']}\n⏱ {ev['duration']//60} мин!",parse_mode="Markdown")
                    except: pass
            if upd: await DB.executemany("UPDATE players SET data=? WHERE user_id=?",upd); await DB.commit()
        except Exception as e: logger.error(f"[event_loop] {e}")

# ─── TELEGRAM BOT (только /start с кнопкой WebApp) ───────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; uname=update.effective_user.username or update.effective_user.first_name
    async with get_lock(uid):
        p=await load_player(uid)
        offline=apply_offline_income(p)
        _ensure_quests(p); p["session_start"]=time.time()
        await save_player(uid,p,uname)
    msg=""
    if offline>0: msg=f"⏰ Пока вас не было, вы заработали *{fmt(offline)} монет*!\n\n"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton(
        "🚀 Открыть Cosmic Tapper",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )]])
    await update.message.reply_text(
        f"{msg}🌌 *COSMIC TAPPER v4 ULTIMATE*\n\n"
        f"💰 Монет: {fmt(p['coins'])} | 👆 Мощь: {fmt(calc_tap_power(p))}\n"
        f"🔄 Ребирт: {p['rebirth']} | ⭐ Престиж: {p['prestige']} | 🌌 Вознесение: {p['ascension']}\n\n"
        f"Нажмите кнопку, чтобы запустить игру!",
        reply_markup=kb, parse_mode="Markdown"
    )

async def post_init(app: Application):
    global APP; APP=app
    await app.bot.delete_webhook(drop_pending_updates=True)
    await init_db()
    asyncio.create_task(_passive_loop())
    asyncio.create_task(_event_loop(app))
    logger.info("✅ Cosmic Tapper Backend запущен!")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err=context.error
    if isinstance(err, Conflict): logger.error("Telegram Conflict: другой процесс!"); return
    logger.exception("Ошибка", exc_info=err)

# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN=="СЮДА_ВАШ_ТОКЕН":
        raise RuntimeError("Укажите BOT_TOKEN в переменной окружения или в коде!")
    acquire_lock()

    tg_app = (Application.builder().token(BOT_TOKEN)
              .request(HTTPXRequest(connect_timeout=30,read_timeout=30,write_timeout=30,pool_timeout=30))
              .post_init(post_init).build())
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("menu",  cmd_start))
    tg_app.add_error_handler(error_handler)

    async def _run():
        # Запуск Telegram polling в фоне
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        # Запуск FastAPI
        config = uvicorn.Config(api, host="0.0.0.0", port=API_PORT, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

    asyncio.run(_run())

if __name__ == "__main__":
    main()
