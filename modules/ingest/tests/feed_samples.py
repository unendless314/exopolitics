"""Shared sample feed payloads for parser and fetch-level tests.

All timestamps inside the samples are fixed so tests never depend on the real
clock. Samples are raw XML strings; callers encode them to bytes as needed.
Extend this module when new tests need additional feed shapes instead of
inlining ad hoc XML in each test file.
"""

# One fully-populated RSS item: messy title whitespace, a tracking-parameter
# URL, a GUID, an RFC-822 date, and a description body.
RSS_BASIC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example RSS Channel</title>
    <link>https://example.com/</link>
    <item>
      <title>  First   Item
        Title </title>
      <link>https://Example.COM/article/?utm_source=feed&amp;id=42#comments</link>
      <guid>rss-guid-001</guid>
      <pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate>
      <description>First item summary.</description>
    </item>
  </channel>
</rss>
"""

# One fully-populated Atom entry; the updated timestamp carries a +08:00 offset.
ATOM_BASIC = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <entry>
    <title>Atom Entry Title</title>
    <id>urn:uuid:atom-001</id>
    <link rel="alternate" href="https://example.org/articles/1"/>
    <updated>2026-06-02T20:00:00+08:00</updated>
    <summary>Atom summary text.</summary>
  </entry>
</feed>
"""

# Atom entry without summary/description; body must fall back to content.
ATOM_CONTENT_ONLY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Content Only Entry</title>
    <id>atom-002</id>
    <link rel="alternate" href="https://example.org/articles/2"/>
    <updated>2026-06-03T12:00:00Z</updated>
    <content type="html">&lt;p&gt;Body from content element.&lt;/p&gt;</content>
  </entry>
</feed>
"""

# RSS item without link; the non-permalink GUID must become the identity.
RSS_GUID_NO_LINK = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Guid Channel</title>
    <item>
      <title>Guid Identified Entry</title>
      <guid isPermaLink="false">rss-guid-777</guid>
      <pubDate>Fri, 05 Jun 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

# Atom entry with both summary and content; summary must win.
ATOM_SUMMARY_AND_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Summary And Content Entry</title>
    <id>atom-003</id>
    <link rel="alternate" href="https://example.org/articles/3"/>
    <updated>2026-06-04T12:00:00Z</updated>
    <summary>Summary wins.</summary>
    <content type="html">&lt;p&gt;Content loses.&lt;/p&gt;</content>
  </entry>
</feed>
"""

# RSS item without link; the enclosure URL must become the canonical URL.
# isPermaLink="false" keeps feedparser from promoting the GUID to the link.
RSS_ENCLOSURE_ONLY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Podcast Channel</title>
    <item>
      <title>Podcast Episode Nine</title>
      <guid isPermaLink="false">episode-009</guid>
      <pubDate>Wed, 03 Jun 2026 12:00:00 GMT</pubDate>
      <enclosure url="https://example.net/media/ep9.mp3" type="audio/mpeg" length="12345"/>
    </item>
  </channel>
</rss>
"""

# RSS item with only a description; every identity field is missing.
RSS_MINIMAL_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Minimal Channel</title>
    <item>
      <description>Only a description.</description>
    </item>
  </channel>
</rss>
"""

# RSS item with title and date but no link or GUID (tp dedup rule path).
RSS_TITLE_DATE_ONLY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Title Date Channel</title>
    <item>
      <title>Title Without Link Or Guid</title>
      <pubDate>Thu, 04 Jun 2026 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

# RSS item whose pubDate cannot be parsed; published_at must become None.
RSS_INVALID_DATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bad Date Channel</title>
    <item>
      <title>Bad Date Item</title>
      <link>https://example.com/bad-date</link>
      <pubDate>not a real date</pubDate>
    </item>
  </channel>
</rss>
"""

# RSS item whose title is shorter than the title-hash marker threshold.
RSS_SHORT_TITLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Short Title Channel</title>
    <item>
      <title>Live</title>
      <link>https://example.com/live</link>
    </item>
  </channel>
</rss>
"""

# Structurally valid RSS feed with zero items.
RSS_EMPTY_CHANNEL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Channel</title>
  </channel>
</rss>
"""

# Not XML at all: once encoded to bytes, feedparser reports the payload as
# ill-formed (bozo), which the parser maps to a source-level parse_error.
MALFORMED_NOT_XML = "this is not xml at all"

# Well-formed RSS feed whose declared encoding (us-ascii) disagrees with the
# actual UTF-8 bytes. feedparser recovers via CharacterEncodingOverride: bozo
# is set, but the entries are valid — this must NOT be a parse error.
RSS_ENCODING_OVERRIDE = """<?xml version="1.0" encoding="us-ascii"?>
<rss version="2.0">
  <channel>
    <title>Encoding Channel</title>
    <item>
      <title>Café</title>
      <link>https://example.com/cafe</link>
    </item>
  </channel>
</rss>
"""
