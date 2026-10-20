import unittest

from app.errors import ApiError
from app.jitsi import parse_meeting

PUBLIC = lambda h: ["8.8.8.8"]


def parse(url="", room="", resolver=PUBLIC, allowed=()):
    return parse_meeting(url, room, "https://meet.jit.si", allowed, resolver)


class JitsiLinkTests(unittest.TestCase):
    def code(self, *a, **k):
        with self.assertRaises(ApiError) as cm:
            parse(*a, **k)
        return cm.exception.code

    def test_valid_links_on_any_public_server(self):
        r = parse("https://meet.jit.si/Team-Standup?jwt=SECRET#config.x=1")
        self.assertEqual((r["server"], r["room"], r["url"]), ("https://meet.jit.si", "Team-Standup", "https://meet.jit.si/Team-Standup"))
        self.assertNotIn("SECRET", str(r))                                    # query tokens are never kept
        r = parse("https://jitsi.example.org:8443/tenant/room-1")
        self.assertEqual((r["server"], r["room"]), ("https://jitsi.example.org:8443", "tenant/room-1"))

    def test_link_without_scheme_gets_https(self):
        self.assertEqual(parse("meet.jit.si/abc")["room"], "abc")

    def test_room_name_only_uses_default_server_and_empty_generates_one(self):
        self.assertEqual(parse(room="my-room")["url"], "https://meet.jit.si/my-room")
        a, b = parse()["room"], parse()["room"]
        self.assertTrue(a.startswith("smmg-") and a != b)

    def test_link_wins_over_room_name(self):
        self.assertEqual(parse("https://meet.jit.si/from-link", "ignored")["room"], "from-link")

    def test_rejects_non_https_and_credentials_and_bad_rooms(self):
        self.assertEqual(self.code("http://meet.jit.si/room"), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("https://user:pw@meet.jit.si/room"), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("https://meet.jit.si/"), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("https://meet.jit.si/a/b/c"), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("https://meet.jit.si/ro om"), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("https://meet.jit.si/" + "x" * 300), "INVALID_MEETING_LINK")
        self.assertEqual(self.code("ftp://meet.jit.si/room"), "INVALID_MEETING_LINK")

    def test_rejects_dot_only_room_names(self):
        for bad in ("https://meet.jit.si/..", "https://meet.jit.si/.", "https://meet.jit.si/tenant/.."):
            self.assertEqual(self.code(bad), "INVALID_MEETING_LINK", bad)
        self.assertEqual(self.code(room=".."), "INVALID_MEETING_LINK")
        self.assertEqual(parse("https://meet.jit.si/a.b")["room"], "a.b")

    def test_blocks_internal_targets(self):
        for url in ("https://localhost/room", "https://127.0.0.1/room", "https://10.1.2.3/room", "https://192.168.0.9/room",
                    "https://169.254.169.254/room", "https://[::1]/room", "https://printer.local/room"):
            self.assertEqual(self.code(url), "INVALID_MEETING_LINK", url)
        self.assertEqual(self.code("https://sneaky.example.com/room", resolver=lambda h: ["10.0.0.5"]), "INVALID_MEETING_LINK")  # DNS to private IP

    def test_unresolvable_host(self):
        def boom(h): raise OSError("nx")
        self.assertEqual(self.code("https://nope.example.com/room", resolver=boom), "HOST_UNRESOLVABLE")

    def test_allowed_hosts_list(self):
        self.assertEqual(self.code("https://other.example.com/room", allowed=("meet.jit.si",)), "HOST_NOT_ALLOWED")
        self.assertEqual(parse("https://meet.jit.si/room", allowed=("meet.jit.si",))["room"], "room")

    def test_same_room_has_same_key_regardless_of_case(self):
        self.assertEqual(parse("https://Meet.Jit.Si/Room")["room_key"], parse("https://meet.jit.si/room")["room_key"])


if __name__ == "__main__":
    unittest.main()
