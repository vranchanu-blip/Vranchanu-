"""Работа с сайтом ANC (cetatenie.just.ro): поиск PDF, загрузка, извлечение номеров досаров.

Парсер намеренно НЕ привязан к колонкам таблиц: он ищет в тексте PDF номера вида
«12345/RD/2021» и сохраняет строку, в которой номер встретился. Так бот продолжает
работать, даже если ANC поменяет вёрстку таблиц.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from pypdf import PdfReader

# Номер досара по ст. 11: «12345/RD/2021». Допускаем пробелы вокруг «/».
DOSAR_RE = re.compile(r"(?<!\d)(\d{1,6})\s*/\s*RD\s*/\s*((?:19|20)\d{2})(?!\d)", re.IGNORECASE)

_LINK_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

MAX_LINE = 220

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def normalize_number(raw: str) -> str | None:
    """'12345 / rd / 2021' -> '12345/RD/2021'; None, если формат не распознан."""
    m = DOSAR_RE.search(raw.strip())
    if not m:
        return None
    return f"{int(m.group(1))}/RD/{m.group(2)}"


def number_year(number: str) -> int:
    return int(number.rsplit("/", 1)[1])


def number_seq(number: str) -> int:
    return int(number.split("/", 1)[0])


@dataclass(frozen=True)
class PdfLink:
    url: str
    title: str
    source_page: str

    @property
    def kind(self) -> str:
        return classify(self.url, self.title, self.source_page)


def classify(url: str, title: str, source_page: str) -> str:
    text = f"{url} {title}".lower()
    if "ordin" in text:
        return "ordin"
    if "stadiu" in text or "stadiu" in source_page.lower():
        return "stadiu"
    if "ordin" in source_page.lower():
        return "ordin"
    return "other"


def extract_pdf_links(page_html: str, base_url: str, url_filter: str = "") -> list[PdfLink]:
    """Все ссылки на .pdf со страницы (без дублей), с текстом ссылки как заголовком."""
    flt = re.compile(url_filter, re.IGNORECASE) if url_filter else None
    seen: set[str] = set()
    links: list[PdfLink] = []
    for href, inner in _LINK_RE.findall(page_html):
        href = html.unescape(href).strip()
        if ".pdf" not in href.lower():
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", inner))).strip()
        if flt and not (flt.search(url) or flt.search(title)):
            continue
        seen.add(url)
        links.append(PdfLink(url=url, title=title or url.rsplit("/", 1)[-1], source_page=base_url))
    return links


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _snippet(line: str, start: int, end: int) -> str:
    if len(line) <= MAX_LINE:
        return line
    half = (MAX_LINE - (end - start)) // 2
    lo = max(0, start - half)
    hi = min(len(line), end + half)
    return ("…" if lo > 0 else "") + line[lo:hi].strip() + ("…" if hi < len(line) else "")


def find_mentions(text: str) -> dict[str, set[str]]:
    """{номер: {строки, где он встретился}}."""
    found: dict[str, set[str]] = {}
    for raw_line in text.splitlines():
        line = _WS_RE.sub(" ", raw_line).strip()
        if not line:
            continue
        for m in DOSAR_RE.finditer(line):
            number = f"{int(m.group(1))}/RD/{m.group(2)}"
            found.setdefault(number, set()).add(_snippet(line, m.start(), m.end()))
    return found


class AncClient:
    """HTTP-клиент к сайту ANC. Поддерживает прокси (сайт может не отвечать зарубежным IP)."""

    def __init__(self, proxy: str | None = None, timeout: float = 60.0):
        # Адрес посредника (например, Cloudflare Worker): запросы идут на relay?url=<адрес ANC>.
        self.relay: str | None = None
        self.proxy = proxy
        self._timeout = timeout
        self._client = self._make_client(proxy)

    def _make_client(self, proxy: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=proxy,
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ro,en;q=0.8"},
        )

    async def set_proxy(self, proxy: str | None) -> None:
        """Сменить прокси на лету (например, после запуска VPN)."""
        if proxy == self.proxy:
            return
        old, self._client, self.proxy = self._client, self._make_client(proxy), proxy
        await old.aclose()

    def _via_relay(self, url: str) -> str:
        if not self.relay:
            return url
        return str(httpx.URL(self.relay).copy_merge_params({"url": url}))

    async def get_text(self, url: str) -> str:
        resp = await self._client.get(self._via_relay(url))
        resp.raise_for_status()
        return resp.text

    async def get_bytes(self, url: str) -> bytes:
        resp = await self._client.get(self._via_relay(url))
        resp.raise_for_status()
        return resp.content

    async def close(self) -> None:
        await self._client.aclose()
