# -*- coding: utf-8 -*-
"""
포켓몬 결정력 · 내구력 · 데미지 계산기 (v3)
=============================================
Streamlit + PokeAPI 기반으로 동작하는 포켓몬 실전 계산기입니다.
포켓몬 / 기술 / 특성 / 도구 / 타입 / 성격 데이터는 하드코딩하지 않고 전부
PokeAPI(https://pokeapi.co)에서 가져옵니다. 능력치 계산식·데미지 공식·타수 판정
로직은 PokeAPI가 제공하지 않는 게임 내부 규칙이므로 코드로 직접 구현했습니다.

파일 구성
---------
- main.py           : 이 파일 하나로 앱 전체가 동작합니다.
- requirements.txt  : 배포에 필요한 패키지 목록.

실행 방법
---------
    streamlit run main.py

이번 버전에서 고친 구조적인 문제들
------------------------------------
1) [폼 처리] PokeAPI는 "종(species)"과 "실제 데이터를 가진 폼(pokemon variety)"이
   분리되어 있고, 둘의 이름이 항상 같지는 않습니다(예: 따라큐는 기본 폼 리소스가
   `mimikyu-disguised` 이고, 단순히 `mimikyu` 로 조회하면 존재하지 않을 수 있습니다).
   그래서 특정 포켓몬 이름을 하드코딩해서 예외 처리하는 대신, 종 데이터의
   `varieties` 배열에서 `is_default=true` 인 폼을 찾아 그 폼의 실제 리소스 이름으로
   `/pokemon/{name}` 을 조회하는 일반적인 구조로 바꿨습니다. 이 구조는 킬가르도,
   따라큐, 루브도, 그리고 폼이 없는 일반 포켓몬 모두에 동일하게 적용됩니다.
2) [빈 필드 방어] `moves`, `abilities`, `sprites`, `types`, `stats` 등 어떤 필드가
   없거나 비어 있어도(None) 예외로 앱이 죽지 않도록 모든 파싱에 `.get()` 과 기본값을
   사용했습니다. 일부 데이터를 가져오지 못했다고 해서 선택된 포켓몬 자체가 사라지지
   (`None` 이 되지) 않도록, 이미 선택된 이름은 실패한 부분 데이터와 무관하게
   `st.session_state`에 유지됩니다.
3) [루브도(Smeargle) 기술폭] 루브도는 실제 게임/PokeAPI 데이터 구조상 자기 자신의
   레벨업 기술로는 '스케치' 하나만 가지고 있습니다(실제로 그렇게 설계된 포켓몬입니다).
   이를 "특정 포켓몬 이름이 smeargle이면..." 같은 방식으로 예외 처리하지 않고,
   "이 포켓몬이 배우는 기술 수가 매우 적다(<=3개)"는 일반적인 조건으로 감지해서,
   그 경우에만 검색 대상을 해당 포켓몬의 movepool 대신 PokeAPI 전체 기술 목록으로
   자동 전환합니다. 이 규칙은 루브도와 유사한 구조를 가진 다른 포켓몬에도 동일하게
   적용됩니다.
4) [선택 상태 유지] 포켓몬 선택 값은 검색창의 위젯 key와 분리된 별도의
   session_state 키에 저장되며, IV/EV/성격/랭크/특성/도구/기술 등 다른 설정을
   변경해도 rerun 시 그대로 유지됩니다.

계산 정확성 관련 안내
----------------------
- 특성/도구 효과는 PokeAPI가 구조화된 배율 데이터를 제공하지 않으므로, "효과가
  명확하고 조건이 단순한" 것들만 최소한의 참조표로 직접 정리해 자동 계산에
  반영했습니다. 표에 없는 특성/도구는 설명은 보여주되 "⚠️ 현재 자동 계산
  미지원"이라고 표시하고, 필요하면 수동 배율 입력으로 대체할 수 있게 했습니다.
  절대 효과를 추측해서 적용하지 않습니다.
- 능력치 계산은 3세대 이후 공식 게임에서 쓰이는 일반식
      HP = floor((2*종족값 + IV + floor(EV/4)) * Lv / 100) + Lv + 10
      기타 = floor((floor((2*종족값 + IV + floor(EV/4)) * Lv / 100) + 5) * 성격보정)
  을 사용합니다. 레벨 50에서 이 식은 커뮤니티 간이식과 사실상 동일한 결과를 주며
  모든 레벨에서 정확합니다.
- 능력치 랭크는 `(2 + max(0,x)) / (2 - min(0,x))` 배율을 정수 나눗셈으로 적용합니다
  (+1=1.5배, +2=2배, -1≈0.67배, -2=0.5배).
- 데미지 계산은 "랜덤 배율을 제외한 모든 배율을 먼저 합쳐 floor 한 뒤, 85~100%
  난수를 곱해 다시 floor" 하는 2단계 방식을 사용합니다(일반적인 팬 계산기 방식).
"""

import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import streamlit as st

# ----------------------------------------------------------------------------
# 0. 기본 설정 / 상수
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
DAMAGE_CLASS_KO = {"physical": "물리", "special": "특수", "status": "변화"}

WEATHER_OPTIONS = {
    "없음": None,
    "맑음 (한여름의 태양 등)": "sun",
    "비 (비바라기 등)": "rain",
    "모래바람": "sand",
    "눈": "snow",
}

RANDOM_ROLLS = list(range(85, 101))  # 85% ~ 100%, 16단계
SMALL_MOVEPOOL_THRESHOLD = 3  # 이 개수 이하면 '특수 학습 포켓몬'으로 간주하고 전체 기술 목록으로 전환

# 요구사항: PokeAPI가 제공하지 않는 "고정 배율" 도구 최소 참조표.
ITEM_TYPE_BOOST = {
    "charcoal": "fire", "mystic-water": "water", "magnet": "electric",
    "miracle-seed": "grass", "never-melt-ice": "ice", "black-belt": "fighting",
    "poison-barb": "poison", "soft-sand": "ground", "sharp-beak": "flying",
    "twisted-spoon": "psychic", "silver-powder": "bug", "hard-stone": "rock",
    "spell-tag": "ghost", "dragon-fang": "dragon", "black-glasses": "dark",
    "metal-coat": "steel", "silk-scarf": "normal", "fairy-feather": "fairy",
}
ITEM_CHOICE = {"choice-band": "physical", "choice-specs": "special"}
ITEM_FLAT_BOTH = {"life-orb": 1.3}
ITEM_FLAT_PHYSICAL = {"muscle-band": 1.1}
ITEM_FLAT_SPECIAL = {"wise-glasses": 1.1}
ITEM_EXPERT_BELT = {"expert-belt"}
DEF_ITEM_STAT_BOOST = {"assault-vest": ("special-defense", 1.5)}

# 계산 가능한(효과가 명확하고 단순한) 특성 최소 참조표.
ABILITY_ATK_DOUBLE = {"huge-power", "pure-power"}  # 물리 기술 사용 시 공격 실능치 ×2
ABILITY_HUSTLE = {"hustle"}  # 물리 기술 ×1.5 (명중률 감소는 미반영)
ABILITY_SOLAR_POWER = {"solar-power"}  # 맑음 + 특수 기술일 때 ×1.5

st.set_page_config(page_title="포켓몬 결정력 · 내구력 계산기", page_icon="⚔️", layout="wide")

# ----------------------------------------------------------------------------
# 1. PokeAPI 통신 레이어 (모두 캐시 처리)
# ----------------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def api_get(url: str):
    """PokeAPI에 GET 요청을 보내고 JSON을 반환한다. 실패하면 None을 반환한다."""
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


