#!/usr/bin/env python3
"""
UFO Config Generator (Derived Work Helper)

This script aggregates all RSS feeds from the 3 cloned repositories and the
19 curated UFO feeds, dumping them into categories.yaml and rss-sources.yaml.
It acts as the first stage of the UFO UAP research collection pipeline.
"""

import os
import re
import xml.etree.ElementTree as ET
import yaml
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_OUT_DIR = BASE_DIR / "config"
CONFIG_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Define categories
CATEGORIES = {
    1: {"name": "UFO & UAP Curated (精選幽浮與超常現象)", "enabled": True},
    2: {"name": "Space & Astronomy (太空天文與航太)", "enabled": True},
    3: {"name": "Science & Research (前沿科學與研究)", "enabled": True},
    4: {"name": "Chinese Independent Blogs (中文獨立博客全量)", "enabled": True},
    5: {"name": "Chinese Top RSS (中文熱門與科普排行榜)", "enabled": True}
}

# Curated UFO feeds (Category 1)
CURATED_UFO_FEEDS = [
    {
        "title": "MUFON (Mutual UFO Network)",
        "xml_url": "https://mufon.com/feed/",
        "html_url": "https://mufon.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "UFO FOTOCAT BLOG",
        "xml_url": "https://fotocat.blogspot.com/feeds/posts/default?alt=rss",
        "html_url": "https://fotocat.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Bad UFOs (Scientific Skepticism)",
        "xml_url": "https://feeds.feedburner.com/BadUfosSkepticismUfosAndTheUniverse-ByRobertSheaffer",
        "html_url": "https://badufos.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Unidentified Aerial Phenomena Research",
        "xml_url": "https://ufos-scientificresearch.blogspot.com/feeds/posts/default?alt=rss",
        "html_url": "https://ufos-scientificresearch.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "theozfiles (Forensic Anomaly)",
        "xml_url": "https://theozfiles.blogspot.com/feeds/posts/default?alt=rss",
        "html_url": "https://theozfiles.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "NewsNation » UFO",
        "xml_url": "https://www.newsnationnow.com/space/ufo/feed/",
        "html_url": "https://www.newsnationnow.com/space/ufo/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "The Black Vault Case Files (FOIA)",
        "xml_url": "https://www.theblackvault.com/casefiles/feed/",
        "html_url": "https://www.theblackvault.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Openminds.tv",
        "xml_url": "https://openminds.tv/feed/",
        "html_url": "https://openminds.tv/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Earthfiles.com (Linda Moulton Howe)",
        "xml_url": "https://www.earthfiles.com/feed/",
        "html_url": "https://www.earthfiles.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "New York Post » UFOs",
        "xml_url": "https://nypost.com/tag/ufos/feed/",
        "html_url": "https://nypost.com/tag/ufos/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Latest-UFO-Sightings",
        "xml_url": "https://www.latest-ufo-sightings.net/feed/atom",
        "html_url": "https://www.latest-ufo-sightings.net/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "The UFO Chronicles",
        "xml_url": "https://feeds.feedburner.com/TheUFOChronicles",
        "html_url": "https://www.theufochronicles.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "UFO Sightings Hotspot",
        "xml_url": "http://ufosightingshotspot.blogspot.com/feeds/posts/default",
        "html_url": "http://ufosightingshotspot.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "A Different Perspective (Kevin Randle)",
        "xml_url": "http://kevinrandle.blogspot.com/feeds/posts/default",
        "html_url": "http://kevinrandle.blogspot.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "UFOnutt Blog (Chuck Zukowski)",
        "xml_url": "https://www.ufonut.com/feed/",
        "html_url": "https://www.ufonut.com/the-ufonut-blog/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "UFO Sightings Daily",
        "xml_url": "https://www.ufosightingsdaily.com/feeds/posts/default",
        "html_url": "https://www.ufosightingsdaily.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "UFO MatriX",
        "xml_url": "https://www.ufomatrix.org/feeds/posts/default",
        "html_url": "https://www.ufomatrix.org/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Spectral Vision",
        "xml_url": "https://spectralvision.wordpress.com/feed/",
        "html_url": "https://spectralvision.wordpress.com/",
        "category_id": 1,
        "enabled": True
    },
    {
        "title": "Reddit r/UFOs Community Feed",
        "xml_url": "https://www.reddit.com/r/UFOs/.rss",
        "html_url": "https://www.reddit.com/r/UFOs/",
        "category_id": 1,
        "enabled": True
    }
]


