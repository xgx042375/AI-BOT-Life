"""联网搜索（Bing）：网页搜索 + 图片（表情包）搜索。

- web_search: 网页搜索，返回 [{title, url, snippet}]
- image_search: 图片搜索，返回图片直链列表（供表情包发送）
全部走 Bing（国内可达），无 API key；失败返回空 + 错误信息。
"""
import html
import logging
import re

import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote  # 2026-09-05 恢复：清理时误删（q 参数转义必需）

logger = logging.getLogger("search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 15.0
_IMAGE_MURL_RE = re.compile(r'murl&quot;:&quot;([^&]+)')


async def web_search(query: str, n: int = 5) -> tuple[list[dict], str]:
    """Bing 网页搜索。返回 (结果列表, 错误信息)。"""
    url = f"https://www.bing.com/search?q={quote(query)}&setlang=zh-hans&count={n}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo")[:n]:
            a = li.select_one("h2 a")
            if not a:
                continue
            p = li.select_one(".b_caption p") or li.select_one("p")
            results.append(
                {
                    "title": a.get_text(strip=True),
                    "url": a.get("href") or "",
                    "snippet": p.get_text(strip=True) if p else "",
                }
            )
        return results, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("bing web search failed: %s", e)
        return [], f"搜索失败: {type(e).__name__}"


async def image_search(query: str, n: int = 3) -> tuple[list[str], str]:
    """Bing 图片搜索，返回图片直链。"""
    url = f"https://www.bing.com/images/search?q={quote(query)}&form=HDRSC2"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        urls = []
        for m in _IMAGE_MURL_RE.findall(resp.text):
            u = html.unescape(m)
            if u.startswith(("http://", "https://")) and u not in urls:
                urls.append(u)
            if len(urls) >= n:
                break
        return urls, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("bing image search failed: %s", e)
        return [], f"图片搜索失败: {type(e).__name__}"


def format_results(results: list[dict], limit: int = 3) -> str:
    """格式化搜索结果供提示词注入。"""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results[:limit], 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet'][:120]}\n   {r['url']}")
    return "\n".join(lines)


async def web_search_en(query: str, n: int = 5) -> tuple[list[dict], str]:
    """Bing 网页搜索（英文市场，用于专名/类型查询）。"""
    url = f"https://www.bing.com/search?q={quote(query)}&mkt=en-US&setlang=en&cc=US&count={n}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo")[:n]:
            a = li.select_one("h2 a")
            if not a:
                continue
            p = li.select_one(".b_caption p") or li.select_one("p")
            results.append(
                {
                    "title": a.get_text(strip=True),
                    "url": a.get("href") or "",
                    "snippet": p.get_text(strip=True) if p else "",
                }
            )
        return results, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("bing en web search failed: %s", e)
        return [], f"搜索失败: {type(e).__name__}"


async def image_search_en(query: str, n: int = 3) -> tuple[list[str], str]:
    """Bing 图片搜索（英文市场，专名图片更准）。"""
    url = f"https://www.bing.com/images/search?q={quote(query)}&mkt=en-US&setlang=en&cc=US&form=HDRSC2"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        urls = []
        for m in _IMAGE_MURL_RE.findall(resp.text):
            u = html.unescape(m)
            if u.startswith(("http://", "https://")) and u not in urls:
                urls.append(u)
            if len(urls) >= n:
                break
        return urls, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("bing en image search failed: %s", e)
        return [], f"图片搜索失败: {type(e).__name__}"


# ---------------- 舰船档案查询（ibiblio hyperwar/USN） ----------------
IBIBLIO_BASE = "https://www.ibiblio.org/hyperwar/USN/ships/"
# 舷号前缀 -> 舰种页
SHIP_TYPE_PAGES = {
    "CVB": "ships-cv.html", "CV": "ships-cv.html", "CVL": "ships-cv.html", "CVE": "ships-cv.html",
    "BB": "ships-bb.html",
    "CB": "ships-ca.html", "CA": "ships-ca.html", "CL": "ships-ca.html",
    "DD": "ships-dd.html", "DE": "ships-de.html", "SS": "ships-ss.html", "DM": "ships-dm.html",
}
SHIP_TYPE_NAMES = {
    "CVB": "大型舰队航母", "CV": "舰队航母", "CVL": "轻型航母", "CVE": "护航航母",
    "BB": "战列舰", "CB": "大型巡洋舰", "CA": "重型巡洋舰", "CL": "轻型巡洋舰",
    "DD": "驱逐舰", "DE": "护航驱逐舰", "SS": "潜艇", "DM": "布雷舰",
}