def ko_effect_from(effect_entries):
    """effect_entries에서 한국어 설명을 우선 찾고, 없으면 영어 설명을 fallback으로 사용한다."""
    if not effect_entries:
        return None
    ko, en = None, None
    for e in effect_entries:
        lang = e.get("language", {}).get("name")
        text = e.get("short_effect") or e.get("effect")
        if not text:
            continue
        if lang == "ko":
            ko = text
        elif lang == "en":
            en = text
    return ko or en


def _extract_sprite(sprites: dict):
    """공식 아트워크 > front_default > 기타 스프라이트 순으로 사용 가능한 이미지를 찾는다.
    sprites 자체가 없거나 구조가 예상과 다르더라도 예외 없이 None을 반환한다."""
    if not sprites:
        return None
    try:
        art = (sprites.get("other") or {}).get("official-artwork", {}).get("front_default")
        if art:
            return art
    except Exception:
        pass
    if sprites.get("front_default"):
        return sprites["front_default"]
    try:
        for val in (sprites.get("other") or {}).values():
            if isinstance(val, dict) and val.get("front_default"):
                return val["front_default"]
    except Exception:
        pass
    return None


# ---- 포켓몬 (종 -> 대표 폼 리소스를 안전하게 연결) -----------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_species_list():
    """전체 포켓몬 종(species) 목록(영문명, url)을 가져온다. (단 1회 요청)"""
    data = api_get(f"{POKEAPI}/pokemon-species?limit=2000&offset=0")
    if not data:
        return []
    return data.get("results", [])


@st.cache_resource(show_spinner=False)
def build_pokemon_index():
    """모든 포켓몬 종에 대해 (한국어 이름, 실제로 조회해야 할 '기본 폼' 리소스 이름)을
    함께 인덱싱한다.

    PokeAPI에서 species(종)와 실제 스탯/타입을 가진 pokemon(폼) 리소스는 이름이
    다를 수 있다(예: 종은 'mimikyu' 지만 실제 데이터를 가진 기본 폼은
    'mimikyu-disguised' 일 수 있음). species의 'varieties' 배열에서
    is_default=true 인 항목의 pokemon 리소스 이름을 사용하면, 폼 유무와 무관하게
    항상 올바른 /pokemon/{name} 엔드포인트를 찾을 수 있다.
    """
    species_list = get_species_list()
    index = {}  # species_en -> {"ko": str, "pokemon_name": str}

    def fetch_one(item):
        species_en = item["name"]
        data = api_get(item["url"])
        if not data:
            return species_en, {"ko": species_en, "pokemon_name": species_en}
        ko = ko_name_from(data.get("names", []), species_en)
        varieties = data.get("varieties") or []
        default_variety = next((v for v in varieties if v.get("is_default")), None)
        if default_variety and default_variety.get("pokemon", {}).get("name"):
            pokemon_name = default_variety["pokemon"]["name"]
        else:
            # varieties 정보가 없거나 비어 있으면 종 이름으로 직접 시도 (대부분의 포켓몬은 동일)
            pokemon_name = species_en
        return species_en, {"ko": ko, "pokemon_name": pokemon_name}

    if not species_list:
        return index

    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(fetch_one, item) for item in species_list]
        for fut in as_completed(futures):
            species_en, info = fut.result()
            index[species_en] = info
    return index


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_pokemon_light(pokemon_name_en: str):
    """검색 결과 미리보기용 가벼운 조회 (이미지 · 타입만)."""
    data = api_get(f"{POKEAPI}/pokemon/{pokemon_name_en}")
    if not data:
        return None
    types_sorted = sorted((data.get("types") or []), key=lambda x: x.get("slot", 0))
    types_en = [t.get("type", {}).get("name") for t in types_sorted if t.get("type", {}).get("name")]
    return {"id": data.get("id"), "types_en": types_en, "sprite": _extract_sprite(data.get("sprites"))}


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_pokemon_full(species_en: str):
    """포켓몬 1마리의 전체 데이터(종족값/타입/특성/스프라이트/기술목록)를 안전하게 가져온다.
    species_en 은 검색에 사용한 '종' 이름이며, 내부적으로 올바른 폼 리소스를 찾아 조회한다."""
    idx = build_pokemon_index()  # 이미 캐시된 리소스를 재사용 (네트워크 재요청 없음)
    entry = idx.get(species_en)
    pokemon_name = entry["pokemon_name"] if entry else species_en
    ko_name = entry["ko"] if entry else species_en

    pdata = api_get(f"{POKEAPI}/pokemon/{pokemon_name}")
    if not pdata and pokemon_name != species_en:
        # 폼 이름으로 실패했다면 종 이름 자체로 한 번 더 시도 (안전망)
        pdata = api_get(f"{POKEAPI}/pokemon/{species_en}")
    if not pdata:
        return None

    base_stats = {s.get("stat", {}).get("name"): s.get("base_stat", 0) for s in (pdata.get("stats") or [])}
    for k in STAT_ORDER:
        base_stats.setdefault(k, 0)

    types_sorted = sorted((pdata.get("types") or []), key=lambda x: x.get("slot", 0))
    types_en = [t.get("type", {}).get("name") for t in types_sorted if t.get("type", {}).get("name")]
    if not types_en:
        types_en = ["normal"]  # 방어적 기본값 (실제로는 거의 발생하지 않음)

    abilities = []
    for a in (pdata.get("abilities") or []):
        a_ref = a.get("ability") or {}
        a_name = a_ref.get("name")
        if not a_name:
            continue
        a_data = api_get(a_ref.get("url")) if a_ref.get("url") else None
        ko = ko_name_from(a_data.get("names", []), a_name) if a_data else a_name
        effect = ko_effect_from(a_data.get("effect_entries", [])) if a_data else None
        abilities.append({"name_en": a_name, "name_ko": ko, "effect": effect, "is_hidden": a.get("is_hidden", False)})

    sprite = _extract_sprite(pdata.get("sprites"))
    moves_en = sorted({
        m.get("move", {}).get("name")
        for m in (pdata.get("moves") or [])
        if m.get("move", {}).get("name")
    })

    return {
        "id": pdata.get("id"),
        "species_en": species_en,
        "pokemon_name_en": pokemon_name,
        "name_ko": ko_name,
        "types_en": types_en,
        "base_stats": base_stats,
        "abilities": abilities,
        "sprite": sprite,
        "moves_en": moves_en,
        "limited_movepool": len(moves_en) <= SMALL_MOVEPOOL_THRESHOLD,
    }


# ---- 기술 -------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_move_full(name_en: str):
    """기술 1개의 상세 정보를 가져온다."""
    data = api_get(f"{POKEAPI}/move/{name_en}")
    if not data:
        return None
    dmg_class = (data.get("damage_class") or {}).get("name", "status")
    move_type = (data.get("type") or {}).get("name", "normal")
    return {
        "name_en": name_en,
        "name_ko": ko_name_from(data.get("names", []), name_en),
        "power": data.get("power"),  # None이면 변화기 등 위력 없는 기술
        "type_en": move_type,
        "damage_class": dmg_class,  # physical / special / status
        "pp": data.get("pp"),
        "accuracy": data.get("accuracy"),
    }


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_moves_bulk(move_names: tuple):
    """여러 기술을 동시에 조회한다 (기술 검색용)."""
    result = {}
    if not move_names:
        return result

    def fetch_one(name):
        return name, get_move_full(name)

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_one, n) for n in move_names]
        for fut in as_completed(futures):
            name, data = fut.result()
            if data:
                result[name] = data
    return result


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_all_moves_list():
    """PokeAPI 전체 기술 목록(영문명, url)을 가져온다. (단 1회 요청)
    루브도처럼 자체 movepool이 극히 적은 포켓몬을 위한 전체 검색 대상이다."""
    data = api_get(f"{POKEAPI}/move?limit=1500&offset=0")
    if not data:
        return []
    return data.get("results", [])


