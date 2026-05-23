#!/usr/bin/env python3

import asyncio
import logging
import os
import re

import aiohttp

from github_stats import Stats


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


################################################################################
# Helper Functions
################################################################################


def generate_output_folder() -> None:
    if not os.path.isdir("generated"):
        os.mkdir("generated")
        logger.info("Created generated/ folder")


################################################################################
# SVG Generation
################################################################################


async def generate_overview(stats: Stats) -> None:
    logger.info("Generating overview.svg")

    with open("templates/overview.svg", "r", encoding="utf-8") as f:
        output = f.read()

    output = re.sub("{{ name }}", await stats.name, output)
    output = re.sub("{{ stars }}", f"{await stats.stargazers:,}", output)
    output = re.sub("{{ forks }}", f"{await stats.forks:,}", output)
    output = re.sub(
        "{{ contributions }}",
        f"{await stats.total_contributions:,}",
        output,
    )

    # Disabled unstable contributor stats endpoint
    output = re.sub("{{ lines_changed }}", "N/A", output)

    output = re.sub("{{ views }}", f"{await stats.views:,}", output)
    output = re.sub("{{ repos }}", f"{len(await stats.all_repos):,}", output)

    generate_output_folder()

    with open("generated/overview.svg", "w", encoding="utf-8") as f:
        f.write(output)

    logger.info("overview.svg generated successfully")


async def generate_languages(stats: Stats) -> None:
    logger.info("Generating languages.svg")

    with open("templates/languages.svg", "r", encoding="utf-8") as f:
        output = f.read()

    progress = ""
    lang_list = ""

    sorted_languages = sorted(
        (await stats.languages).items(),
        reverse=True,
        key=lambda t: t[1].get("size"),
    )

    delay_between = 150

    for i, (lang, data) in enumerate(sorted_languages):
        color = data.get("color") or "#000000"

        ratio = [0.98, 0.02]

        if data.get("prop", 0) > 50:
            ratio = [0.99, 0.01]

        if i == len(sorted_languages) - 1:
            ratio = [1, 0]

        progress += (
            f'<span style="background-color: {color};'
            f'width: {(ratio[0] * data.get("prop", 0)):0.3f}%;'
            f'margin-right: {(ratio[1] * data.get("prop", 0)):0.3f}%;" '
            f'class="progress-item"></span>'
        )

        lang_list += f"""
<li style="animation-delay: {i * delay_between}ms;">
<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill:{color};"
viewBox="0 0 16 16" version="1.1" width="16" height="16"><path
fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>
<span class="lang">{lang}</span>
<span class="percent">{data.get("prop", 0):0.2f}%</span>
</li>
"""

    output = re.sub(r"{{ progress }}", progress, output)
    output = re.sub(r"{{ lang_list }}", lang_list, output)

    generate_output_folder()

    with open("generated/languages.svg", "w", encoding="utf-8") as f:
        f.write(output)

    logger.info("languages.svg generated successfully")


################################################################################
# Main
################################################################################


async def main() -> None:
    access_token = os.getenv("ACCESS_TOKEN")

    if not access_token:
        raise RuntimeError("ACCESS_TOKEN secret is missing")

    user = os.getenv("GITHUB_ACTOR")

    if not user:
        raise RuntimeError("GITHUB_ACTOR environment variable is missing")

    exclude_repos = os.getenv("EXCLUDED", "")
    exclude_repos = (
        {x.strip() for x in exclude_repos.split(",") if x.strip()}
        if exclude_repos
        else set()
    )

    exclude_langs = os.getenv("EXCLUDED_LANGS", "")
    exclude_langs = (
        {x.strip() for x in exclude_langs.split(",") if x.strip()}
        if exclude_langs
        else set()
    )

    consider_forked_repos = (
        os.getenv("COUNT_STATS_FROM_FORKS", "false").lower() == "true"
    )

    logger.info(f"GitHub user: {user}")
    logger.info(f"Excluded repos: {exclude_repos}")
    logger.info(f"Excluded langs: {exclude_langs}")
    logger.info(f"Include forked repos: {consider_forked_repos}")

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        stats = Stats(
            user,
            access_token,
            session,
            exclude_repos=exclude_repos,
            exclude_langs=exclude_langs,
            consider_forked_repos=consider_forked_repos,
        )

        await asyncio.gather(
            generate_languages(stats),
            generate_overview(stats),
        )

    logger.info("All images generated successfully")


if __name__ == "__main__":
    asyncio.run(main())