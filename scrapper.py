"""
Crawler implementation
"""
import json
import os
import re
import time
import random
import shutil
import logging.config

from json.decoder import JSONDecodeError
from collections import namedtuple
from datetime import datetime
from typing import Set, List, Tuple, Optional, Dict, Any
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests import RequestException

from constants import CRAWLER_CONFIG_PATH, HEADERS, COOKIES, ASSETS_PATH, CRAWLER_STATE, PARSER_STATE
from article import Article

logging.config.fileConfig(fname='crawler_logging.ini', disable_existing_loggers=False)
log = logging.getLogger(__name__)


class IncorrectURLError(Exception):
    """
    Custom error
    """


class NumberOfArticlesOutOfRangeError(Exception):
    """
    Custom error
    """


class IncorrectNumberOfArticlesError(Exception):
    """
    Custom error
    """


class UnknownConfigError(Exception):
    """
    Most general error
    """


class Crawler:
    """
    Crawler implementation
    """
    seed_urls: List[str]
    max_articles: int
    max_articles_per_seed: int
    urls: Set[str]

    def __init__(self, seed_urls: list, max_articles: int, max_articles_per_seed: int):
        self.seed_urls = seed_urls
        self.max_articles = max_articles
        self.max_articles_per_seed = max_articles_per_seed
        self.urls = set()

    def find_articles(self):
        """
        Finds articles
        """
        for url in self.get_search_urls():

            if page := fetch_page(url) is None:
                continue

            links = self._process_seed(page)
            self.urls.update(links)

            log.info("Seed page '%s' is processed.", url)

            time.sleep(random.uniform(1, 3))

            if len(self.urls) == self.max_articles:
                return

    def get_search_urls(self) -> List[str]:
        """
        Returns seed_urls param
        """
        return self.seed_urls

    @staticmethod
    def _extract_url(article_bs: BeautifulSoup) -> List[str]:
        """
        Extracts news urls from the seed page
        """
        news = article_bs.select('div.col-md-9 div.col-md-6 h2 a')
        links = [article.attrs['href'] for article in news]

        return links

    def _process_seed(self, page: str):
        """
        Extracts specified number of links from the seed page and returns them
        """
        soup = BeautifulSoup(page, 'lxml')

        seed_articles = self._extract_url(soup)[:self.max_articles_per_seed]
        links = seed_articles[:self.max_articles - len(self.urls)]

        return links


class CrawlerRecursive(Crawler):
    """
    Recursive Crawler
    """
    def find_articles(self):
        """
        Finds articles
        """
        self._load_state()

        seed, self.seed_urls[0] = self.get_search_urls()
        page = fetch_page(seed)
        links = self._process_seed(page)
        self.urls.update(links)

        if len(self.urls) == self.max_articles:
            return

        self._save_state()

        log.info("Seed page '%s' is processed.", seed)

        time.sleep(random.uniform(1, 3))

        return self.find_articles()

    def get_search_urls(self) -> Tuple[str, str]:
        """
        Gets current and next seed pages
        """
        seed = self.seed_urls[0]
        page = fetch_page(seed)

        soup = BeautifulSoup(page, 'lxml')
        next_page = soup.select_one('ul.pagination li:has(span.current) + li a')

        return seed, next_page['href']

    def _save_state(self) -> None:
        with open(CRAWLER_STATE, "w", encoding='utf-8') as file:
            json.dump(
                {
                    'urls': sorted(self.urls),
                    'seed': self.seed_urls
                },
                file,
                indent=4
            )

    def _load_state(self) -> None:
        if os.path.exists(CRAWLER_STATE):
            with open(CRAWLER_STATE, encoding='utf-8') as file:
                status: Dict[str, Any] = json.load(file)

            self.seed_urls, self.urls = status['seed'], set(status['urls'])