@st.cache_resource(show_spinner=False)
def build_korean_move_index():
    """전체 기술의 한국어 이름 인덱스를 구축한다 (en -> ko).
    movepool이 극히 적은 포켓몬(예: 루브도)을 선택했을 때만 지연 구축한다."""
    move_list = get_all_moves_list()
    index = {}

    def fetch_one(item):
        data = api_get(item["url"])
        if not data:
            return item["name"], item["name"]
        return item["name"], ko_name_from(data.get("names", []), item["name"])

    if not move_list:
        return index

    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(fetch_one, item) for item in move_list]
        for fut in as_completed(futures):
            en, ko = fut.result()
            index[en] = ko
    return index


# ---- 도구 -------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_item_list():
    """전체 도구 목록(영문명, url)을 가져온다. (단 1회 요청)"""
    data = api_get(f"{POKEAPI}/item?limit=2000&offset=0")
    if not data:
        return []
    return data.get("results", [])


@st.cache_resource(show_spinner=False)
def build_korean_item_index():
    """전체 도구의 한국어 이름 인덱스를 구축한다 (en -> ko). 도구 검색을 처음 켤 때 지연 구축한다."""
    item_list = get_item_list()
    index = {}

    def fetch_one(item):
        data = api_get(item["url"])
        if not data:
            return item["name"], item["name"]
        return item["name"], ko_name_from(data.get("names", []), item["name"])

    if not item_list:
        return index

    with ThreadPoolExecutor(max_workers=24) as executor:
        futures = [executor.submit(fetch_one, item) for item in item_list]
        for fut in as_completed(futures):
            en, ko = fut.result()
            index[en] = ko
    return index


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_item_full(name_en: str):
    """도구 1개의 상세 정보(이름·효과 설명·아이콘)를 가져온다."""
    data = api_get(f"{POKEAPI}/item/{name_en}")
    if not data:
        return None
    effect = ko_effect_from(data.get("effect_entries", []))
    sprite = (data.get("sprites") or {}).get("default")
    return {
        "name_en": name_en,
        "name_ko": ko_name_from(data.get("names", []), name_en),
        "effect": effect,
        "sprite": sprite,
    }


def get_item_damage_effect(item_name_en, move_type_en, damage_class, type_mult):
    """PokeAPI 데이터만으로 명확하게 판정 가능한 '고정 배율' 도구에 한해 데미지 배율을 계산한다.
    표에 없는 도구는 (None, False, 안내문)을 반환한다."""
    if not item_name_en:
        return 1.0, True, "도구 없음"

    if item_name_en in ITEM_TYPE_BOOST:
        boosted_type = ITEM_TYPE_BOOST[item_name_en]
        if boosted_type == move_type_en:
            return 1.2, True, "타입 강화 도구: 기술 타입과 일치하여 ×1.2 적용"
        return 1.0, True, "타입 강화 도구지만 기술 타입이 달라 효과 없음"

    if item_name_en in ITEM_CHOICE:
        if ITEM_CHOICE[item_name_en] == damage_class:
            return 1.5, True, "구애 시리즈: ×1.5 적용 (고정 기술 효과는 미반영)"
        return 1.0, True, "구애 시리즈지만 물리/특수 분류가 달라 효과 없음"

    if item_name_en in ITEM_FLAT_BOTH:
        return ITEM_FLAT_BOTH[item_name_en], True, f"×{ITEM_FLAT_BOTH[item_name_en]} 적용 (반동 등 다른 효과는 미반영)"

    if item_name_en in ITEM_FLAT_PHYSICAL:
        if damage_class == "physical":
            return ITEM_FLAT_PHYSICAL[item_name_en], True, f"물리 기술에 ×{ITEM_FLAT_PHYSICAL[item_name_en]} 적용"
        return 1.0, True, "물리 기술 전용 도구지만 현재 기술은 특수라 효과 없음"

    if item_name_en in ITEM_FLAT_SPECIAL:
        if damage_class == "special":
            return ITEM_FLAT_SPECIAL[item_name_en], True, f"특수 기술에 ×{ITEM_FLAT_SPECIAL[item_name_en]} 적용"
        return 1.0, True, "특수 기술 전용 도구지만 현재 기술은 물리라 효과 없음"

    if item_name_en in ITEM_EXPERT_BELT:
        if type_mult > 1.0:
            return 1.2, True, "효과가 굉장한 기술에 ×1.2 적용"
        return 1.0, True, "효과가 굉장하지 않아 발동하지 않음"

    return None, False, "⚠️ 현재 자동 계산 미지원 (아래 수동 배율 입력을 사용해주세요)"