def _ship_hull_prefix(en_name: str) -> str | None:
    """从英文名提取舷号前缀（CV-6 -> CV）。"""
    m = re.search(r"\b(CVB|CVL|CVE|CV|BB|CB|CA|CL|DD|DE|SS|DM)-\d+", en_name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _ship_keyword(en_name: str) -> str:
    """提取舰名主词（去 USS/舷号），用于链接匹配。"""
    name = re.sub(r"\(.*?\)", "", en_name)
    parts = [p for p in re.split(r"[\s\-/,]", name) if p and p.upper() != "USS" and not re.match(r"^(CVB|CVL|CVE|CV|BB|CB|CA|CL|DD|DE|SS|DM)-\d+$", p.upper())]
    if not parts:
        return ""
    # 主词取最长部分（舰名通常是最长单词）
    return max(parts, key=len)


async def ibiblio_ship_lookup(en_name: str) -> dict | None:
    """ibiblio 舰船档案查询：返回 {url, title, type_name, class_name} 或 None。

    流程：舷号前缀 -> 舰种页；Bing 搜所属级别（class）；抓舰种页匹配舰名。
    """
    key = _ship_keyword(en_name)
    if not key:
        return None
    prefix = _ship_hull_prefix(en_name)
    type_name = SHIP_TYPE_NAMES.get(prefix, "舰船")
    type_page = SHIP_TYPE_PAGES.get(prefix)
    if not type_page:
        return None  # 无舷号前缀（如外国舰），无法定位舰种页
    # Bing 搜所属级别（class）补充描述
    class_name = ""
    try:
        tres, _ = await web_search_en(f'"{en_name}" ship class', 2)
        joined = " ".join(r["title"] + " " + r["snippet"] for r in tres)
        cm = re.search(r"([A-Za-z]+)-class", joined)
        if cm:
            class_name = cm.group(1)
    except Exception:  # noqa: BLE001
        pass
    # 抓舰种页匹配舰名
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(IBIBLIO_BASE + type_page)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        key_l = key.lower()
        for a in soup.find_all("a"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if key_l in (href + " " + text).lower() and href.endswith(".html"):
                url = href if href.startswith("http") else IBIBLIO_BASE + href
                return {"url": url, "title": text or key, "type_name": type_name, "class_name": class_name}
    except Exception as e:  # noqa: BLE001
        logger.warning("ibiblio lookup failed: %s", e)
    return None


# 档案页正文清洗：hyperwar 为 90 年代风格 HTML，正文是大量 <p>/<h*> 纯文本
_DETAIL_LEN = 800    # 返回正文前段的最大字符数（控 token：引擎 slot ctx 有限，避免请求超限）
_DETAIL_MAX_PARAS = 8  # 最多取前 N 个非空段落


async def ibiblio_ship_detail(url: str) -> str:
    """抓取舰船档案页正文并清洗，返回前段文字摘要（供 LLM 直接汇总回答）。

    失败/空页返回空串（调用方回落到链接形式）。
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # 去掉导航/脚本/样式等噪音
        for tag in soup.find_all(["script", "style", "nav", "footer", "form"]):
            tag.decompose()
        paras: list[str] = []
        for block in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
            txt = block.get_text(" ", strip=True)
            if len(txt) >= 20:  # 跳过短碎片（导航链接等）
                paras.append(txt)
            if len(paras) >= _DETAIL_MAX_PARAS:
                break
        if not paras:
            # 兜底：整页纯文本
            raw = soup.get_text("\n", strip=True)
            paras = [ln for ln in raw.splitlines() if len(ln.strip()) >= 20][:_DETAIL_MAX_PARAS]
        summary = "\n".join(paras)[:_DETAIL_LEN]
        return summary.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("ibiblio detail failed: %s", e)
        return ""