class ArticleParser:
    """
    ArticleParser implementation
    """
    article: Article
    full_url: str
    article_id: int

    def __init__(self, full_url: str, article_id: int):
        self.full_url = full_url
        self.article_id = article_id
        self.article = Article(full_url, article_id)

    @staticmethod
    def unify_date_format(date_str: str) -> datetime:
        """
        Unifies date format
        """
        return datetime.fromisoformat(date_str)

    def parse(self) -> None:
        """
        Parses each article
        """
        if self.full_url in self._load_state():
            return

        page = fetch_page(self.full_url)
        soup = BeautifulSoup(page, features='lxml')

        self._fill_article_with_text(soup)
        self._fill_article_with_meta_information(soup)

        self._save_state()

    def save(self):
        self.article.save_raw()

    def _fill_article_with_text(self, article_soup: BeautifulSoup) -> None:
        self.article.text = '\n'.join([p.text for p in article_soup.select('.entry-content > p')])

    def _fill_article_with_meta_information(self, article_soup: BeautifulSoup) -> None:
        self.article.title = article_soup.select_one('h1').text
        self.article.author = article_soup.select_one('li.autor').text

        date: str = article_soup.select_one('time.entry-date').attrs['datetime']
        self.article.date = self.unify_date_format(date)

        self.article.topics = [tag.text for tag in article_soup.select('a[rel^="category"]')]

    def _save_state(self):
        if not os.path.exists(PARSER_STATE):
            with open(PARSER_STATE, 'w', encoding='utf-8') as file:
                file.write('{"processed": []}')

        with open(PARSER_STATE, encoding='utf-8') as file:
            state = json.load(file)
            state['processed'].append(self.article.url)

        with open(PARSER_STATE, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=4)

    @staticmethod
    def _load_state():
        if os.path.exists(PARSER_STATE):
            with open(PARSER_STATE, encoding='utf-8') as file:
                state: Dict[str, list] = json.load(file)
            return set(state['processed'])
        return {}


def fetch_page(url: str) -> Optional[str]:
    """
    Fetches the seed page and returns its content
    """
    try:
        response = requests.get(url, headers=HEADERS, cookies=COOKIES)
        response.raise_for_status()
        return response.text

    except (RequestException, ConnectionError) as exc:
        log.exception("%s was encountered while getting seed_urls", exc.__class__.__name__)


def prepare_environment(base_path: str) -> None:
    """
    Creates ASSETS_PATH folder if not created and removes existing folder
    """
    shutil.rmtree(base_path, ignore_errors=True)
    Path(base_path).mkdir(parents=True, exist_ok=True)


def validate_config(crawler_path: str) -> Tuple[List[str], int, int]:
    """
    Validates given config
    """
    with open(crawler_path) as file:
        config: dict = json.load(file)

    Check = namedtuple('Check', ['status', 'error'])

    try:
        total_num = config['total_articles_to_find_and_parse']
        url_check = re.compile(r'^(https?://)?[0-9a-z]+\.[-_0-9a-z]+\.[0-9a-z/]+', re.I)

        is_correct_total_num = isinstance(total_num, int)
        is_correct_url = all(url_check.match(str(x)) for x in config['base_urls'])
        is_num_not_oor = is_correct_total_num and 0 < total_num <= 100000

        checks = (
            Check(is_correct_url, IncorrectURLError),
            Check(is_correct_total_num, IncorrectNumberOfArticlesError),
            Check(is_num_not_oor, NumberOfArticlesOutOfRangeError),
        )

        for check in checks:
            if not check.status:
                raise check.error('Could not check config.')

        return (
            config['base_urls'],
            config['total_articles_to_find_and_parse'],
            config['max_number_articles_to_get_from_one_seed']
        )

    except (JSONDecodeError, KeyError) as exc:
        log.exception("%s was encountered while validating crawler config.", exc.__class__.__name__)
        raise UnknownConfigError from exc


def main():
    urls, max_num_articles, max_per_seed = validate_config(CRAWLER_CONFIG_PATH)
    crawler = CrawlerRecursive(seed_urls=urls, max_articles=max_num_articles, max_articles_per_seed=max_per_seed)

    if not os.path.exists(ASSETS_PATH):
        prepare_environment(ASSETS_PATH)

    crawler.find_articles()

    for idx, article_url in enumerate(sorted(crawler.urls)):
        parser = ArticleParser(full_url=article_url, article_id=idx)

        time.sleep(random.uniform(2, 4))

        parser.parse()
        parser.save()

        log.info("Article #%s '%s' is processed.", idx, article_url)

    log.info("Total: %s articles.", len(crawler.urls))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log.exception(exc)
