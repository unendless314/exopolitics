import unittest
from unittest.mock import patch, MagicMock
import datetime
from email.utils import format_datetime
import httpx
import asyncio
from modules.ingest.src.fetcher import fetch_feed, FetchResult

class TestHTTPFetcher(unittest.IsolatedAsyncioTestCase):
    @patch("httpx.AsyncClient.get")
    async def test_fetch_success_200(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<xml>rss</xml>"
        mock_response.headers = {"etag": "w/\"etag-value\"", "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT"}
        mock_get.return_value = mock_response

        result = await fetch_feed("https://example.com/feed.xml", etag="old-etag")
        
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, b"<xml>rss</xml>")
        self.assertEqual(result.etag, "w/\"etag-value\"")
        self.assertEqual(result.last_modified, "Wed, 21 Oct 2015 07:28:00 GMT")
        self.assertIsNone(result.error_class)
        self.assertEqual(result.retry_count, 0)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_cache_304(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_get.return_value = mock_response

        result = await fetch_feed("https://example.com/feed.xml", etag="old-etag", last_modified="old-lm")
        
        self.assertEqual(result.status_code, 304)
        self.assertIsNone(result.content)
        self.assertEqual(result.etag, "old-etag")
        self.assertEqual(result.last_modified, "old-lm")
        self.assertIsNone(result.error_class)
        self.assertEqual(result.retry_count, 0)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_non_transient_404_fails_fast(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="404 Not Found",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml")
        
        self.assertEqual(result.status_code, 404)
        self.assertIsNone(result.content)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertIn("HTTP 404", result.error_detail)
        self.assertEqual(result.retry_count, 0)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_500_retries_and_fails(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="500 Internal Server Error",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2, backoff_factor=0.01)
        
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.error_class, "http_error_5xx")
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_timeout_retries_and_fails(self, mock_get, mock_sleep) -> None:
        mock_get.side_effect = httpx.TimeoutException("Connection timed out", request=MagicMock())

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2)
        
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_class, "timeout_error")
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_unexpected_error_fails_immediately(self, mock_get) -> None:
        mock_get.side_effect = RuntimeError("Something completely unexpected happened")

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2)
        
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_class, "unexpected_error")
        self.assertEqual(result.retry_count, 0)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_429_retries_and_fails_with_default_backoff(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        mock_response.headers = {}
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2, backoff_factor=0.01)
        
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_any_call(0.01)
        mock_sleep.assert_any_call(0.02)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_429_dynamic_sleep_from_headers(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        mock_response.headers = {"retry-after": "5"}
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=1)
        
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(mock_sleep.call_count, 1)
        mock_sleep.assert_called_with(5.0)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_429_dynamic_sleep_from_x_ratelimit_reset(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        mock_response.headers = {"x-ratelimit-reset": "15"}
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=1)
        
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(mock_sleep.call_count, 1)
        mock_sleep.assert_called_with(15.0)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_without_custom_headers_sends_no_forced_user_agent(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<xml>rss</xml>"
        mock_response.headers = {}
        mock_get.return_value = mock_response

        result = await fetch_feed("https://example.com/feed.xml")

        self.assertEqual(result.status_code, 200)
        sent_headers = mock_get.call_args.kwargs["headers"]
        self.assertNotIn("User-Agent", sent_headers)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_custom_user_agent_forwarded_unchanged(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<xml>rss</xml>"
        mock_response.headers = {}
        mock_get.return_value = mock_response

        custom_ua = "pc:exopolitics.ingest:v1.0.0 (by /u/exopolitics_bot)"
        result = await fetch_feed("https://example.com/feed.xml", custom_headers={"User-Agent": custom_ua})

        self.assertEqual(result.status_code, 200)
        sent_headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["User-Agent"], custom_ua)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_429_retry_after_http_date(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        http_date = format_datetime(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30),
            usegmt=True
        )
        mock_response.headers = {"retry-after": http_date}

        mock_get.side_effect = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=1)

        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertEqual(mock_sleep.call_count, 1)
        actual_sleep = mock_sleep.call_args.args[0]
        self.assertGreater(actual_sleep, 20.0)
        self.assertLessEqual(actual_sleep, 30.0)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_429_non_finite_retry_after_falls_back(self, mock_get, mock_sleep) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"

        mock_get.side_effect = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=MagicMock(),
            response=mock_response
        )

        for bad_value in ("nan", "inf", "-inf"):
            with self.subTest(retry_after=bad_value):
                mock_response.headers = {"retry-after": bad_value}
                mock_sleep.reset_mock()

                result = await fetch_feed("https://example.com/feed.xml", max_retries=1, backoff_factor=0.01)

                self.assertEqual(result.status_code, 429)
                self.assertEqual(result.error_class, "http_error_4xx")
                mock_sleep.assert_called_once_with(0.01)

if __name__ == "__main__":
    unittest.main()