# ---- 타입 -------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_type_data(type_name_en: str):
    """타입 1개의 한국어 이름과 상성 관계(damage_relations)를 가져온다."""
    data = api_get(f"{POKEAPI}/type/{type_name_en}")
    if not data:
        return None
    return {
        "name_en": type_name_en,
        "name_ko": ko_name_from(data.get("names", []), type_name_en),
        "relations": data.get("damage_relations", {}),
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


# ---- 성격 -------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_all_natures():
    """25개 성격의 한국어 이름과 증가/감소 스탯을 PokeAPI에서 가져온다."""
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
            inc = (data.get("increased_stat") or {}).get("name")
            dec = (data.get("decreased_stat") or {}).get("name")
            result[n] = {
                "name_ko": ko_name_from(data.get("names", []), n),
                "increased_stat": inc,
                "decreased_stat": dec,
                "neutral": inc is None,
            }
    return result


def nature_label(nature_info: dict) -> str:
    """'고집 (공격↑ 특수공격↓)' 형태의 표시용 라벨을 만든다."""
    if nature_info["neutral"]:
        return f"{nature_info['name_ko']} (변화 없음)"
    inc = STAT_KEY_KO.get(nature_info["increased_stat"], nature_info["increased_stat"])
    dec = STAT_KEY_KO.get(nature_info["decreased_stat"], nature_info["decreased_stat"])
    return f"{nature_info['name_ko']} ({inc}↑ {dec}↓)"


# ----------------------------------------------------------------------------
# 2. 능력치 계산
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
        return inner + level + 10
    mult = nature_multiplier(nature_info, stat_key)
    return math.floor((inner + 5) * mult)


def rank_multiplier_value(stat: int, rank: int) -> int:
    """능력치 랭크(-6~+6)를 실제 스탯에 적용한다.
    공식: (2 + max(0,rank)) / (2 - min(0,rank)), 게임처럼 정수 나눗셈 사용.
    +1=1.5배, +2=2배, -1≈0.67배, -2=0.5배가 되도록 구현했다."""
    rank = max(-6, min(6, rank))
    numerator = 2 + max(0, rank)
    denominator = 2 - min(0, rank)
    return (stat * numerator) // denominator


# ----------------------------------------------------------------------------
# 3. 타입 상성 / STAB
# ----------------------------------------------------------------------------


def type_effectiveness(attack_type_en: str, defender_types_en: list, all_types: dict) -> float:
    """공격 타입이 방어 포켓몬의 (복합)타입에 대해 갖는 배율을 계산한다."""
    atk = all_types.get(attack_type_en)
    if not atk:
        return 1.0
    rel = atk.get("relations", {})
    double = {t["name"] for t in rel.get("double_damage_to", [])}
    half = {t["name"] for t in rel.get("half_damage_to", [])}
    zero = {t["name"] for t in rel.get("no_damage_to", [])}

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
    """공격 포켓몬의 타입과 기술 타입이 일치하는지(자속) 판정한다."""
    return move_type_en in attacker_types_en


# ----------------------------------------------------------------------------
# 4. 날씨 보정
# ----------------------------------------------------------------------------


def weather_damage_multiplier(weather: str, move_type_en: str) -> float:
    """맑음/비는 불꽃·물 기술 위력에 직접 배율이 붙는다(공식 규칙)."""
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
    """모래바람: 바위 타입의 특수방어 ×1.5 / 눈: 얼음 타입의 방어 ×1.5 (공식 규칙)."""
    if weather == "sand" and damage_class == "special" and "rock" in defender_types_en:
        return 1.5
    if weather == "snow" and damage_class == "physical" and "ice" in defender_types_en:
        return 1.5
    return 1.0


# ----------------------------------------------------------------------------
# 5. 특성 자동 효과 (계산 가능한 것만)
# ----------------------------------------------------------------------------


def get_ability_auto_effect(ability_name_en: str, damage_class: str, weather_key):
    """효과가 명확하고 단순한 특성에 한해 데미지 배율을 계산한다.
    반환: (배율, 지원여부 bool, 설명 str). 지원하지 않으면 (None, False, 안내문)."""
    if not ability_name_en:
        return 1.0, True, "특성 없음"
    if ability_name_en in ABILITY_ATK_DOUBLE:
        if damage_class == "physical":
            return 2.0, True, "물리 기술 사용 시 공격 실능치 ×2 적용"
        return 1.0, True, "물리 기술이 아니므로 효과 없음"
    if ability_name_en in ABILITY_HUSTLE:
        if damage_class == "physical":
            return 1.5, True, "물리 기술에 ×1.5 적용 (명중률 감소는 미반영)"
        return 1.0, True, "물리 기술이 아니므로 효과 없음"
    if ability_name_en in ABILITY_SOLAR_POWER:
        if damage_class == "special" and weather_key == "sun":
            return 1.5, True, "맑음 + 특수 기술 조건 충족으로 ×1.5 적용"
        return 1.0, True, "조건(맑음 + 특수 기술) 불충족으로 효과 없음"
    return None, False, "⚠️ 현재 자동 계산 미지원"


# ----------------------------------------------------------------------------
# 6. 데미지 / 난수 계산
# ----------------------------------------------------------------------------


def base_power_damage(level: int, power: int, attack_stat: int, defense_stat: int) -> int:
    """랜덤 보정 이전의 기본 데미지.
    floor( floor( (2*Level/5 + 2) * Power * Attack / Defense ) / 50 ) + 2
    """
    defense_stat = max(defense_stat, 1)  # 0으로 나누는 사고 방지
    step1 = (2 * level / 5) + 2
    step2 = step1 * power * attack_stat / defense_stat
    step2 = math.floor(step2)
    step3 = math.floor(step2 / 50) + 2
    return step3


def calculate_damage_rolls(level: int, power: int, attack_stat: int, defense_stat: int,
                            combined_modifier: float) -> list:
    """85~100% 난수 16종에 대한 최종 데미지 리스트(정수)를 반환한다.
    실제 게임처럼: (기본데미지 × 기타보정들 을 먼저 floor) → (그 값 × 난수 를 floor) 순서로 계산한다."""
    base = base_power_damage(level, power, attack_stat, defense_stat)
    modified = math.floor(base * combined_modifier)
    if combined_modifier > 0:
        modified = max(modified, 1)
    rolls = []
    for r in RANDOM_ROLLS:
        dmg = math.floor(modified * r / 100)
        if combined_modifier > 0:
            dmg = max(dmg, 1)
        rolls.append(dmg)
    return rolls


# ----------------------------------------------------------------------------
# 7. 타수 판정 & 확률 계산
# ----------------------------------------------------------------------------


def analyze_hits(damage_rolls: list, hp: int, max_hits_to_check: int = 9):
    """몇 타에 쓰러뜨릴 수 있는지, 그리고 각 타수별 확률을 계산한다.
    16가지 난수 값이 각각 1/16 확률로 발생한다고 가정하고,
    n번 공격했을 때의 데미지 합 분포를 컨볼루션으로 직접 계산한다."""
    n_rolls = len(damage_rolls)
    if max(damage_rolls) <= 0:
        return {"impossible": True}

    dist = {d: 1 for d in damage_rolls}
    results = []
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
        if n < max_hits_to_check:
            new_dist = {}
            for total, cnt in dist.items():
                for d in damage_rolls:
                    key = total + d
                    new_dist[key] = new_dist.get(key, 0) + cnt
            dist = new_dist

    return {
        "impossible": False,
        "results": results,
        "guaranteed_n": guaranteed_n,
        "first_possible_n": first_possible_n,
    }


def format_hit_label(analysis: dict) -> str:
    if analysis.get("impossible"):
        return "데미지 없음 (처치 불가)"
    g = analysis["guaranteed_n"]
    f = analysis["first_possible_n"]
    if g == f:
        return f"확정 {g}타"
    if g is not None:
        return f"난수 {f}타 (확정은 {g}타)"
    return f"{f}타 이상 (9타 이내 확정 불가)"


# ----------------------------------------------------------------------------
# 8. 결정력 / 내구력 (커뮤니티 관용 지표 — 실제 데미지 공식과 구분)
# ----------------------------------------------------------------------------


def calc_juldjeongryeok(attack_stat: int, power: int, stab: float, other_mult: float) -> float:
    """'결정력' = 공격 실능치 × 기술 위력 × STAB × 기타보정."""
    return attack_stat * power * stab * other_mult


def calc_naegurryeok(hp_stat: int, def_stat: int) -> float:
    """'내구력' = HP 실능치 × 방어(또는 특수방어) 실능치 ÷ 0.411."""
    return hp_stat * def_stat / 0.411


# ----------------------------------------------------------------------------
# 9. Streamlit UI
# ----------------------------------------------------------------------------

DEFAULT_RANK = {"attack": 0, "special-attack": 0, "defense": 0, "special-defense": 0, "speed": 0}

DEFAULTS = {
    "atk_species_en": None, "def_species_en": None,
    "atk_level": 50, "def_level": 50,
    "atk_iv": {k: 31 for k in STAT_ORDER}, "atk_ev": {k: 0 for k in STAT_ORDER},
    "def_iv": {k: 31 for k in STAT_ORDER}, "def_ev": {k: 0 for k in STAT_ORDER},
    "atk_nature": "hardy", "def_nature": "hardy",
    "atk_rank": dict(DEFAULT_RANK), "def_rank": dict(DEFAULT_RANK),
    "weather": "없음",
    "atk_item_name_en": None, "def_item_name_en": None,
    "atk_item_manual_mult": 1.0, "def_item_manual_mult": 1.0,
    "atk_ability_name_en": None, "def_ability_name_en": None,
    "atk_ability_manual_mult": 1.0, "def_ability_manual_mult": 1.0,
    "atk_other_mult": 1.0, "def_other_mult": 1.0,
    "move_name_en": None,
    "use_items": False,
}


def init_state():
    if "ver" not in st.session_state:
        # 위젯 key에 붙는 버전 번호. 교체/초기화를 누르면 값이 바뀌어 Streamlit이
        # 입력 위젯을 '새 위젯'으로 인식하고 session_state에 저장된 값을 그대로
        # 초기 표시값으로 사용하게 만든다. (버튼을 눌러도 선택된 포켓몬 자체는
        # 항상 별도의 *_species_en 키에 보존되므로 사라지지 않는다.)
        st.session_state["ver"] = 0
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = dict(v) if isinstance(v, dict) else v


def reset_state():
    for k in DEFAULTS:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["ver"] = st.session_state.get("ver", 0) + 1
    init_state()


def swap_sides():
    pairs = [
        ("atk_species_en", "def_species_en"), ("atk_level", "def_level"),
        ("atk_iv", "def_iv"), ("atk_ev", "def_ev"),
        ("atk_nature", "def_nature"), ("atk_rank", "def_rank"),
        ("atk_item_name_en", "def_item_name_en"),
        ("atk_item_manual_mult", "def_item_manual_mult"),
        ("atk_ability_name_en", "def_ability_name_en"),
        ("atk_ability_manual_mult", "def_ability_manual_mult"),
        ("atk_other_mult", "def_other_mult"),
    ]
    for a, b in pairs:
        st.session_state[a], st.session_state[b] = st.session_state[b], st.session_state[a]
    st.session_state["ver"] = st.session_state.get("ver", 0) + 1


def pokemon_picker(label: str, state_key: str, pkm_index: dict, all_types: dict, ver: int):
    """이름 일부(한글/영문)만 입력해도 검색되고, 썸네일과 타입을 보여주는 포켓몬 선택 위젯.
    선택된 값은 검색창 위젯과 분리된 state_key에 저장되므로, 검색어를 지우거나
    다른 설정을 바꿔도 선택 상태가 사라지지 않는다."""
    query = st.text_input(f"{label} 검색 (한글/영문 일부만 입력, 예: 리자 / char)", key=f"{state_key}_query_{ver}")
    if query:
        q = query.strip().lower()
        matches = [
            (en, info["ko"]) for en, info in pkm_index.items()
            if q in en.lower() or q in info["ko"].lower()
        ]
        matches = sorted(matches, key=lambda x: (len(x[1]), x[1]))[:8]
        if not matches:
            st.warning("검색 결과가 없습니다. 다른 이름으로 시도해보세요.")
        else:
            st.caption("검색 결과에서 선택하세요 👇")
            for row_start in range(0, len(matches), 4):
                row = matches[row_start:row_start + 4]
                cols = st.columns(len(row))
                for col, (en, ko) in zip(cols, row):
                    with col:
                        pokemon_name = pkm_index[en]["pokemon_name"]
                        light = get_pokemon_light(pokemon_name)
                        if light and light.get("sprite"):
                            st.image(light["sprite"], width=64)
                        else:
                            st.caption("(이미지 없음)")
                        type_txt = ""
                        if light:
                            type_txt = "/".join(
                                all_types[t]["name_ko"] for t in light["types_en"] if t in all_types
                            )
                        if st.button(ko, key=f"pick_{state_key}_{en}_{ver}", help=type_txt, use_container_width=True):
                            st.session_state[state_key] = en
                            st.rerun()
                        if type_txt:
                            st.caption(type_txt)
    return st.session_state.get(state_key)


def stat_inputs(prefix: str, iv_key: str, ev_key: str, ver: int):
    """IV/EV 입력 UI. EV 총합 510 제한을 검증한다."""
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


def nature_picker(prefix: str, state_key: str, nature_options: list, all_natures: dict, ver: int):
    """상승/하락 능력치를 함께 보여주는 성격 선택 위젯 + 적용 보정 안내."""
    labels = [nature_label(all_natures[n]) for n in nature_options]
    cur = st.session_state[state_key]
    idx = nature_options.index(cur) if cur in nature_options else 0
    chosen_label = st.selectbox("성격", labels, index=idx, key=f"{prefix}_nature_select_{ver}")
    chosen_n = nature_options[labels.index(chosen_label)]
    st.session_state[state_key] = chosen_n

    info = all_natures[chosen_n]
    if info["neutral"]:
        st.caption("적용 보정: 모든 능력치 ×1.0 (변화 없음)")
    else:
        inc = STAT_KEY_KO[info["increased_stat"]]
        dec = STAT_KEY_KO[info["decreased_stat"]]
        st.caption(f"적용 보정: {inc} ×1.1 / {dec} ×0.9")
    return chosen_n


def rank_control(display_label: str, rank_dict_key: str, stat_key: str, widget_prefix: str, ver: int):
    """-/+ 버튼으로 -6~+6 랭크를 조절하는 위젯. 0은 '±0'으로 표시한다."""
    rank_dict = st.session_state[rank_dict_key]
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("−", key=f"{widget_prefix}_{stat_key}_minus_{ver}", use_container_width=True):
            rank_dict[stat_key] = max(-6, rank_dict[stat_key] - 1)
    with col2:
        val = rank_dict[stat_key]
        val_txt = "±0" if val == 0 else (f"+{val}" if val > 0 else f"{val}")
        st.markdown(f"<div style='text-align:center; padding-top:6px;'>{display_label} <b>{val_txt}</b></div>", unsafe_allow_html=True)
    with col3:
        if st.button("＋", key=f"{widget_prefix}_{stat_key}_plus_{ver}", use_container_width=True):
            rank_dict[stat_key] = min(6, rank_dict[stat_key] + 1)
    return rank_dict[stat_key]


def item_picker(prefix: str, state_key: str, ko_item_index: dict, ver: int):
    """이름 일부만 입력해도 되는 도구 검색 위젯. 선택된 도구의 효과 설명도 표시한다."""
    query = st.text_input("도구 검색 (한글/영문 일부, 예: 구애)", key=f"{prefix}_item_query_{ver}")
    if query:
        q = query.strip().lower()
        matches = [(en, ko) for en, ko in ko_item_index.items() if q in en.lower() or q in ko.lower()]
        matches = sorted(matches, key=lambda x: (len(x[1]), x[1]))[:15]
        if not matches:
            st.warning("검색 결과가 없습니다.")
        else:
            options = ["(도구 없음)"] + [f"{ko} ({en})" for en, ko in matches]
            sel = st.selectbox("검색 결과에서 선택", options, key=f"{prefix}_item_select_{ver}")
            if sel == "(도구 없음)":
                st.session_state[state_key] = None
            else:
                chosen_en = matches[options.index(sel) - 1][0]
                st.session_state[state_key] = chosen_en

    current_en = st.session_state.get(state_key)
    if current_en:
        item_data = get_item_full(current_en)
        if item_data:
            c1, c2 = st.columns([1, 4])
            with c1:
                if item_data.get("sprite"):
                    st.image(item_data["sprite"], width=40)
            with c2:
                st.write(f"**{item_data['name_ko']}**")
                if item_data.get("effect"):
                    st.caption(item_data["effect"])
        if st.button("도구 해제", key=f"{prefix}_item_clear_{ver}"):
            st.session_state[state_key] = None
            st.rerun()
    return st.session_state.get(state_key)


def ability_section(prefix: str, pkm_data: dict, name_state_key: str, manual_mult_key: str,
                     damage_class_for_check, weather_key, ver: int):
    """포켓몬의 실제 특성 목록에서 선택하고, 설명과 자동 계산 지원 여부를 표시한다."""
    abilities = pkm_data.get("abilities") or []
    if not abilities:
        st.caption("특성 정보를 가져오지 못했습니다.")
        return None, 1.0

    labels = [f"{a['name_ko']}" + (" (숨겨진 특성)" if a.get("is_hidden") else "") for a in abilities]
    cur_en = st.session_state.get(name_state_key)
    en_list = [a["name_en"] for a in abilities]
    idx = en_list.index(cur_en) if cur_en in en_list else 0
    sel_label = st.selectbox("특성", labels, index=idx, key=f"{prefix}_ability_select_{ver}")
    chosen = abilities[labels.index(sel_label)]
    st.session_state[name_state_key] = chosen["name_en"]

    st.markdown(f"**특성 [ {chosen['name_ko']} ]**")
    if chosen.get("effect"):
        st.caption(chosen["effect"])
    else:
        st.caption("(효과 설명을 가져오지 못했습니다)")

    if damage_class_for_check is None:
        # 기술을 아직 선택하지 않은 단계에서는 지원 여부를 판단할 수 없다.
        return chosen["name_en"], 1.0

    auto_mult, supported, note = get_ability_auto_effect(chosen["name_en"], damage_class_for_check, weather_key)
    if supported:
        st.caption(f"계산 적용: ✓ {note}")
        return chosen["name_en"], auto_mult
    else:
        st.caption(f"계산 적용: {note}")
        st.session_state[manual_mult_key] = st.number_input(
            "수동 배율 입력 (선택 사항)", 0.1, 5.0, st.session_state[manual_mult_key], step=0.1,
            key=f"{prefix}_ability_manual_{ver}",
        )
        return chosen["name_en"], st.session_state[manual_mult_key]


def main():
    init_state()
    ver = st.session_state["ver"]

    st.title("포켓몬 결정력 · 내구력 계산기")
    st.write(
        "공격 포켓몬과 방어 포켓몬의 능력치와 기술을 설정하고 "
        "결정력, 내구력, 실제 데미지와 난수 범위를 계산합니다."
    )

    top_l, top_r = st.columns(2)
    with top_l:
        if st.button("↔️ 포켓몬 교체", use_container_width=True):
            swap_sides()
            st.rerun()
    with top_r:
        if st.button("🔄 초기화", use_container_width=True):
            reset_state()
            st.rerun()

    with st.spinner("포켓몬 도감 데이터베이스를 준비하는 중입니다 (최초 1회, 다소 시간이 걸릴 수 있어요)..."):
        pkm_index = build_pokemon_index()
    if not pkm_index:
        st.error("PokeAPI에서 포켓몬 목록을 가져오지 못했습니다. 네트워크 상태를 확인하고 새로고침해주세요.")
        return

    all_types = get_all_types()
    all_natures = get_all_natures()
    if not all_types or not all_natures:
        st.error("PokeAPI에서 타입/성격 데이터를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return

    nature_options = list(all_natures.keys())

    st.session_state["use_items"] = st.checkbox(
        "🎒 도구(held item) 설정 사용 — 처음 켤 때 도구 데이터베이스 로딩에 다소 시간이 걸립니다",
        value=st.session_state["use_items"],
    )
    ko_item_index = {}
    if st.session_state["use_items"]:
        with st.spinner("도구 데이터베이스를 준비하는 중입니다 (최초 1회)..."):
            ko_item_index = build_korean_item_index()
        if not ko_item_index:
            st.warning("도구 목록을 가져오지 못했습니다. 도구 기능 없이 계속 진행합니다.")

    col_atk, col_def = st.columns(2)
    atk_data = None
    def_data = None
    move_details = {}
    atk_ability_mult = 1.0
    def_ability_mult = 1.0

    # ---------------- 공격측 ----------------
    with col_atk:
        st.header("⚔️ 공격측")
        atk_en = pokemon_picker("공격 포켓몬", "atk_species_en", pkm_index, all_types, ver)
        atk_data = get_pokemon_full(atk_en) if atk_en else None

        if atk_en and not atk_data:
            st.error("이 포켓몬의 데이터를 PokeAPI에서 불러오지 못했습니다. 네트워크 상태를 확인하거나 다시 검색해주세요.")

        if atk_data:
            c1, c2 = st.columns([1, 2])
            with c1:
                if atk_data["sprite"]:
                    st.image(atk_data["sprite"], width=130)
                else:
                    st.caption("(이미지 없음)")
            with c2:
                st.subheader(atk_data["name_ko"])
                st.write(" / ".join(all_types[t]["name_ko"] for t in atk_data["types_en"] if t in all_types))

            st.session_state["atk_level"] = st.slider("레벨", 1, 100, st.session_state["atk_level"], key=f"atk_level_slider_{ver}")

            with st.expander("세부 능력치 설정 (IV / EV / 성격 / 랭크)", expanded=True):
                valid_ev_atk = stat_inputs("atk", "atk_iv", "atk_ev", ver)
                nature_picker("atk", "atk_nature", nature_options, all_natures, ver)
                st.caption("능력치 랭크 (−6 ~ +6)")
                rc1, rc2, rc3 = st.columns(3)
                with rc1:
                    rank_control("공격", "atk_rank", "attack", "atk", ver)
                with rc2:
                    rank_control("특수공격", "atk_rank", "special-attack", "atk", ver)
                with rc3:
                    rank_control("스피드", "atk_rank", "speed", "atk", ver)

            with st.expander("특성 / 도구 / 기타 보정", expanded=False):
                st.session_state["atk_ability_name_en"], _ = ability_section(
                    "atk", atk_data, "atk_ability_name_en", "atk_ability_manual_mult", None, None, ver
                )
                if st.session_state["use_items"] and ko_item_index:
                    item_picker("atk", "atk_item_name_en", ko_item_index, ver)
                else:
                    st.caption("도구 검색을 사용하려면 위의 '도구 설정 사용'을 체크하세요.")
                st.session_state["atk_other_mult"] = st.number_input(
                    "기타 공격 보정 배율", 0.1, 5.0, st.session_state["atk_other_mult"], step=0.1, key=f"atk_other_mult_in_{ver}"
                )

            st.subheader("기술 선택")
            if atk_data.get("limited_movepool"):
                st.info("이 포켓몬은 자체 movepool이 매우 적습니다(예: 스케치로 배우는 포켓몬). "
                        "전체 기술 목록에서 검색합니다.")
                with st.spinner("전체 기술 데이터베이스를 준비하는 중입니다 (최초 1회)..."):
                    ko_move_index = build_korean_move_index()
                move_query = st.text_input("기술 검색 (한글/영문 일부, 예: 지진 / earth)", key=f"move_query_all_{ver}")
                if move_query:
                    q = move_query.strip().lower()
                    candidate_names = [n for n, ko in ko_move_index.items() if q in n.lower() or q in ko.lower()][:50]
                else:
                    candidate_names = []
                with st.spinner("기술 정보를 불러오는 중..."):
                    move_details = get_moves_bulk(tuple(candidate_names))
                filtered = [m for m in candidate_names if m in move_details]
            else:
                move_query = st.text_input("기술 검색 (한글/영문 일부, 예: 지진 / earth)", key=f"move_query_{ver}")
                with st.spinner("이 포켓몬이 배울 수 있는 기술 정보를 불러오는 중..."):
                    move_details = get_moves_bulk(tuple(atk_data["moves_en"]))
                if move_query:
                    q = move_query.strip().lower()
                    filtered = [
                        m for m in atk_data["moves_en"]
                        if m in move_details and (q in m.lower() or q in move_details[m]["name_ko"].lower())
                    ]
                else:
                    filtered = []
            filtered = filtered[:50]

            move_labels = []
            move_map = {}
            for m in filtered:
                d = move_details[m]
                move_labels.append(f"{d['name_ko']} ({d['power'] if d['power'] else '변화기'})")
                move_map[move_labels[-1]] = m

            if move_labels:
                sel_move_label = st.selectbox("기술", move_labels, key=f"move_select_{ver}")
                st.session_state["move_name_en"] = move_map[sel_move_label]
                mv = move_details[st.session_state["move_name_en"]]
                mv_type_ko = all_types.get(mv["type_en"], {}).get("name_ko", mv["type_en"])
                cls_ko = DAMAGE_CLASS_KO.get(mv["damage_class"], mv["damage_class"])
                st.info(f"타입: {mv_type_ko} | 분류: {cls_ko} | 위력: {mv['power'] if mv['power'] else '없음'}")
                if mv["damage_class"] == "status" or not mv["power"]:
                    st.warning("변화기이거나 위력이 없는 기술이라 데미지 계산이 불가능합니다.")
            elif move_query:
                st.warning("검색 결과가 없습니다.")
            else:
                st.info("검색어를 입력해 기술을 찾아보세요. (예: 지진, 인파이트, 아쿠아제트)")
        else:
            st.info("먼저 공격 포켓몬을 검색해서 선택해주세요. (예: 피카츄, 리자몽, 마릴리)")

    # ---------------- 방어측 ----------------
    with col_def:
        st.header("🛡️ 방어측")
        def_en = pokemon_picker("방어 포켓몬", "def_species_en", pkm_index, all_types, ver)
        def_data = get_pokemon_full(def_en) if def_en else None

        if def_en and not def_data:
            st.error("이 포켓몬의 데이터를 PokeAPI에서 불러오지 못했습니다. 네트워크 상태를 확인하거나 다시 검색해주세요.")

        if def_data:
            c1, c2 = st.columns([1, 2])
            with c1:
                if def_data["sprite"]:
                    st.image(def_data["sprite"], width=130)
                else:
                    st.caption("(이미지 없음)")
            with c2:
                st.subheader(def_data["name_ko"])
                st.write(" / ".join(all_types[t]["name_ko"] for t in def_data["types_en"] if t in all_types))

            st.session_state["def_level"] = st.slider("레벨", 1, 100, st.session_state["def_level"], key=f"def_level_slider_{ver}")

            with st.expander("세부 능력치 설정 (IV / EV / 성격 / 랭크)", expanded=True):
                valid_ev_def = stat_inputs("def", "def_iv", "def_ev", ver)
                nature_picker("def", "def_nature", nature_options, all_natures, ver)
                st.caption("능력치 랭크 (−6 ~ +6)")
                rc1, rc2, rc3 = st.columns(3)
                with rc1:
                    rank_control("방어", "def_rank", "defense", "def", ver)
                with rc2:
                    rank_control("특수방어", "def_rank", "special-defense", "def", ver)
                with rc3:
                    rank_control("스피드", "def_rank", "speed", "def", ver)

            with st.expander("특성 / 도구 / 기타 보정 / 날씨", expanded=False):
                st.session_state["def_ability_name_en"], _ = ability_section(
                    "def", def_data, "def_ability_name_en", "def_ability_manual_mult", None, None, ver
                )
                if st.session_state["use_items"] and ko_item_index:
                    item_picker("def", "def_item_name_en", ko_item_index, ver)
                else:
                    st.caption("도구 검색을 사용하려면 위의 '도구 설정 사용'을 체크하세요.")
                st.session_state["def_other_mult"] = st.number_input(
                    "기타 방어 보정 배율", 0.1, 5.0, st.session_state["def_other_mult"], step=0.1, key=f"def_other_mult_in_{ver}"
                )
                weather_label_val = st.selectbox(
                    "현재 날씨", list(WEATHER_OPTIONS.keys()),
                    index=list(WEATHER_OPTIONS.keys()).index(st.session_state["weather"]),
                    key=f"weather_select_{ver}",
                )
                st.session_state["weather"] = weather_label_val
        else:
            st.info("방어 포켓몬을 검색해서 선택해주세요.")

    st.divider()

    # ---------------- 계산 ----------------
    ready = atk_data and def_data and st.session_state.get("move_name_en") in move_details
    calc_clicked = st.button("🧮 계산하기", type="primary", disabled=not ready, use_container_width=True)

    if not ready:
        st.caption("공격/방어 포켓몬과 (위력이 있는) 기술을 모두 선택하면 계산할 수 있습니다.")
        return
    if not calc_clicked:
        return

    move = move_details[st.session_state["move_name_en"]]

    if move["damage_class"] == "status" or not move["power"]:
        st.warning("선택한 기술은 변화기이거나 위력이 없어 데미지를 계산할 수 없습니다. 위력이 있는 기술을 선택해주세요.")
        return

    weather_key = WEATHER_OPTIONS[st.session_state["weather"]]
    dmg_class = move["damage_class"]
    atk_stat_key = "attack" if dmg_class == "physical" else "special-attack"
    def_stat_key = "defense" if dmg_class == "physical" else "special-defense"

    atk_nature_info = all_natures[st.session_state["atk_nature"]]
    def_nature_info = all_natures[st.session_state["def_nature"]]

    # ---- 이제 기술이 확정되었으므로, 특성 자동 계산 지원 여부를 다시 판정해 표시한다 ----
    st.markdown("### 특성 적용 결과")
    fc1, fc2 = st.columns(2)
    with fc1:
        atk_ability_en, atk_ability_mult = ability_section(
            "atk_final", atk_data, "atk_ability_name_en", "atk_ability_manual_mult", dmg_class, weather_key, ver
        )
    with fc2:
        def_ability_en, def_ability_mult = ability_section(
            "def_final", def_data, "def_ability_name_en", "def_ability_manual_mult", dmg_class, weather_key, ver
        )

    # ---- 실능치 계산 ----
    atk_base_stat = calc_stat(atk_data["base_stats"][atk_stat_key], st.session_state["atk_iv"][atk_stat_key],
                               st.session_state["atk_ev"][atk_stat_key], st.session_state["atk_level"],
                               atk_stat_key, atk_nature_info)
    atk_rank_val = st.session_state["atk_rank"][atk_stat_key]
    atk_real_staged = rank_multiplier_value(atk_base_stat, atk_rank_val)

    def_base_stat = calc_stat(def_data["base_stats"][def_stat_key], st.session_state["def_iv"][def_stat_key],
                               st.session_state["def_ev"][def_stat_key], st.session_state["def_level"],
                               def_stat_key, def_nature_info)
    def_rank_val = st.session_state["def_rank"][def_stat_key]
    def_real_staged = rank_multiplier_value(def_base_stat, def_rank_val)

    def_weather_mult = weather_defense_multiplier(weather_key, def_data["types_en"], dmg_class)

    def_item_stat_mult = 1.0
    def_item_note = None
    if st.session_state["use_items"] and st.session_state.get("def_item_name_en") in DEF_ITEM_STAT_BOOST:
        boost_stat, boost_mult = DEF_ITEM_STAT_BOOST[st.session_state["def_item_name_en"]]
        if boost_stat == def_stat_key:
            def_item_stat_mult = boost_mult
            def_item_note = f"방어측 도구로 {STAT_KEY_KO[boost_stat]} ×{boost_mult} 적용"

    def_real_final = math.floor(def_real_staged * def_weather_mult * def_item_stat_mult)

    hp_real = calc_stat(def_data["base_stats"]["hp"], st.session_state["def_iv"]["hp"],
                         st.session_state["def_ev"]["hp"], st.session_state["def_level"],
                         "hp", None)

    # 참고용 물리/특수 내구력
    def_stat_for_phys = rank_multiplier_value(
        calc_stat(def_data["base_stats"]["defense"], st.session_state["def_iv"]["defense"],
                  st.session_state["def_ev"]["defense"], st.session_state["def_level"],
                  "defense", def_nature_info), st.session_state["def_rank"]["defense"])
    def_stat_for_spec = rank_multiplier_value(
        calc_stat(def_data["base_stats"]["special-defense"], st.session_state["def_iv"]["special-defense"],
                  st.session_state["def_ev"]["special-defense"], st.session_state["def_level"],
                  "special-defense", def_nature_info), st.session_state["def_rank"]["special-defense"])
    durability_phys = calc_naegurryeok(hp_real, def_stat_for_phys)
    durability_spec = calc_naegurryeok(hp_real, def_stat_for_spec)
    applied_durability = durability_phys if dmg_class == "physical" else durability_spec

    stab = 1.5 if is_stab(atk_data["types_en"], move["type_en"]) else 1.0
    type_mult = type_effectiveness(move["type_en"], def_data["types_en"], all_types)
    w_dmg_mult = weather_damage_multiplier(weather_key, move["type_en"])

    # ---- 공격측 도구 효과 ----
    atk_item_mult = 1.0
    atk_item_note = "도구 미사용"
    if st.session_state["use_items"] and st.session_state.get("atk_item_name_en"):
        auto_mult, supported, note = get_item_damage_effect(
            st.session_state["atk_item_name_en"], move["type_en"], dmg_class, type_mult
        )
        if supported:
            atk_item_mult = auto_mult
            atk_item_note = note
        else:
            st.session_state["atk_item_manual_mult"] = st.number_input(
                "선택한 도구는 자동 계산을 지원하지 않습니다 — 수동 배율 입력",
                0.1, 5.0, st.session_state["atk_item_manual_mult"], step=0.1, key=f"atk_item_manual_{ver}"
            )
            atk_item_mult = st.session_state["atk_item_manual_mult"]
            atk_item_note = note

    combined_modifier = (
        stab * type_mult * w_dmg_mult * atk_item_mult * atk_ability_mult
        * st.session_state["atk_other_mult"] * def_ability_mult * st.session_state["def_other_mult"]
    )

    juldjeongryeok = calc_juldjeongryeok(
        atk_real_staged, move["power"], stab,
        atk_item_mult * atk_ability_mult * st.session_state["atk_other_mult"]
    )

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

    # ---------------- 결과 화면 ----------------
    st.markdown("## 결과")
    st.markdown(f"### ⚔️ {atk_data['name_ko']} · {move['name_ko']}  →  🛡️ {def_data['name_ko']}")

    if analysis.get("guaranteed_n") == 1 and analysis.get("first_possible_n") == 1:
        st.success(f"## [ {hit_label} ]")
    elif analysis.get("first_possible_n") == 1:
        st.warning(f"## [ {hit_label} ]")
    else:
        st.info(f"## [ {hit_label} ]")

    st.caption(f"사용 공격 능력치: {STAT_KEY_KO[atk_stat_key]} {atk_real_staged:,}")

    r1, r2, r3 = st.columns(3)
    r1.metric("데미지", f"{min_dmg:,} ~ {max_dmg:,}")
    r2.metric("상대 HP", f"{hp_real:,}")
    r3.metric("HP 비율", f"{min_dmg/hp_real*100:.1f}% ~ {max_dmg/hp_real*100:.1f}%")

    r4, r5, r6 = st.columns(3)
    r4.metric("결정력 (참고용)", f"{juldjeongryeok:,.0f}")
    r5.metric(f"내구력 ({'물리' if dmg_class=='physical' else '특수'}, 참고용)", f"{applied_durability:,.0f}")
    r6.metric("타입 상성", f"×{type_mult}")

    if stab == 1.5:
        st.write("**자속(STAB)**: O — ×1.5")
    else:
        st.write("**자속(STAB)**: 없음")
    if st.session_state["use_items"] and st.session_state.get("atk_item_name_en"):
        st.write(f"**도구 효과**: {atk_item_note}")
    if def_item_note:
        st.write(f"**방어측 도구 효과**: {def_item_note}")

    st.caption("결정력·내구력은 커뮤니티에서 널리 쓰이는 '한쪽 스탯만 반영한 근사 참고 지표'이며, "
               "실제 데미지·타수 판정은 위의 데미지 공식으로 별도 계산됩니다.")

    if not analysis.get("impossible"):
        st.markdown("#### 타수별 확률 (85~100% 난수 기준)")
        for n, prob in analysis["results"]:
            st.progress(min(prob / 100, 1.0), text=f"{n}타 이내 처치 확률: {prob:.1f}%")
            if prob >= 100 - 1e-9:
                break
        st.caption("※ 위 확률은 기본 데미지 난수(85~100%, 16단계) 기준이며, 회피/급소/추가 상태이상 등 "
                   "다른 전투 변수는 반영하지 않았습니다.")

    with st.expander("📖 계산 과정 보기"):
        st.markdown("**공격측**")
        st.write(f"- 종족값 ({STAT_KEY_KO[atk_stat_key]}): {atk_data['base_stats'][atk_stat_key]}")
        st.write(f"- IV / EV: {st.session_state['atk_iv'][atk_stat_key]} / {st.session_state['atk_ev'][atk_stat_key]}")
        st.write(f"- 성격: {nature_label(atk_nature_info)}")
        st.write(f"- 성격 보정: ×{nature_multiplier(atk_nature_info, atk_stat_key)}")
        st.write(f"- 성격 적용 후: {atk_base_stat}")
        st.write(f"- 능력치 랭크: {'±0' if atk_rank_val == 0 else f'{atk_rank_val:+d}'}")
        st.write(f"- 랭크 보정: ×{(2+max(0,atk_rank_val))/(2-min(0,atk_rank_val)):.3f}")
        st.write(f"- 최종 실능치: {atk_real_staged}")

        st.markdown("**기술**")
        cls_ko = DAMAGE_CLASS_KO.get(dmg_class, dmg_class)
        st.write(f"- 위력: {move['power']} / 타입: {all_types[move['type_en']]['name_ko']} / 분류: {cls_ko}")
        st.write(f"- STAB: ×{stab}")
        st.write(f"- 타입 상성: ×{type_mult}")
        st.write(f"- 날씨 위력 보정: ×{w_dmg_mult}")
        st.write(f"- 특성 보정: ×{atk_ability_mult}")
        st.write(f"- 도구 보정: ×{atk_item_mult} ({atk_item_note})")
        st.write(f"- 기타 보정: ×{st.session_state['atk_other_mult']}")

        st.markdown("**방어측**")
        st.write(f"- HP 종족값/실능치: {def_data['base_stats']['hp']} / {hp_real}")
        st.write(f"- {STAT_KEY_KO[def_stat_key]} 종족값: {def_data['base_stats'][def_stat_key]}")
        st.write(f"- 성격: {nature_label(def_nature_info)}")
        st.write(f"- 성격 보정: ×{nature_multiplier(def_nature_info, def_stat_key)}")
        st.write(f"- 성격 적용 후: {def_base_stat}")
        st.write(f"- 능력치 랭크: {'±0' if def_rank_val == 0 else f'{def_rank_val:+d}'}")
        st.write(f"- 랭크 보정: ×{(2+max(0,def_rank_val))/(2-min(0,def_rank_val)):.3f}")
        st.write(f"- 날씨/도구로 인한 추가 방어 보정: ×{def_weather_mult * def_item_stat_mult:.2f}")
        st.write(f"- 특성 보정(방어측): ×{def_ability_mult}")
        st.write(f"- 최종 실능치: {def_real_final}")

        st.markdown("**최종**")
        st.write(f"- 합산 보정(난수 제외): ×{combined_modifier:.4f}")
        base_before_mod = base_power_damage(st.session_state["atk_level"], move["power"], atk_real_staged, def_real_final)
        st.write(f"- 난수 적용 전 기본 데미지: {base_before_mod}")
        st.write(f"- 난수 16단계 데미지: {rolls}")
        st.write(f"- 최소 데미지 비율: {min_dmg/hp_real*100:.1f}% / 최대 데미지 비율: {max_dmg/hp_real*100:.1f}%")
        st.write(f"- 결정력: {juldjeongryeok:,.0f}")
        st.write(f"- 내구력(적용): {applied_durability:,.0f}")
        st.write(f"- 타수 판정: {hit_label}")
        st.write(f"- 타수별 확률: " + ", ".join(f"{n}타 {p:.1f}%" for n, p in analysis["results"]))

    if not (valid_ev_atk and valid_ev_def):
        st.error("EV 총합이 510을 초과한 상태에서 계산되었습니다. 값을 수정 후 다시 계산해주세요.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 예상치 못한 오류가 발생해도 앱 전체가 완전히 종료되지 않고
        # 사용자에게 알아보기 쉬운 메시지를 보여준다.
        st.error("예상치 못한 오류가 발생했습니다. 새로고침 후 다시 시도해주세요.")
        st.exception(e)
