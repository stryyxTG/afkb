import unittest

from tglol.proxy_utils import mask_proxy, parse_proxy, proxy_to_telethon


class ProxyUtilsTests(unittest.TestCase):
    def test_plain_host_port_defaults_to_socks5(self):
        proxy = parse_proxy("127.0.0.1:1080")
        self.assertEqual((proxy.kind, proxy.host, proxy.port, proxy.rdns), ("socks5", "127.0.0.1", 1080, True))

    def test_plain_host_port_auth(self):
        proxy = parse_proxy("host.com:1080:user:pa:ss")
        self.assertEqual(proxy.kind, "socks5")
        self.assertEqual(proxy.username, "user")
        self.assertEqual(proxy.password, "pa:ss")

    def test_url_aliases_are_normalized(self):
        self.assertEqual(parse_proxy("socks5h://u:p@host:1080").kind, "socks5")
        self.assertEqual(parse_proxy("socks4a://host:1080").kind, "socks4")
        self.assertEqual(parse_proxy("https://host:8080").kind, "http")

    def test_telegram_socks_link(self):
        proxy = parse_proxy("tg://socks?server=host&port=1080&user=u&pass=p")
        self.assertEqual((proxy.kind, proxy.host, proxy.port, proxy.username, proxy.password), ("socks5", "host", 1080, "u", "p"))

    def test_telegram_mtproto_link(self):
        proxy = parse_proxy("https://t.me/proxy?server=host&port=443&secret=dd" + "a" * 32)
        self.assertEqual(proxy.kind, "mtproto")
        self.assertEqual(proxy.transport, "randomized_intermediate")

    def test_plain_mtproto_secret(self):
        proxy = parse_proxy("host:443:" + "a" * 32)
        self.assertEqual(proxy.kind, "mtproto")
        self.assertEqual(proxy.secret, "a" * 32)

    def test_mask_hides_credentials(self):
        self.assertEqual(mask_proxy("socks5://user:pass@host:1080"), "socks5://user:***@host:1080")
        self.assertIn("secret=dddd", mask_proxy("host:443:" + "d" * 32))

    def test_telethon_kwargs_for_socks_and_mtproto(self):
        socks_kwargs = proxy_to_telethon("host:1080")
        self.assertIn("proxy", socks_kwargs)
        mtproto_kwargs = proxy_to_telethon("host:443:" + "a" * 32)
        self.assertIn("connection", mtproto_kwargs)
        self.assertEqual(mtproto_kwargs["proxy"], ("host", 443, "a" * 32))

    def test_invalid_proxy_rejected(self):
        with self.assertRaises(ValueError):
            parse_proxy("host:99999")
        with self.assertRaises(ValueError):
            parse_proxy(":1080")


    def test_pysocks_tuple_from_json(self):
        proxy = parse_proxy([2, "host", 1080, True, "user", "pass"])
        self.assertEqual(proxy.kind, "socks5")
        self.assertEqual(proxy.username, "user")
        self.assertEqual(proxy.password, "pass")

    def test_python_socks_tuple_from_json(self):
        proxy = parse_proxy(["socks5", "host", 1080, "user", "pass", True])
        self.assertEqual(proxy.kind, "socks5")
        self.assertTrue(proxy.rdns)

    def test_proxy_dict_from_json(self):
        proxy = parse_proxy({"type": "https", "host": "host", "port": 8080, "username": "u", "password": "p"})
        self.assertEqual(proxy.kind, "http")
        self.assertEqual(mask_proxy(proxy), "http://u:***@host:8080")
if __name__ == "__main__":
    unittest.main()
