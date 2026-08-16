"""
advanced_crawler.py — Robust recursive link discoverer for uoli.edu.pk
=======================================================================
Discovers all internal HTML pages on the university website.
Used by setup_knowledge_base.py to find pages beyond the static list.
"""
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import Set, List
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patterns to never crawl
_SKIP_PATTERNS = re.compile(
    r"\.(pdf|jpg|jpeg|png|gif|zip|doc|docx|xlsx|mp4|mp3|webp|svg|ico)$"
    r"|wp-content|wp-includes|wp-json|wp-admin|#|javascript:|mailto:|tel:"
    r"|instagram\.com|facebook\.com|linkedin\.com|twitter\.com|youtube\.com",
    re.IGNORECASE,
)


class UniversityCrawler:
    def __init__(self, base_url: str, allowed_domain: str):
        self.base_url = base_url.rstrip("/")
        self.allowed_domain = allowed_domain
        self.visited_urls: Set[str] = set()
        self.urls_to_visit: Set[str] = {self.base_url}
        self.found_urls: List[str] = []

    def _make_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        return session

    def _is_internal(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.netloc == self.allowed_domain or parsed.netloc == ""

    def _normalise(self, url: str) -> str:
        parsed = urlparse(url)
        # Drop query strings and fragments, normalise trailing slash
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        return clean

    def crawl(self, max_pages: int = 300) -> List[str]:
        session = self._make_session()
        count = 0

        while self.urls_to_visit and count < max_pages:
            current_url = self.urls_to_visit.pop()
            normalised = self._normalise(current_url)

            # Skip already visited, non-webpage, or pattern-blocked URLs
            if normalised in self.visited_urls:
                continue
            if _SKIP_PATTERNS.search(normalised):
                continue

            logger.info(f"[{count+1}] Crawling: {normalised}")
            self.visited_urls.add(normalised)
            self.found_urls.append(normalised)
            count += 1

            try:
                response = session.get(normalised, timeout=30)
                response.raise_for_status()

                if "text/html" not in response.headers.get("Content-Type", ""):
                    continue

                soup = BeautifulSoup(response.text, "html.parser")

                for a_tag in soup.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    if not href or href.startswith("#"):
                        continue

                    full_url = urljoin(normalised, href)

                    if not self._is_internal(full_url):
                        continue
                    if _SKIP_PATTERNS.search(full_url):
                        continue

                    norm = self._normalise(full_url)
                    if norm not in self.visited_urls:
                        self.urls_to_visit.add(norm)

                time.sleep(0.5)  # polite crawl delay

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout: {normalised}")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Connection error: {normalised} — {e}")
            except requests.exceptions.HTTPError as e:
                logger.warning(f"HTTP {e.response.status_code}: {normalised}")
            except Exception as e:
                logger.error(f"Unexpected error crawling {normalised}: {e}")

        logger.info(f"Crawl complete. Found {len(self.found_urls)} pages.")
        return self.found_urls


if __name__ == "__main__":
    crawler = UniversityCrawler("https://uoli.edu.pk/", "uoli.edu.pk")
    links = crawler.crawl(max_pages=300)
    print(f"\nFound {len(links)} links:")
    for link in sorted(links):
        print(link)
