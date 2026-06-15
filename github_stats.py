#!/usr/bin/env python3

import asyncio
import logging
from typing import Dict, List, Optional, Set

import aiohttp


###############################################################################
# Logging
###############################################################################

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


###############################################################################
# Queries
###############################################################################

class Queries:
    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        max_connections: int = 3,
    ):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def query(self, generated_query: str) -> Dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/vnd.github+json",
        }

        async with self.semaphore:
            async with self.session.post(
                "https://api.github.com/graphql",
                headers=headers,
                json={"query": generated_query},
            ) as response:

                response.raise_for_status()

                result = await response.json()

                if "errors" in result:
                    logger.error(f"GraphQL API errors: {result['errors']}")

                return result

    async def query_rest(
        self,
        path: str,
        params: Optional[Dict] = None,
    ) -> Dict:

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/vnd.github+json",
        }

        if params is None:
            params = {}

        if path.startswith("/"):
            path = path[1:]

        url = f"https://api.github.com/{path}"

        for attempt in range(8):
            try:
                async with self.semaphore:
                    async with self.session.get(
                        url,
                        headers=headers,
                        params=params,
                    ) as response:

                        if response.status == 202:
                            logger.warning(
                                f"GitHub is preparing statistics for {path} "
                                f"(attempt {attempt + 1}/8)"
                            )
                            await asyncio.sleep(5)
                            continue

                        if response.status == 404:
                            logger.warning(f"Resource not found: {path}")
                            return {}

                        if response.status == 403:
                            logger.error(
                                f"Access forbidden or rate limited for: {path}"
                            )
                            return {}

                        response.raise_for_status()

                        return await response.json()

            except asyncio.TimeoutError:
                logger.warning(f"Timeout while requesting: {path}")

            except aiohttp.ClientError as error:
                logger.error(f"GitHub REST API error for {path}: {error}")

            except Exception as error:
                logger.error(f"Unexpected error for {path}: {error}")

            await asyncio.sleep(2)

        logger.warning(f"Skipping endpoint after retries: {path}")

        return {}

    ############################################################################
    # GraphQL Queries
    ############################################################################

    @staticmethod
    def repos_overview() -> str:
        return """
{
  viewer {
    login
    name

    repositories(
      first: 100
      ownerAffiliations: OWNER
      isFork: false
      orderBy: {
        field: UPDATED_AT
        direction: DESC
      }
    ) {
      nodes {
        nameWithOwner

        stargazers {
          totalCount
        }

        forkCount

        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size

            node {
              name
              color
            }
          }
        }
      }
    }
  }
}
"""

    @staticmethod
    def contrib_years() -> str:
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        return f'''
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
'''

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        by_years = "\n".join(map(cls.contribs_by_year, years))

        return f'''
query {{
  viewer {{
    {by_years}
  }}
}}
'''


###############################################################################
# Stats
###############################################################################

class Stats:
    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        exclude_repos: Optional[Set] = None,
        exclude_langs: Optional[Set] = None,
        consider_forked_repos: bool = False,
    ):

        self.username = username

        self._exclude_repos = exclude_repos or set()
        self._exclude_langs = exclude_langs or set()
        self._consider_forked_repos = consider_forked_repos

        self.queries = Queries(
            username,
            access_token,
            session,
        )

        self._name = None
        self._stargazers = None
        self._forks = None
        self._total_contributions = None
        self._languages = None
        self._repos = None
        self._views = None

    async def get_stats(self) -> None:
        logger.info("Loading GitHub repository statistics")

        self._stargazers = 0
        self._forks = 0
        self._languages = {}
        self._repos = set()

        raw_results = await self.queries.query(
            Queries.repos_overview()
        )

        viewer = raw_results.get("data", {}).get("viewer", {})

        self._name = viewer.get("name") or viewer.get("login")

        repositories = (
            viewer
            .get("repositories", {})
            .get("nodes", [])
        )

        logger.info(f"Found {len(repositories)} repositories")

        for repo in repositories:
            repo_name = repo.get("nameWithOwner")

            if not repo_name:
                continue

            if repo_name in self._exclude_repos:
                logger.info(f"Skipping excluded repository: {repo_name}")
                continue

            logger.info(f"Processing repository: {repo_name}")

            self._repos.add(repo_name)

            self._stargazers += (
                repo
                .get("stargazers", {})
                .get("totalCount", 0)
            )

            self._forks += repo.get("forkCount", 0)

            for lang in repo.get("languages", {}).get("edges", []):
                lang_name = (
                    lang
                    .get("node", {})
                    .get("name", "Other")
                )

                if lang_name in self._exclude_langs:
                    continue

                if lang_name not in self._languages:
                    self._languages[lang_name] = {
                        "size": 0,
                        "occurrences": 0,
                        "color": (
                            lang
                            .get("node", {})
                            .get("color")
                        ),
                    }

                self._languages[lang_name]["size"] += (
                    lang.get("size", 0)
                )

                self._languages[lang_name]["occurrences"] += 1

        total_language_size = sum(
            value["size"]
            for value in self._languages.values()
        )

        if total_language_size > 0:
            for value in self._languages.values():
                value["prop"] = (
                    100 * value["size"] / total_language_size
                )

        logger.info("Repository statistics loaded successfully")

    @property
    async def name(self) -> str:
        if self._name is None:
            await self.get_stats()

        return self._name

    @property
    async def stargazers(self) -> int:
        if self._stargazers is None:
            await self.get_stats()

        return self._stargazers

    @property
    async def forks(self) -> int:
        if self._forks is None:
            await self.get_stats()

        return self._forks

    @property
    async def languages(self) -> Dict:
        if self._languages is None:
            await self.get_stats()

        return self._languages

    @property
    async def repos(self):
        if self._repos is None:
            await self.get_stats()

        return self._repos

    @property
    async def all_repos(self):
        if self._repos is None:
            await self.get_stats()

        return self._repos

    @property
    async def total_contributions(self) -> int:
        if self._total_contributions is not None:
            return self._total_contributions

        logger.info("Loading contribution statistics")

        self._total_contributions = 0

        years = (
            await self.queries.query(
                Queries.contrib_years()
            )
        ).get("data", {}).get("viewer", {}).get(
            "contributionsCollection",
            {},
        ).get("contributionYears", [])

        by_year = (
            await self.queries.query(
                Queries.all_contribs(years)
            )
        ).get("data", {}).get("viewer", {}).values()

        for year in by_year:
            self._total_contributions += (
                year
                .get("contributionCalendar", {})
                .get("totalContributions", 0)
            )

        logger.info(
            f"Total contributions: {self._total_contributions}"
        )

        return self._total_contributions

    @property
    async def views(self) -> int:
        if self._views is not None:
            return self._views

        logger.info("Loading repository traffic statistics")

        total_views = 0

        for repo in await self.repos:
            logger.info(f"Fetching views for {repo}")

            result = await self.queries.query_rest(
                f"/repos/{repo}/traffic/views"
            )

            if not isinstance(result, dict):
                continue

            for view in result.get("views", []):
                total_views += view.get("count", 0)

        self._views = total_views

        logger.info(f"Total repository views: {total_views}")

        return total_views