def parse_opml(file_path: Path, category_id: int) -> list:
    """Parse feeds from an OPML file."""
    feeds = []
    if not file_path.exists():
        print(f"Warning: {file_path} not found.")
        return feeds

    try:
        content = file_path.read_text(encoding="utf-8")
        # Strip out description attributes entirely to avoid unescaped HTML/newlines breaking the parser
        content = re.sub(r'\sdescription\s*=\s*(["\'])(.*?)\1', '', content, flags=re.DOTALL)
        # Fix unescaped ampersands in XML attributes or text
        content = re.sub(r'&(?!(amp|lt|gt|quot|apos);)', '&amp;', content)
        
        root = ET.fromstring(content)

        # Find all outlines that have xmlUrl
        for outline in root.findall(".//outline"):
            xml_url = outline.get("xmlUrl")
            if xml_url:
                title = outline.get("title") or outline.get("text") or "Untitled Feed"
                html_url = outline.get("htmlUrl") or ""
                feeds.append({
                    "title": title.strip(),
                    "xml_url": xml_url.strip(),
                    "html_url": html_url.strip(),
                    "category_id": category_id,
                    "enabled": True
                })
    except Exception as e:
        print(f"Error parsing OPML {file_path}: {e}")

    return feeds


def parse_markdown_table(file_path: Path, category_id: int) -> list:
    """Parse feeds from markdown tables in README.md."""
    feeds = []
    if not file_path.exists():
        print(f"Warning: {file_path} not found.")
        return feeds

    try:
        content = file_path.read_text(encoding="utf-8")
        # Find markdown table rows containing links
        # Row format: | Name | [URL](URL) | [View](...) |
        lines = content.splitlines()
        for line in lines:
            if "|" in line and ("http://" in line or "https://" in line):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                # Skip header separators e.g., | --- | --- |
                if len(parts) >= 2 and not all(c in "- :" for c in parts[0]):
                    title = parts[0]
                    # Extract RSS url from the second column e.g., [url](url) or plain url
                    url_col = parts[1]
                    url_match = re.search(r"https?://[^\s)\]]+", url_col)
                    if url_match:
                        xml_url = url_match.group(0)
                        feeds.append({
                            "title": title,
                            "xml_url": xml_url,
                            "html_url": "",
                            "category_id": category_id,
                            "enabled": True
                        })
    except Exception as e:
        print(f"Error parsing markdown {file_path}: {e}")

    return feeds


def main():
    all_sources = []
    source_id_counter = 1

    # 1. Curated UFO feeds (Category 1)
    print(f"Adding {len(CURATED_UFO_FEEDS)} curated UFO feeds...")
    for feed in CURATED_UFO_FEEDS:
        feed["id"] = source_id_counter
        all_sources.append(feed)
        source_id_counter += 1

    # 2. English Space feeds (Category 2)
    space_opml = BASE_DIR / "awesome-rss-feeds/recommended/with_category/Space.opml"
    space_feeds = parse_opml(space_opml, category_id=2)
    print(f"Parsed {len(space_feeds)} feeds from Space.opml")
    for feed in space_feeds:
        feed["id"] = source_id_counter
        all_sources.append(feed)
        source_id_counter += 1

    # 3. English Science feeds (Category 3)
    science_opml = BASE_DIR / "awesome-rss-feeds/recommended/with_category/Science.opml"
    science_feeds = parse_opml(science_opml, category_id=3)
    print(f"Parsed {len(science_feeds)} feeds from Science.opml")
    for feed in science_feeds:
        feed["id"] = source_id_counter
        all_sources.append(feed)
        source_id_counter += 1

    # 4. Chinese Independent Blogs (Category 4)
    cn_blogs_opml = BASE_DIR / "awesome-blogCN-feeds/feedlist.opml"
    cn_blog_feeds = parse_opml(cn_blogs_opml, category_id=4)
    print(f"Parsed {len(cn_blog_feeds)} feeds from awesome-blogCN-feeds")
    for feed in cn_blog_feeds:
        feed["id"] = source_id_counter
        all_sources.append(feed)
        source_id_counter += 1

    # 5. Chinese Top RSS Feeds (Category 5)
    cn_top_md = BASE_DIR / "top-rss-list/README.md"
    cn_top_feeds = parse_markdown_table(cn_top_md, category_id=5)
    print(f"Parsed {len(cn_top_feeds)} feeds from top-rss-list")
    for feed in cn_top_feeds:
        feed["id"] = source_id_counter
        all_sources.append(feed)
        source_id_counter += 1

    # Write categories.yaml
    categories_data = {"categories": CATEGORIES}
    categories_path = CONFIG_OUT_DIR / "categories.yaml"
    with open(categories_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(categories_data, f, allow_unicode=True, sort_keys=False)
    print(f"Successfully generated {categories_path}")

    # Write rss-sources.yaml
    sources_data = {"sources": all_sources}
    sources_path = CONFIG_OUT_DIR / "rss-sources.yaml"
    with open(sources_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(sources_data, f, allow_unicode=True, sort_keys=False)
    print(f"Successfully generated {sources_path} with {len(all_sources)} total sources!")


if __name__ == "__main__":
    main()
