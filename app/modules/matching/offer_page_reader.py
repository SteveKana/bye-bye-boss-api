"""Read the text of a job offer from its web page (admin tool).

The admin pastes a link instead of the whole announcement. We fetch the page
ourselves, once, on demand, and hand the text back so the admin can check it
before anything is sent to the model. Nothing is stored and nothing runs on a
schedule.

Three things this module is careful about:

* **No server-side request forgery.** The link is typed by a person, but the
  fetch runs from our server, so every hop (including redirects) must resolve
  to a public address -- never localhost, a private network or a cloud
  metadata address.
* **Sites that say no, stay no.** A few big sites forbid automated reading in
  their terms (or put the offer behind a login), so we don't even try them,
  and a 401/403/429 from any other site is reported as "this site blocks
  reading" rather than worked around. The caller falls back to pasting text.
* **We say who we are.** Plain, honest User-Agent; no browser disguise.

Extraction prefers the structured `JobPosting` data many offer pages embed for
search engines (clean title/company/place/description); otherwise it takes the
page's main text. A page that is empty without JavaScript is reported as such.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
TIMEOUT_SECONDS = 15
MIN_TEXT_CHARS = 200
MAX_TEXT_CHARS = 20_000
# ASCII only: HTTP header values can't carry accents.
USER_AGENT = "ByeByeBoss/1.0 (admin tool, reads one offer page on demand)"

# Sites whose terms forbid automated reading, or that hide the offer behind a
# login: not attempted at all.
NO_AUTOMATION_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "indeed.fr",
    "glassdoor.com",
    "glassdoor.fr",
    "welcometothejungle.com",
)


class OfferPageError(Exception):
    """The page can't be turned into an offer text. `str(e)` is shown to the
    admin as is, in French, and always ends by what to do instead."""


@dataclass(frozen=True)
class OfferPage:
    text: str
    title: str | None
    host: str
    used_structured_data: bool


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [str(info[4][0]) for info in infos]


async def _check_url(url: str) -> str:
    """Return a clean http(s) URL, or raise OfferPageError."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise OfferPageError(
            "Ce lien n'est pas valide : colle l'adresse complète de l'offre."
        )
    host = parsed.hostname.lower()
    if _host_matches(host, NO_AUTOMATION_HOSTS):
        raise OfferPageError(
            "Ce site n'autorise pas la lecture automatique de ses offres : "
            "colle le texte de l'annonce."
        )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise OfferPageError("Ce lien n'est pas autorisé : colle le texte de l'offre.")
    try:
        addresses = await _resolve(host, port)
    except socket.gaierror:
        raise OfferPageError(
            "Je ne trouve pas ce site : vérifie le lien, ou colle le texte de l'offre."
        ) from None
    if not addresses or not all(_is_public(a) for a in addresses):
        raise OfferPageError("Ce lien n'est pas autorisé : colle le texte de l'offre.")
    return url


async def _fetch_html(url: str) -> tuple[str, str]:
    """Fetch the page, following a few redirects by hand so that each hop is
    checked. Returns (final_url, html)."""
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=TIMEOUT_SECONDS,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        },
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            url = await _check_url(url)
            try:
                async with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    status = response.status_code
                    if status in (401, 403, 429, 451):
                        raise OfferPageError(
                            "Ce site bloque la lecture automatique : "
                            "colle le texte de l'annonce."
                        )
                    if status in (404, 410):
                        raise OfferPageError(
                            "Cette offre est introuvable (elle a peut-être expiré) : "
                            "vérifie le lien."
                        )
                    if status >= 400:
                        raise OfferPageError(
                            f"Le site a répondu une erreur ({status}) : "
                            "colle le texte de l'annonce."
                        )
                    kind = response.headers.get("content-type", "").lower()
                    if "html" not in kind and "xml" not in kind:
                        raise OfferPageError(
                            "Ce lien ne mène pas à une page web : "
                            "colle le texte de l'annonce."
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            break
                    encoding = response.encoding or "utf-8"
                    return url, bytes(body).decode(encoding, errors="replace")
            except httpx.HTTPError:
                raise OfferPageError(
                    "Le site ne répond pas : réessaie, ou colle le texte de l'annonce."
                ) from None
    raise OfferPageError(
        "Ce lien fait trop de redirections : colle le texte de l'annonce."
    )


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "html.parser")
    for item in soup.find_all("li"):
        item.insert_before("\n- ")
    for br in soup.find_all(["br", "p", "div", "h1", "h2", "h3", "h4"]):
        br.insert_after("\n")
    return _clean(soup.get_text())


def _job_postings(soup: BeautifulSoup) -> list[dict]:
    found: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                found.append(node)
            walk(node.get("@graph"))

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or tag.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return found


def _place(posting: dict) -> str:
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    address = (location or {}).get("address") if isinstance(location, dict) else None
    if isinstance(address, dict):
        parts = [address.get("addressLocality"), address.get("addressRegion")]
        return ", ".join(p for p in parts if isinstance(p, str) and p)
    return ""


def _from_structured_data(soup: BeautifulSoup) -> tuple[str, str] | None:
    for posting in _job_postings(soup):
        description = _html_to_text(str(posting.get("description") or ""))
        if len(description) < MIN_TEXT_CHARS:
            continue
        title = str(posting.get("title") or "").strip()
        org = posting.get("hiringOrganization")
        company = (org.get("name") if isinstance(org, dict) else org) or ""
        head = [
            title,
            " — ".join(p for p in (str(company).strip(), _place(posting)) if p),
        ]
        return "\n".join(p for p in head if p) + "\n\n" + description, title
    return None


def _from_page_text(soup: BeautifulSoup) -> tuple[str, str]:
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = _clean(h1.get_text(" "))
    elif soup.title and soup.title.string:
        title = _clean(soup.title.string)
    for tag in soup.find_all(
        ["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]
    ):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for item in root.find_all("li"):
        item.insert_before("\n- ")
    for block in root.find_all(["br", "p", "div", "h1", "h2", "h3", "h4", "section"]):
        block.insert_after("\n")
    return _clean(root.get_text()), title


def extract_offer_text(html: str) -> tuple[str, str | None, bool]:
    """(text, title, used_structured_data). Raises OfferPageError when the page
    holds no readable offer."""
    soup = BeautifulSoup(html, "html.parser")
    structured = _from_structured_data(soup)
    if structured:
        text, title, used = structured[0], structured[1], True
    else:
        text, title = _from_page_text(soup)
        used = False
    text = text[:MAX_TEXT_CHARS].strip()
    if len(text) < MIN_TEXT_CHARS:
        raise OfferPageError(
            "Je n'arrive pas à lire l'offre sur cette page (elle s'affiche "
            "peut-être seulement dans un navigateur) : colle le texte de l'annonce."
        )
    return text, (title or None), used


async def read_offer_from_url(url: str) -> OfferPage:
    final_url, html = await _fetch_html(url)
    text, title, used = extract_offer_text(html)
    return OfferPage(
        text=text,
        title=title,
        host=(urlparse(final_url).hostname or "").lower(),
        used_structured_data=used,
    )
