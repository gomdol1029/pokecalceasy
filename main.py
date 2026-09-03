# -*- coding: utf-8 -*-
"""
포켓몬 결정력 · 내구력 · 데미지 계산기
====================================
Streamlit + PokeAPI 기반으로 동작하는 포켓몬 실전 계산기입니다.
포켓몬/기술/타입/특성/성격 데이터는 하드코딩하지 않고 전부 PokeAPI(https://pokeapi.co)에서
가져옵니다. 단, 능력치 계산식·데미지 공식·타수 판정 로직 자체는 게임 내부 규칙을
코드로 옮긴 것이며, 이 부분은 PokeAPI가 제공하지 않으므로 아래에 근거와 함께 구현했습니다.

파일 구성
---------
- main.py           : 이 파일 하나로 앱 전체가 동작합니다.
- requirements.txt  : 배포에 필요한 패키지 목록.

실행 방법
---------
    streamlit run main.py

주의(정확성 관련) - 반드시 읽어주세요
--------------------------------------
1) 특성/도구/날씨로 인한 위력·랭크 보정 효과는 포켓몬마다(그리고 특성마다) 규칙이 전혀 다르고
   그 종류가 수백 가지가 넘습니다. PokeAPI는 "이 특성이 데미지 계산에 정확히 어떤 배율을
   적용하는지"를 기계가 읽을 수 있는 형태로 제공하지 않습니다(효과 설명은 사람이 읽는 텍스트뿐).
   따라서 이 앱은 특성/도구 자동 데미지 배율 계산을 "추측"하지 않고, 사용자가 직접 배율을
   입력하는 수동 보정 필드로 분리했습니다. (요구사항 18~20의 "불확실한 시스템은 추측하지
   말고 분리하라"는 지시를 따른 것입니다.)
2) 날씨의 화상/불꽃/물 데미지 배율(맑음·비)은 게임 공식 룰이 명확하므로 자동 계산에 포함했고,
   모래바람/눈으로 인한 특정 타입의 방어 랭크 보정은 공식이 명확한 범위에서만 반영했습니다.
3) 데미지 공식에서 "보정치들을 곱한 뒤 언제 floor 하는가"는 실제 게임에서는 매우 세분화되어
   있습니다(랭크효과 순서, 특성 순서, 아이템 순서 등). 이 앱은 통상적인 팬 계산기들이 쓰는
   방식대로 "랜덤 배율을 제외한 모든 배율을 먼저 하나로 합쳐 floor 한 뒤, 85~100% 난수를
   곱해 다시 floor" 하는 2단계 방식을 사용합니다. 실제 게임과 극히 일부 상황(복수 보정이
   동시에 걸릴 때 등)에서 1 데미지 정도 차이가 날 수 있습니다.
4) 능력치 계산은 3세대 이후 공식 게임에서 쓰이는 일반식
       HP = floor((2*종족값 + IV + floor(EV/4)) * Lv / 100) + Lv + 10
       기타 = floor((floor((2*종족값 + IV + floor(EV/4)) * Lv / 100) + 5) * 성격보정)
   을 그대로 사용합니다. 레벨 50일 때 이 식은 커뮤니티에서 자주 쓰이는 간이식과 사실상
   동일한 결과를 주며, 모든 레벨에서 정확하다는 장점이 있어 이 식을 채택했습니다.
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import streamlit as st

# ----------------------------------------------------------------------------
# 기본 설정
# ----------------------------------------------------------------------------

POKEAPI = "https://pokeapi.co/api/v2"
REQUEST_TIMEOUT = 8

STAT_KEY_KO = {
    "hp": "HP",
    "attack": "공격",
    "defense": "방어",
    "special-attack": "특수공격",
    "special-defense": "특수방어",
    "speed": "스피드",
}
STAT_ORDER = ["hp", "attack", "defense", "special-attack", "special-defense", "speed"]

st.set_page_config(
    page_title="포켓몬 결정력 · 내구력 계산기",
    page_icon="⚔️",
    layout="wide",
)

# ----------------------------------------------------------------------------
# 1. PokeAPI 통신 레이어 (모두 캐시 처리)
# ----------------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def api_get(url: str):
    """PokeAPI에 GET 요청을 보내고 JSON을 반환한다. 실패하면 None을 반환한다.
    st.cache_data로 캐싱하여 동일 URL을 반복 요청하지 않는다."""
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return None
        return res.json()
    except requests.exceptions.RequestException:
        return None


def ko_name_from(names, fallback):
    """PokeAPI의 'names' 배열에서 한국어(ko) 이름을 찾는다. 없으면 fallback(영문명)을 사용한다."""
    if not names:
        return fallback
    for n in names:
        if n.get("language", {}).get("name") == "ko":
            return n.get("name")
    return fallback


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_species_list():
    """전체 포켓몬 종(species) 목록(영문명, url)을 가져온다. (단 1회 요청)"""
    data = api_get(f"{POKEAPI}/pokemon-species?limit=2000&offset=0")
    if not data:
        return []
    return data.get("results", [])


@st.cache_resource(show_spinner=False)
def build_korean_index():
    """모든 포켓몬 종의 한국어 이름 인덱스를 구축한다.
    PokeAPI는 이름을 한국어로 검색하는 기능을 제공하지 않으므로,
    검색을 지원하려면 전체 목록의 한국어 이름을 미리 받아와야 한다.
    앱이 실행되는 동안(서버 프로세스 단위) 단 한 번만 수행되도록 st.cache_resource를 사용한다."""
    species_list = get_species_list()
    index = {}  # en_name -> ko_name

    def fetch_one(item):
        data = api_get(item["url"])
        if not data:
            return item["name"], item["name"]
        return item["name"], ko_name_from(data.get("names", []), item["name"])

    if not species_list:
        return index

    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(fetch_one, item) for item in species_list]
        for fut in as_completed(futures):
            en, ko = fut.result()
            index[en] = ko
    return index


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_pokemon_full(name_en: str):
    """포켓몬 1마리의 전체 데이터(도감/종족값/타입/특성/스프라이트)를 가져온다."""
    pdata = api_get(f"{POKEAPI}/pokemon/{name_en}")
    if not pdata:
        return None
    sdata = api_get(f"{POKEAPI}/pokemon-species/{name_en}")

    base_stats = {s["stat"]["name"]: s["base_stat"] for s in pdata["stats"]}
    types_en = [t["type"]["name"] for t in sorted(pdata["types"], key=lambda x: x["slot"])]

    abilities = []
    for a in pdata["abilities"]:
        a_data = api_get(a["ability"]["url"])
        ko = ko_name_from(a_data.get("names", []), a["ability"]["name"]) if a_data else a["ability"]["name"]
        abilities.append({"name_en": a["ability"]["name"], "name_ko": ko, "is_hidden": a["is_hidden"]})

    ko_name = ko_name_from(sdata.get("names", []), name_en) if sdata else name_en

    sprite = None
    try:
        sprite = pdata["sprites"]["other"]["official-artwork"]["front_default"] or pdata["sprites"]["front_default"]
    except Exception:
        pass

    moves_en = sorted({m["move"]["name"] for m in pdata["moves"]})

    return {
        "id": pdata["id"],
        "name_en": name_en,
        "name_ko": ko_name,
        "types_en": types_en,
        "base_stats": base_stats,
        "abilities": abilities,
        "sprite": sprite,
        "moves_en": moves_en,
    }


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_move_full(name_en: str):
    """기술 1개의 상세 정보를 가져온다."""
    data = api_get(f"{POKEAPI}/move/{name_en}")
    if not data:
        return None
    return {
        "name_en": name_en,
        "name_ko": ko_name_from(data.get("names", []), name_en),
        "power": data.get("power"),  # None이면 변화기 등 위력 없는 기술
        "type_en": data["type"]["name"],
        "damage_class": data["damage_class"]["name"],  # physical / special / status
        "pp": data.get("pp"),
        "accuracy": data.get("accuracy"),
    }


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_moves_bulk(move_names):
    """여러 기술을 동시에 조회한다 (기술 목록 표시용)."""
    result = {}

    def fetch_one(name):
        return name, get_move_full(name)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(fetch_one, n) for n in move_names]
        for fut in as_completed(futures):
            name, data = fut.result()
            if data:
                result[name] = data
    return result


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_type_data(type_name_en: str):
    """타입 1개의 한국어 이름과 상성 관계(damage_relations)를 가져온다."""
    data = api_get(f"{POKEAPI}/type/{type_name_en}")
    if not data:
        return None
    return {
        "name_en": type_name_en,
        "name_ko": ko_name_from(data.get("names", []), type_name_en),
        "relations": data["damage_relations"],
    }


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_all_types():
    """18개 타입 전체 정보를 미리 받아온다 (타입 상성 계산에 필요)."""
    names = [
        "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison",
        "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark",
        "steel", "fairy",
    ]
    result = {}

    def fetch_one(n):
        return n, get_type_data(n)

    with ThreadPoolExecutor(max_workers=18) as executor:
        futures = [executor.submit(fetch_one, n) for n in names]
        for fut in as_completed(futures):
            n, d = fut.result()
            if d:
                result[n] = d
    return result


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_all_natures():
    """25개 성격의 한국어 이름과 증가/감소 스탯을 가져온다."""
    names = [
        "hardy", "lonely", "brave", "adamant", "naughty",
        "bold", "docile", "relaxed", "impish", "lax",
        "timid", "hasty", "serious", "jolly", "naive",
        "modest", "mild", "quiet", "bashful", "rash",
        "calm", "gentle", "sassy", "careful", "quirky",
    ]
    result = {}

    def fetch_one(n):
        data = api_get(f"{POKEAPI}/nature/{n}")
        return n, data

    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(fetch_one, n) for n in names]
        for fut in as_completed(futures):
            n, data = fut.result()
            if not data:
                continue
            inc = data["increased_stat"]["name"] if data["increased_stat"] else None
            dec = data["decreased_stat"]["name"] if data["decreased_stat"] else None
            result[n] = {
                "name_ko": ko_name_from(data.get("names", []), n),
                "increased_stat": inc,
                "decreased_stat": dec,
                "neutral": inc is None,
            }
    return result


# ----------------------------------------------------------------------------
# 2. 능력치 계산 (2번 요구사항 "능력치 계산")
# ----------------------------------------------------------------------------


def nature_multiplier(nature_info: dict, stat_key: str) -> float:
    """성격에 따른 스탯 배율(1.1 / 0.9 / 1.0)을 반환한다. HP에는 성격 보정이 없다."""
    if stat_key == "hp" or nature_info is None:
        return 1.0
    if nature_info.get("neutral"):
        return 1.0
    if nature_info.get("increased_stat") == stat_key:
        return 1.1
    if nature_info.get("decreased_stat") == stat_key:
        return 0.9
    return 1.0


def calc_stat(base: int, iv: int, ev: int, level: int, stat_key: str, nature_info: dict) -> int:
    """공식 게임(3세대 이후) 능력치 계산식.
    HP:  floor((2*base + iv + floor(ev/4)) * level / 100) + level + 10
    기타: floor( (floor((2*base + iv + floor(ev/4)) * level / 100) + 5) * 성격보정 )
    """
    inner = math.floor((2 * base + iv + math.floor(ev / 4)) * level / 100)
    if stat_key == "hp":
        # 1PP 기술만 있는 케쿤시(Shedinja, base HP=1)는 항상 HP=1 이라는 예외가 있으나
        # 여기서는 일반적인 경우만 다룬다 (요구사항 범위 밖이므로 별도 처리하지 않음).
        return inner + level + 10
    mult = nature_multiplier(nature_info, stat_key)
    return math.floor((inner + 5) * mult)


def stage_multiplier_value(stat: int, stage: int) -> int:
    """능력치 랭크(-6~+6)를 실제 스탯에 적용한다. 게임 내부는 정수 나눗셈을 사용한다."""
    stage = max(-6, min(6, stage))
    if stage >= 0:
        return (stat * (2 + stage)) // 2
    else:
        return (stat * 2) // (2 - stage)


# ----------------------------------------------------------------------------
# 3. 타입 상성 / STAB (7, 12, 13번 요구사항)
# ----------------------------------------------------------------------------


def type_effectiveness(attack_type_en: str, defender_types_en: list, all_types: dict) -> float:
    """공격 타입이 방어 포켓몬의 (복합)타입에 대해 갖는 배율을 계산한다."""
    atk = all_types.get(attack_type_en)
    if not atk:
        return 1.0
    rel = atk["relations"]
    double = {t["name"] for t in rel["double_damage_to"]}
    half = {t["name"] for t in rel["half_damage_to"]}
    zero = {t["name"] for t in rel["no_damage_to"]}

    mult = 1.0
    for dt in defender_types_en:
        if dt in zero:
            mult *= 0.0
        elif dt in double:
            mult *= 2.0
        elif dt in half:
            mult *= 0.5
        else:
            mult *= 1.0
    return mult


def is_stab(attacker_types_en: list, move_type_en: str) -> bool:
    return move_type_en in attacker_types_en


# ----------------------------------------------------------------------------
# 4. 날씨 보정 (8번 하위 요구사항 - 규칙이 명확한 부분만 자동 계산)
# ----------------------------------------------------------------------------

WEATHER_OPTIONS = {
    "없음": None,
    "맑음 (한여름의 태양 등)": "sun",
    "비 (비바라기 등)": "rain",
    "모래바람": "sand",
    "눈": "snow",
}


def weather_damage_multiplier(weather: str, move_type_en: str) -> float:
    """맑음/비는 불꽃·물 기술 위력에 직접 배율이 붙는다(공식 규칙, 자동 계산 가능).
    모래바람/눈은 위력 자체가 아니라 방어 랭크에 영향을 주므로 여기서는 1.0을 반환하고,
    별도의 defense 보정 함수(weather_defense_multiplier)에서 처리한다."""
    if weather == "sun":
        if move_type_en == "fire":
            return 1.5
        if move_type_en == "water":
            return 0.5
    elif weather == "rain":
        if move_type_en == "water":
            return 1.5
        if move_type_en == "fire":
            return 0.5
    return 1.0


def weather_defense_multiplier(weather: str, defender_types_en: list, damage_class: str) -> float:
    """모래바람: 바위 타입의 특수방어 1.5배 / 눈: 얼음 타입의 방어 1.5배 (공식 규칙)."""
    if weather == "sand" and damage_class == "special" and "rock" in defender_types_en:
        return 1.5
    if weather == "snow" and damage_class == "physical" and "ice" in defender_types_en:
        return 1.5
    return 1.0


# ----------------------------------------------------------------------------
# 5. 데미지 계산 (9번 요구사항)
# ----------------------------------------------------------------------------

RANDOM_ROLLS = list(range(85, 101))  # 85% ~ 100%, 16단계


def base_power_damage(level: int, power: int, attack_stat: int, defense_stat: int) -> int:
    """랜덤 보정 이전의 기본 데미지.
    floor( floor( (2*Level/5 + 2) * Power * Attack / Defense ) / 50 ) + 2
    """
    step1 = (2 * level / 5) + 2
    step2 = step1 * power * attack_stat / defense_stat
    step2 = math.floor(step2)
    step3 = math.floor(step2 / 50) + 2
    return step3


def calculate_damage_rolls(level: int, power: int, attack_stat: int, defense_stat: int,
                            combined_modifier: float) -> list:
    """85~100% 난수 16종에 대한 최종 데미지 리스트(정수)를 반환한다.
    combined_modifier 에는 STAB, 타입 상성, 날씨, 특성/도구/기타 수동 보정이 모두 곱해져 들어온다.
    실제 게임처럼: (기본데미지 * 기타보정들 을 먼저 floor) -> (그 값 * 난수 를 floor) 순서로 계산한다."""
    base = base_power_damage(level, power, attack_stat, defense_stat)
    modified = math.floor(base * combined_modifier)
    modified = max(modified, 1) if combined_modifier > 0 else 0  # 최소 데미지 보정(효과가 있다면 최소 1)
    rolls = []
    for r in RANDOM_ROLLS:
        dmg = math.floor(modified * r / 100)
        if combined_modifier > 0:
            dmg = max(dmg, 1)
        rolls.append(dmg)
    return rolls


# ----------------------------------------------------------------------------
# 6. 타수 판정 & 확률 계산 (10, 11번 요구사항)
# ----------------------------------------------------------------------------


def analyze_hits(damage_rolls: list, hp: int, max_hits_to_check: int = 9):
    """몇 타에 쓰러뜨릴 수 있는지, 그리고 각 타수별 확률을 계산한다.
    16가지 난수 값이 각각 1/16 확률로 발생한다고 가정하고,
    n번 공격했을 때의 데미지 합 분포를 컨볼루션으로 직접 계산한다."""
    n_rolls = len(damage_rolls)
    if max(damage_rolls) <= 0:
        return {"impossible": True}

    # sum_dist[n] = {합계값: 발생 경우의 수} , n회 공격했을 때
    dist = {d: 1 for d in damage_rolls}  # 1회 공격 분포
    results = []  # (n, ko_probability_percent)
    guaranteed_n = None
    first_possible_n = None

    for n in range(1, max_hits_to_check + 1):
        total_combos = n_rolls ** n
        ko_combos = sum(cnt for total, cnt in dist.items() if total >= hp)
        prob = ko_combos / total_combos * 100
        results.append((n, prob))
        if prob > 0 and first_possible_n is None:
            first_possible_n = n
        if prob >= 100 - 1e-9 and guaranteed_n is None:
            guaranteed_n = n
            break
        # 다음 타수를 위해 분포를 한 번 더 컨볼루션한다 (조합 폭발 방지를 위해 dict로 압축)
        if n < max_hits_to_check:
            new_dist = {}
            for total, cnt in dist.items():
                for d in damage_rolls:
                    key = total + d
                    new_dist[key] = new_dist.get(key, 0) + cnt
            dist = new_dist

    return {
        "impossible": False,
        "results": results,  # [(타수, 그 타수 이내 KO 확률%), ...]
        "guaranteed_n": guaranteed_n,
        "first_possible_n": first_possible_n,
    }


def format_hit_label(analysis: dict) -> str:
    if analysis.get("impossible"):
        return "데미지가 없어 쓰러뜨릴 수 없음"
    g = analysis["guaranteed_n"]
    f = analysis["first_possible_n"]
    if g == f:
        return f"확정 {g}타"
    if g is not None:
        return f"난수 {f}타 (확정은 {g}타)"
    return f"{f}타 이상 (9타 이내 확정 불가)"


# ----------------------------------------------------------------------------
# 7. 결정력 / 내구력 (6, 7번 요구사항 - 커뮤니티 용어이며 실제 데미지 공식과는 구분)
# ----------------------------------------------------------------------------


def calc_juldjeongryeok(attack_stat: int, power: int, stab: float, other_mult: float) -> float:
    """'결정력' = 공격 실능치 × 기술 위력 × STAB × 기타보정.
    이는 유저 커뮤니티에서 널리 쓰는 지표로, 실제 데미지 공식(방어 스탯/레벨 포함)과는
    다른 '한쪽 스탯만 반영한 근사 지표'임을 명확히 한다."""
    return attack_stat * power * stab * other_mult


def calc_naegurryeok(hp_stat: int, def_stat: int) -> float:
    """'내구력' = HP 실능치 × 방어(또는 특수방어) 실능치 ÷ 0.411 (커뮤니티 관용식)."""
    return hp_stat * def_stat / 0.411


# ----------------------------------------------------------------------------
# 8. Streamlit UI
# ----------------------------------------------------------------------------


def init_state():
    if "ver" not in st.session_state:
        # 위젯 key에 붙는 버전 번호. 교체/초기화 버튼을 누르면 값이 바뀌어
        # Streamlit이 입력 위젯들을 '새 위젯'으로 인식하고 session_state에 저장된
        # 데이터 값(아래 defaults/스왑된 값)을 그대로 초기 표시값으로 사용하게 만든다.
        st.session_state["ver"] = 0
    defaults = {
        "atk_name_en": None,
        "def_name_en": None,
        "atk_level": 50,
        "def_level": 50,
        "atk_iv": {k: 31 for k in STAT_ORDER},
        "atk_ev": {k: 0 for k in STAT_ORDER},
        "def_iv": {k: 31 for k in STAT_ORDER},
        "def_ev": {k: 0 for k in STAT_ORDER},
        "atk_nature": "adamant",
        "def_nature": "hardy",
        "atk_stage": 0,
        "def_stage": 0,
        "weather": "없음",
        "atk_ability_mult": 1.0,
        "atk_item_mult": 1.0,
        "atk_other_mult": 1.0,
        "def_ability_mult": 1.0,
        "def_item_mult": 1.0,
        "def_other_mult": 1.0,
        "move_name_en": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_state():
    keys = [
        "atk_name_en", "def_name_en", "atk_level", "def_level", "atk_iv", "atk_ev",
        "def_iv", "def_ev", "atk_nature", "def_nature", "atk_stage", "def_stage",
        "weather", "atk_ability_mult", "atk_item_mult", "atk_other_mult",
        "def_ability_mult", "def_item_mult", "def_other_mult", "move_name_en",
    ]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["ver"] = st.session_state.get("ver", 0) + 1
    init_state()


def swap_sides():
    pairs = [
        ("atk_name_en", "def_name_en"), ("atk_level", "def_level"),
        ("atk_iv", "def_iv"), ("atk_ev", "def_ev"),
        ("atk_nature", "def_nature"), ("atk_stage", "def_stage"),
        ("atk_ability_mult", "def_ability_mult"), ("atk_item_mult", "def_item_mult"),
        ("atk_other_mult", "def_other_mult"),
    ]
    for a, b in pairs:
        st.session_state[a], st.session_state[b] = st.session_state[b], st.session_state[a]
    st.session_state["ver"] = st.session_state.get("ver", 0) + 1


def pokemon_picker(label: str, state_key: str, ko_index: dict):
    """이름 일부만 입력해도 검색되는 포켓몬 선택 위젯."""
    ver = st.session_state["ver"]
    query = st.text_input(f"{label} 검색 (한글/영문 일부만 입력)", key=f"{state_key}_query_{ver}")
    matches = []
    if query:
        q = query.strip().lower()
        for en, ko in ko_index.items():
            if q in en.lower() or q in ko.lower():
                matches.append((en, ko))
        matches = sorted(matches, key=lambda x: (len(x[1]), x[1]))[:40]
    else:
        matches = []

    if matches:
        options = [f"{ko} ({en})" for en, ko in matches]
        idx = 0
        sel = st.selectbox(f"{label} 선택", options, index=idx, key=f"{state_key}_select_{ver}")
        chosen_en = matches[options.index(sel)][0]
        st.session_state[state_key] = chosen_en
    elif query:
        st.warning("검색 결과가 없습니다. 다른 이름으로 시도해보세요.")

    return st.session_state.get(state_key)


def stat_inputs(prefix: str, iv_key: str, ev_key: str):
    """IV/EV 입력 UI. EV 총합 510 제한을 검증한다."""
    ver = st.session_state["ver"]
    st.caption("개체값(IV) 0~31, 노력치(EV) 0~252, 총합 최대 510")
    cols = st.columns(3)
    for i, stat in enumerate(STAT_ORDER):
        with cols[i % 3]:
            st.session_state[iv_key][stat] = st.number_input(
                f"{STAT_KEY_KO[stat]} IV", 0, 31, st.session_state[iv_key][stat],
                key=f"{prefix}_iv_{stat}_{ver}",
            )
    cols2 = st.columns(3)
    for i, stat in enumerate(STAT_ORDER):
        with cols2[i % 3]:
            st.session_state[ev_key][stat] = st.number_input(
                f"{STAT_KEY_KO[stat]} EV", 0, 252, st.session_state[ev_key][stat],
                step=4, key=f"{prefix}_ev_{stat}_{ver}",
            )
    total_ev = sum(st.session_state[ev_key].values())
    if total_ev > 510:
        st.error(f"EV 총합이 {total_ev}로 510을 초과했습니다! 값을 조정해주세요.")
    else:
        st.caption(f"현재 EV 총합: {total_ev} / 510")
    return total_ev <= 510


def main():
    init_state()
    ver = st.session_state["ver"]

    st.title("포켓몬 결정력 · 내구력 계산기")
    st.write(
        "공격 포켓몬과 방어 포켓몬의 능력치와 기술을 설정하고 "
        "결정력, 내구력, 실제 데미지와 난수 범위를 계산합니다."
    )

    top_l, top_r = st.columns([1, 1])
    with top_l:
        if st.button("🔄 공격 ↔ 방어 교체"):
            swap_sides()
            st.rerun()
    with top_r:
        if st.button("♻️ 기본값으로 초기화"):
            reset_state()
            st.rerun()

    with st.spinner("포켓몬 도감 데이터베이스를 준비하는 중입니다 (최초 1회, 다소 시간이 걸릴 수 있어요)..."):
        ko_index = build_korean_index()
    if not ko_index:
        st.error("PokeAPI에서 포켓몬 목록을 가져오지 못했습니다. 네트워크 상태를 확인하고 새로고침해주세요.")
        return

    all_types = get_all_types()
    all_natures = get_all_natures()
    if not all_types or not all_natures:
        st.error("PokeAPI에서 타입/성격 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return

    nature_options = list(all_natures.keys())
    nature_labels = {n: all_natures[n]["name_ko"] for n in nature_options}

    col_atk, col_def = st.columns(2)

    # ---------------- 공격측 ----------------
    with col_atk:
        st.header("⚔️ 공격측")
        atk_en = pokemon_picker("공격 포켓몬", "atk_name_en", ko_index)
        atk_data = get_pokemon_full(atk_en) if atk_en else None

        if atk_data:
            c1, c2 = st.columns([1, 2])
            with c1:
                if atk_data["sprite"]:
                    st.image(atk_data["sprite"], width=120)
            with c2:
                st.subheader(atk_data["name_ko"])
                st.write("타입: " + " / ".join(all_types[t]["name_ko"] for t in atk_data["types_en"] if t in all_types))

            st.session_state["atk_level"] = st.slider("레벨", 1, 100, st.session_state["atk_level"], key=f"atk_level_slider_{ver}")

            valid_ev_atk = stat_inputs("atk", "atk_iv", "atk_ev")

            nat_idx = nature_options.index(st.session_state["atk_nature"]) if st.session_state["atk_nature"] in nature_options else 0
            chosen_nature_label = st.selectbox(
                "성격", [nature_labels[n] for n in nature_options], index=nat_idx, key=f"atk_nature_select_{ver}"
            )
            st.session_state["atk_nature"] = nature_options[[nature_labels[n] for n in nature_options].index(chosen_nature_label)]

            st.session_state["atk_stage"] = st.slider("능력치 랭크 (공격/특수공격)", -6, 6, st.session_state["atk_stage"], key=f"atk_stage_slider_{ver}")

            ability_names = [a["name_ko"] for a in atk_data["abilities"]]
            if ability_names:
                st.selectbox("특성 (표시용)", ability_names, key=f"atk_ability_select_{ver}")
            st.caption("특성마다 데미지 규칙이 달라 자동 계산할 수 없습니다. 아래 '기타 보정'에 배율을 직접 입력해주세요.")

            st.session_state["atk_ability_mult"] = st.number_input(
                "특성 보정 배율 (예: 순수한힘 2.0)", 0.1, 5.0, st.session_state["atk_ability_mult"], step=0.1, key=f"atk_ability_mult_in_{ver}"
            )
            st.session_state["atk_item_mult"] = st.number_input(
                "도구 보정 배율 (예: 생명의구슬 1.3)", 0.1, 5.0, st.session_state["atk_item_mult"], step=0.1, key=f"atk_item_mult_in_{ver}"
            )
            st.session_state["atk_other_mult"] = st.number_input(
                "기타 공격 보정 배율", 0.1, 5.0, st.session_state["atk_other_mult"], step=0.1, key=f"atk_other_mult_in_{ver}"
            )

            st.subheader("기술 선택")
            move_query = st.text_input("기술 검색 (이름 일부)", key=f"move_query_{ver}")
            move_candidates = atk_data["moves_en"]
            if move_query:
                q = move_query.strip().lower()
                move_candidates = [m for m in move_candidates if q in m.lower()]
            move_candidates = move_candidates[:60]

            with st.spinner("기술 정보를 불러오는 중..."):
                move_details = get_moves_bulk(tuple(move_candidates)) if move_candidates else {}

            move_labels = []
            move_map = {}
            for m in move_candidates:
                d = move_details.get(m)
                if d:
                    label = f"{d['name_ko']} ({d['power'] if d['power'] else '-'})"
                    move_labels.append(label)
                    move_map[label] = m

            if move_labels:
                sel_move_label = st.selectbox("기술", move_labels, key=f"move_select_{ver}")
                st.session_state["move_name_en"] = move_map[sel_move_label]
            else:
                st.info("검색어를 입력해 기술을 찾아보세요. (예: 지진, 인파이트, 아쿠아제트)")
        else:
            st.info("먼저 공격 포켓몬을 검색해서 선택해주세요. (예: 피카츄, 리자몽, 마릴리)")

    # ---------------- 방어측 ----------------
    with col_def:
        st.header("🛡️ 방어측")
        def_en = pokemon_picker("방어 포켓몬", "def_name_en", ko_index)
        def_data = get_pokemon_full(def_en) if def_en else None

        if def_data:
            c1, c2 = st.columns([1, 2])
            with c1:
                if def_data["sprite"]:
                    st.image(def_data["sprite"], width=120)
            with c2:
                st.subheader(def_data["name_ko"])
                st.write("타입: " + " / ".join(all_types[t]["name_ko"] for t in def_data["types_en"] if t in all_types))

            st.session_state["def_level"] = st.slider("레벨", 1, 100, st.session_state["def_level"], key=f"def_level_slider_{ver}")

            valid_ev_def = stat_inputs("def", "def_iv", "def_ev")

            nat_idx = nature_options.index(st.session_state["def_nature"]) if st.session_state["def_nature"] in nature_options else 0
            chosen_nature_label = st.selectbox(
                "성격", [nature_labels[n] for n in nature_options], index=nat_idx, key=f"def_nature_select_{ver}"
            )
            st.session_state["def_nature"] = nature_options[[nature_labels[n] for n in nature_options].index(chosen_nature_label)]

            st.session_state["def_stage"] = st.slider("능력치 랭크 (방어/특수방어)", -6, 6, st.session_state["def_stage"], key=f"def_stage_slider_{ver}")

            ability_names = [a["name_ko"] for a in def_data["abilities"]]
            if ability_names:
                st.selectbox("특성 (표시용)", ability_names, key=f"def_ability_select_{ver}")

            st.session_state["def_ability_mult"] = st.number_input(
                "특성 보정 배율 (방어측, 예: 두꺼운지방 0.5)", 0.1, 5.0, st.session_state["def_ability_mult"], step=0.1, key=f"def_ability_mult_in_{ver}"
            )
            st.session_state["def_item_mult"] = st.number_input(
                "도구 보정 배율 (방어측)", 0.1, 5.0, st.session_state["def_item_mult"], step=0.1, key=f"def_item_mult_in_{ver}"
            )
            st.session_state["def_other_mult"] = st.number_input(
                "기타 방어 보정 배율", 0.1, 5.0, st.session_state["def_other_mult"], step=0.1, key=f"def_other_mult_in_{ver}"
            )

            st.subheader("날씨")
            weather_label = st.selectbox("현재 날씨", list(WEATHER_OPTIONS.keys()),
                                          index=list(WEATHER_OPTIONS.keys()).index(st.session_state["weather"]),
                                          key=f"weather_select_{ver}")
            st.session_state["weather"] = weather_label
        else:
            st.info("방어 포켓몬을 검색해서 선택해주세요.")

    st.divider()

    # ---------------- 계산 ----------------
    ready = atk_data and def_data and st.session_state.get("move_name_en")
    calc_clicked = st.button("🧮 계산하기", type="primary", disabled=not ready, use_container_width=True)

    if not ready:
        st.caption("공격/방어 포켓몬과 기술을 모두 선택하면 계산할 수 있습니다.")
        return
    if not calc_clicked:
        return

    move = move_details.get(st.session_state["move_name_en"]) or get_move_full(st.session_state["move_name_en"])
    if not move:
        st.error("기술 정보를 불러오지 못했습니다.")
        return

    if move["damage_class"] == "status" or not move["power"]:
        st.warning("선택한 기술은 변화기이거나 위력이 없어 데미지를 계산할 수 없습니다. 위력이 있는 기술을 선택해주세요.")
        return

    weather_key = WEATHER_OPTIONS[st.session_state["weather"]]
    dmg_class = move["damage_class"]  # physical / special
    atk_stat_key = "attack" if dmg_class == "physical" else "special-attack"
    def_stat_key = "defense" if dmg_class == "physical" else "special-defense"

    atk_nature_info = all_natures[st.session_state["atk_nature"]]
    def_nature_info = all_natures[st.session_state["def_nature"]]

    # 실능치 계산
    atk_real = calc_stat(atk_data["base_stats"][atk_stat_key], st.session_state["atk_iv"][atk_stat_key],
                          st.session_state["atk_ev"][atk_stat_key], st.session_state["atk_level"],
                          atk_stat_key, atk_nature_info)
    atk_real_staged = stage_multiplier_value(atk_real, st.session_state["atk_stage"])

    def_real = calc_stat(def_data["base_stats"][def_stat_key], st.session_state["def_iv"][def_stat_key],
                          st.session_state["def_ev"][def_stat_key], st.session_state["def_level"],
                          def_stat_key, def_nature_info)
    def_real_staged = stage_multiplier_value(def_real, st.session_state["def_stage"])
    def_weather_mult = weather_defense_multiplier(weather_key, def_data["types_en"], dmg_class)
    def_real_final = math.floor(def_real_staged * def_weather_mult)

    hp_real = calc_stat(def_data["base_stats"]["hp"], st.session_state["def_iv"]["hp"],
                         st.session_state["def_ev"]["hp"], st.session_state["def_level"],
                         "hp", None)

    # 방어측 물리/특수 내구력 (참고용, 8번 요구사항)
    def_stat_for_phys = stage_multiplier_value(
        calc_stat(def_data["base_stats"]["defense"], st.session_state["def_iv"]["defense"],
                  st.session_state["def_ev"]["defense"], st.session_state["def_level"],
                  "defense", def_nature_info), st.session_state["def_stage"] if def_stat_key == "defense" else 0)
    def_stat_for_spec = stage_multiplier_value(
        calc_stat(def_data["base_stats"]["special-defense"], st.session_state["def_iv"]["special-defense"],
                  st.session_state["def_ev"]["special-defense"], st.session_state["def_level"],
                  "special-defense", def_nature_info), st.session_state["def_stage"] if def_stat_key == "special-defense" else 0)
    durability_phys = calc_naegurryeok(hp_real, def_stat_for_phys)
    durability_spec = calc_naegurryeok(hp_real, def_stat_for_spec)
    applied_durability = durability_phys if dmg_class == "physical" else durability_spec

    stab = 1.5 if is_stab(atk_data["types_en"], move["type_en"]) else 1.0
    type_mult = type_effectiveness(move["type_en"], def_data["types_en"], all_types)
    w_dmg_mult = weather_damage_multiplier(weather_key, move["type_en"])

    combined_modifier = (
        stab * type_mult * w_dmg_mult
        * st.session_state["atk_ability_mult"] * st.session_state["atk_item_mult"] * st.session_state["atk_other_mult"]
        * st.session_state["def_ability_mult"] * st.session_state["def_item_mult"] * st.session_state["def_other_mult"]
    )

    juldjeongryeok = calc_juldjeongryeok(atk_real_staged, move["power"], stab,
                                          st.session_state["atk_ability_mult"] * st.session_state["atk_item_mult"] * st.session_state["atk_other_mult"])

    if type_mult == 0:
        st.markdown("## 결과")
        st.error(f"**{move['name_ko']} → {def_data['name_ko']}** : 효과가 없다! (데미지 0)")
        with st.expander("계산 과정 보기"):
            st.write(f"방어 타입: {' / '.join(all_types[t]['name_ko'] for t in def_data['types_en'])}")
            st.write(f"기술 타입: {all_types[move['type_en']]['name_ko']}")
            st.write("타입 상성 배율: ×0 (효과 없음)")
        return

    rolls = calculate_damage_rolls(st.session_state["atk_level"], move["power"], atk_real_staged, def_real_final, combined_modifier)
    min_dmg, max_dmg = min(rolls), max(rolls)
    analysis = analyze_hits(rolls, hp_real)
    hit_label = format_hit_label(analysis)

    # ---------------- 결과 화면 (14번 요구사항) ----------------
    st.markdown("## 결과")
    st.markdown(f"### 🔥 {move['name_ko']} → 🛡️ {def_data['name_ko']}")

    if analysis.get("guaranteed_n") == 1 and analysis.get("first_possible_n") == 1:
        st.success(f"## {hit_label}")
    elif analysis.get("first_possible_n") == 1:
        st.warning(f"## {hit_label}")
    else:
        st.info(f"## {hit_label}")

    r1, r2, r3 = st.columns(3)
    r1.metric("데미지 범위", f"{min_dmg:,} ~ {max_dmg:,}")
    r2.metric("상대 HP", f"{hp_real:,}")
    r3.metric("HP 대비", f"{min_dmg/hp_real*100:.1f}% ~ {max_dmg/hp_real*100:.1f}%")

    if type_mult != 1.0:
        eff_text = "효과가 굉장했다! (×2 이상)" if type_mult > 1 else "효과가 별로인 듯하다... (×1 미만)"
        st.write(f"**타입 상성**: ×{type_mult} — {eff_text}")
    if stab == 1.5:
        st.write("**자속(STAB)**: 적용됨 ×1.5")

    r4, r5 = st.columns(2)
    r4.metric("결정력 (참고용)", f"{juldjeongryeok:,.0f}")
    r5.metric(f"내구력 ({'물리' if dmg_class=='physical' else '특수'}, 참고용)", f"{applied_durability:,.0f}")
    st.caption("결정력·내구력은 커뮤니티에서 널리 쓰이는 '한쪽 스탯만 반영한 근사 참고 지표'이며, "
               "실제 데미지·타수 판정은 위의 데미지 공식으로 별도 계산됩니다.")

    if not analysis.get("impossible"):
        st.markdown("#### 타수별 확률 (85~100% 난수 기준)")
        prob_rows = analysis["results"]
        for n, prob in prob_rows:
            bar_label = f"{n}타 이내 처치 확률"
            st.progress(min(prob / 100, 1.0), text=f"{bar_label}: {prob:.1f}%")
            if prob >= 100 - 1e-9:
                break
        st.caption("※ 위 확률은 기본 데미지 난수(85~100%, 16단계) 기준이며, 회피/급소/추가 상태이상 등 "
                   "다른 전투 변수는 반영하지 않았습니다.")

    with st.expander("📖 계산 과정 보기"):
        st.write(f"**공격 실능치 ({STAT_KEY_KO[atk_stat_key]})**: {atk_real} → 랭크 적용 후 {atk_real_staged}")
        st.write(f"**방어 실능치 ({STAT_KEY_KO[def_stat_key]})**: {def_real} → 랭크/날씨 적용 후 {def_real_final}")
        st.write(f"**방어 HP 실능치**: {hp_real}")
        st.write(f"**기술 위력**: {move['power']}")
        st.write(f"**STAB**: ×{stab}")
        st.write(f"**타입 상성**: ×{type_mult}")
        st.write(f"**날씨 위력 보정**: ×{w_dmg_mult}")
        st.write(f"**공격측 특성/도구/기타 보정**: ×{st.session_state['atk_ability_mult']} / ×{st.session_state['atk_item_mult']} / ×{st.session_state['atk_other_mult']}")
        st.write(f"**방어측 특성/도구/기타 보정**: ×{st.session_state['def_ability_mult']} / ×{st.session_state['def_item_mult']} / ×{st.session_state['def_other_mult']}")
        st.write(f"**합산 보정(난수 제외)**: ×{combined_modifier:.4f}")
        base_before_mod = base_power_damage(st.session_state["atk_level"], move["power"], atk_real_staged, def_real_final)
        st.write(f"**난수 적용 전 기본 데미지**: {base_before_mod}")
        st.write(f"**난수 16단계 데미지**: {rolls}")
        st.write(f"**최종 결정력**: {juldjeongryeok:,.0f}")

    if not (valid_ev_atk and valid_ev_def):
        st.error("EV 총합이 510을 초과한 상태에서 계산되었습니다. 값을 수정 후 다시 계산해주세요.")


if __name__ == "__main__":
    main()
