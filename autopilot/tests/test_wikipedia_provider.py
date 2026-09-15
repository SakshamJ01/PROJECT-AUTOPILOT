import unittest, json, urllib.error
from unittest.mock import patch, MagicMock
from autopilot.providers.wikipedia_provider import WikipediaProvider

class TestWikipediaProvider(unittest.TestCase):
    def setUp(self):
        self.p = WikipediaProvider()
    def test_valid(self):
        with patch("urllib.request.urlopen") as m:
            mock_resp = MagicMock(); mock_resp.read.return_value = json.dumps({"query":{"search":[{"title":"AI","snippet":"snip"}]}}).encode(); mock_resp.__enter__=lambda s:s; mock_resp.__exit__=lambda *a:None; mock_resp.status=200
            m.return_value = mock_resp
            res = self.p.search("AI", max_results=1)
        self.assertTrue(len(res)>=1)
        self.assertEqual(res[0]["provider"], "wikipedia")
    def test_empty(self):
        with patch("urllib.request.urlopen") as m:
            mock_resp = MagicMock(); mock_resp.read.return_value = json.dumps({"query":{"search":[]}}).encode(); mock_resp.__enter__=lambda s:s; mock_resp.__exit__=lambda *a:None; mock_resp.status=200
            m.return_value = mock_resp
            res = self.p.search("xyz")
        self.assertEqual(res, [])
    def test_malformed(self):
        with patch("urllib.request.urlopen") as m:
            mock_resp = MagicMock(); mock_resp.read.return_value = b"bad"; mock_resp.__enter__=lambda s:s; mock_resp.__exit__=lambda *a:None; mock_resp.status=200
            m.return_value = mock_resp
            res = self.p.search("x")
        self.assertEqual(res, [])
    def test_http_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(None, 500, "Err", {}, None)):
            res = self.p.search("x")
        self.assertEqual(res, [])
    def test_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            res = self.p.search("x")
        self.assertEqual(res, [])
    def test_user_agent(self):
        with patch("urllib.request.urlopen") as m:
            mock_resp = MagicMock(); mock_resp.read.return_value = json.dumps({"query":{"search":[]}}).encode(); mock_resp.__enter__=lambda s:s; mock_resp.__exit__=lambda *a:None; mock_resp.status=200
            m.return_value = mock_resp
            self.p.search("x")
            call_args = m.call_args
            req = call_args[0][0]
            self.assertIn("autopilot-wikipedia", req.get_header("User-agent"))

if __name__ == "__main__":
    unittest.main()
