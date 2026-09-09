"""Explicit mechanical normalization preserves technical details and link targets."""

import json
import re
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from protocol_intel.config import EXTRACTOR_VERSION as EXTRACTOR_VERSION
from protocol_intel.config import Source, canonical, public_url


def is_html(body: str, content_type: str) -> bool:
    return "html" in content_type.lower() or bool(
        re.search(r"<!doctype\s+html|<html\b", body[:1000], re.I)
    )


def markdown_candidates(body: str, base: str, allowed_hosts: list[str]) -> list[str]:
    tree = html.fromstring(body)
    hosts = {urlsplit(base).hostname, *allowed_hosts}
    current = urlsplit(base)
    path = current.path.rstrip("/")
    equivalent_paths = {path + ".md", path + "/index.md"}
    result = []
    for node in tree.xpath("//a[@href] | //link[@href]"):
        target = urljoin(base, node.get("href"))
        parts = urlsplit(target)
        # A neighboring Markdown page is not a representation of this page.
        explicit_alternate = (
            node.tag == "link"
            and "alternate" in node.get("rel", "").lower().split()
            and node.get("type", "").lower() == "text/markdown"
        )
        same_page = parts.path in equivalent_paths and parts.query == current.query
        if (
            parts.hostname in hosts
            and parts.path.endswith(".md")
            and (same_page or explicit_alternate)
        ):
            result.append(public_url(target))
    return list(dict.fromkeys(result))[:5]


def normalize(body: str, content_type: str, source: Source, final_url: str) -> str:
    if source.kind == "json":
        return canonical(json.loads(body)) + "\n"
    if is_html(body, content_type):
        tree = html.fromstring(body)
        # Keep machine-readable embedded configuration and script identities as evidence.
        extras = []
        for script in tree.xpath("//script"):
            if script.get("src"):
                extras.append("SCRIPT " + urljoin(final_url, script.get("src")))
            if "json" in script.get("type", "") and script.text:
                extras.append("EMBEDDED_JSON " + script.text)
        for node in tree.xpath("//script | //style"):
            node.drop_tree()
        for node in tree.xpath("//a[@href]"):
            node.tail = " <" + urljoin(final_url, node.get("href")) + "> " + (node.tail or "")
        # Cell boundaries are evidence: [a, bc] must not collapse to the same text as [ab, c].
        for node in tree.xpath("//td | //th"):
            node.tail = "\t" + (node.tail or "")
        for node in tree.xpath("//h1 | //h2 | //h3 | //p | //li | //tr | //pre | //br | //div"):
            node.tail = "\n" + (node.tail or "")
        text = tree.text_content() + "\n" + "\n".join(extras)
    else:
        text = body
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
    patterns = [re.compile(rule) for rule in source.ignore_patterns]
    lines = [line for line in lines if not any(rule.fullmatch(line.strip()) for rule in patterns)]
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not result:
        raise ValueError("Empty extraction; previous content must be preserved")
    return result + "\n"


def sitemap_entries(body: bytes) -> tuple[str, list[str]]:
    # External entities are not part of a sitemap inventory.
    root = etree.fromstring(body, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    kind = etree.QName(root).localname
    if kind not in {"urlset", "sitemapindex"}:
        raise ValueError("Response is not a sitemap")
    urls = [public_url(v) for v in root.xpath("//*[local-name()='loc']/text()")]
    return kind, urls
