"""Logs must never carry a user's secrets, network names or filesystem layout."""

from __future__ import annotations

import json
import logging
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

import pytest

import sphereloom.logging as sphereloom_logging
from sphereloom.domain.payloads import MALFORMED
from sphereloom.logging import REDACTED, RedactionFilter, configure_logging, redact


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcdef1234567890abcdef",
        'token="s3cret-value-that-is-long"',
        "api_key=abcdef1234567890",
        'password: "hunter2hunter2"',
    ],
)
def test_credentials_are_removed(raw: str) -> None:
    cleaned = redact(raw)

    assert REDACTED in cleaned
    for secret in ("abcdef1234567890abcdef", "s3cret-value-that-is-long", "hunter2hunter2"):
        assert secret not in cleaned


def test_wifi_network_names_are_removed() -> None:
    """An SSID identifies a person's home or workplace."""
    cleaned = redact('connected to ssid="X5 ABC123.OSC"')

    assert "X5 ABC123.OSC" not in cleaned
    assert REDACTED in cleaned


def test_query_strings_are_removed() -> None:
    cleaned = redact("GET http://192.168.42.1/files?token=abc&name=holiday")

    assert "token=abc" not in cleaned
    assert "holiday" not in cleaned


@pytest.mark.parametrize(
    "raw",
    [
        r"saved to C:\Users\alice\Videos\holiday.mp4",
        "saved to /home/alice/videos/holiday.mp4",
        "saved to /Users/alice/Movies/holiday.mp4",
    ],
)
def test_absolute_paths_are_removed(raw: str) -> None:
    """Absolute paths embed a username, and filenames describe someone's private life."""
    cleaned = redact(raw)

    assert "alice" not in cleaned
    assert "holiday" not in cleaned


def test_relative_workspace_paths_survive() -> None:
    """Redaction must not destroy the information users actually need."""
    cleaned = redact("saved to downloads/clip.insv")

    assert cleaned == "saved to downloads/clip.insv"


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ('password: "correct horse battery staple" done', ["correct", "horse", "staple"]),
        ("token='open sesame now' done", ["open", "sesame", "now"]),
        ('{"ssid": "My Home Net", "x": 1}', ["My", "Home", "Net"]),
        ('connected to ssid="X5 ABC123.OSC"', ["X5", "ABC123"]),
        ('secret: "cut short by the bound', ["cut", "short", "bound"]),
        (r'saved to "C:\Users\John Smith\holiday trip.mp4" ok', ["John", "Smith", "holiday"]),
        ("saved to '/home/john smith/holiday trip.mp4' ok", ["john", "smith", "holiday"]),
        # `repr` switches to double quotes exactly when a string contains an apostrophe.
        (r'saved to "C:\\Users\\Mary O\'Brien\\My Videos\\x.mp4"', ["Mary", "Brien", "Videos"]),
        ('saved to "/home/o\'brien smith/x.mp4"', ["brien", "smith"]),
        # JSON and `repr` escape a quote inside a string rather than ending it.
        (r'{"password": "ab\"cd ef gh"}', ["ab", "cd", "ef", "gh"]),
        (r"token='it\'s a secret'", ["it", "secret"]),
        # `repr` renders a dict's keys in single quotes.
        ("cfg {'password': 'correct horse battery'}", ["correct", "horse", "battery"]),
        ("cfg {'ssid': 'My Home Net'}", ["My", "Home", "Net"]),
    ],
    ids=[
        "double-quoted-credential",
        "single-quoted-credential",
        "json-ssid",
        "ssid-assignment",
        "unterminated-quote",
        "quoted-windows-path",
        "quoted-posix-path",
        "windows-path-with-apostrophe",
        "posix-path-with-apostrophe",
        "escaped-double-quote",
        "escaped-single-quote",
        "repr-dict-credential",
        "repr-dict-ssid",
    ],
)
def test_no_word_of_a_quoted_value_survives(raw: str, words: list[str]) -> None:
    """A quoted value may contain spaces; stopping at the first one leaked the rest."""
    cleaned = redact(raw)

    assert REDACTED in cleaned
    for word in words:
        assert word not in cleaned


def test_a_traceback_line_keeps_its_location() -> None:
    """A quoted path ends at a closing quote followed by a delimiter, so diagnosis survives."""
    cleaned = redact(r'  File "C:\Users\John Smith\app.py", line 12, in fetch')

    assert "John" not in cleaned
    assert "Smith" not in cleaned
    assert cleaned.endswith(", line 12, in fetch")


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        # The query-string pattern once ran first and consumed the escape or drive letter
        # the path was recognised by.
        (r'{"file": "/home/john/a?b\" John Smith diary"}', ["John", "Smith", "diary"]),
        # A quote that a backslash escapes is part of the path even before a delimiter.
        (r'"/home/john/a\", John Smith/x"', ["John", "Smith"]),
        (r"['/home/john/what?it\'s \"John Smith\" diary']", ["John", "Smith", "diary"]),
        (r'"\\?\C:\Users\John Smith\clip.mp4"', ["John", "Smith", "clip"]),
        (r"['\\\\?\\C:\\Users\\John Smith\\clip.mp4']", ["John", "Smith", "clip"]),
        # `Path.as_posix()` writes a drive path with forward slashes.
        ('"D:/Clips/John Smith/clip.mp4"', ["John", "Smith", "clip"]),
        ("saved to D:/Clips/John Smith/clip.mp4", ["John", "Smith", "clip"]),
        # An unquoted path with a space has no visible end.
        (r"saved to C:\Users\John Smith\clip.mp4", ["John", "Smith", "clip"]),
        # A quote inside a path that no delimiter follows does not end it.
        ("saved to '/home/o'brien smith/x.mp4' ok", ["brien", "smith"]),
        # An escaped opening quote, from `repr` of JSON or of a string with both quotes.
        (r"""['"password": \'correct horse\'']""", ["correct", "horse"]),
        (r"""['"ssid": \'Home Net 5G\'']""", ["Home", "Net"]),
        (r"""['{\'password\': "correct horse"}']""", ["correct", "horse"]),
        (r'"{\"password\": \"correct horse\"}"', ["correct", "horse"]),
        # JSON inside `repr`: two levels of escaping.
        (r"""{'body': '{"password": "a\\"b correct horse"}'}""", ["correct", "horse"]),
        # A backslash at the very end once made the quoted match fail outright.
        ('password: "correct horse battery\\', ["correct", "horse", "battery"]),
        ('"C:\\Users\\John Smith\\', ["John", "Smith"]),
        # A label at the end of a longer name.
        ("{'access_token': 'abc def ghi'}", ["abc", "def", "ghi"]),
        ("{'wifi_ssid': 'Home Net'}", ["Home", "Net"]),
    ],
    ids=[
        "query-before-escaped-quote",
        "escaped-quote-before-delimiter",
        "query-before-apostrophe",
        "extended-length-prefix",
        "extended-length-prefix-repr",
        "drive-with-forward-slashes",
        "unquoted-drive-with-forward-slashes",
        "unquoted-path-with-space",
        "apostrophe-inside-single-quoted-path",
        "escaped-opening-quote",
        "escaped-opening-quote-ssid",
        "escaped-key-quote",
        "json-encoded-json",
        "json-inside-repr",
        "trailing-backslash-credential",
        "trailing-backslash-path",
        "access-token",
        "wifi-ssid",
    ],
)
def test_no_shape_of_quoting_or_escaping_lets_a_value_through(raw: str, words: list[str]) -> None:
    """Where a value ends is uncertain once quotes and escapes nest; redaction must not guess."""
    cleaned = redact(raw)

    assert REDACTED in cleaned
    for word in words:
        assert word not in cleaned


@pytest.mark.parametrize(
    "raw",
    [
        "accessToken=hunter2SECRET",
        '{"accessToken": "hunter2SECRET"}',
        '{"wifiSsid": "hunter2SECRET"}',
        '{"wifiPassword": "hunter2SECRET"}',
        "clientSecret: hunter2SECRET",
        "secret_key=hunter2SECRET",
        "aws_secret_access_key=hunter2SECRET",
        "tokens=['hunter2SECRET']",
        "[('token', 'hunter2SECRET')]",
        "('password', 'hunter2SECRET')",
        "Joined SSID 'hunter2SECRET'",
        "Authorization: Basic hunter2SECRET",
        "credentials=hunter2SECRET",
        # The debug form of an f-string and other expressions around the label.
        "headers['Authorization']='hunter2SECRET'",
        "settings['http_token']='hunter2SECRET'",
        "http_token.get_secret_value()='hunter2SECRET'",
        "options['wifi_password'] = 'hunter2SECRET'",
        # An escape `repr` wrote in place of the separator.
        r"['token\thunter2SECRET']",
        r"""{'body': '{\\"password\\": \\"hunter2SECRET\\"}'}""",
    ],
)
def test_a_sensitive_word_anywhere_in_a_label_redacts_its_value(raw: str) -> None:
    """Enumerating label shapes left one more each time; the word itself is the signal."""
    assert "hunter2SECRET" not in redact(raw)


@pytest.mark.parametrize(
    "raw",
    [
        r"open \\fileserver\home\john\x.insv",
        r"open ['\\\\fileserver\\home\\john\\x.insv']",
        "open /media/john/X5/VID.insv",
        "open /run/media/john/X5/VID.insv",
        "open /mnt/c/Users/john/x.insv",
        "open /data/john/x.insv",
        "open file:///home/john/x.insv",
        r"open ['first\n/data/john/x.insv']",
        "open /Volumes/john/x.insv",
        r"open \\?\UNC\nas\share\john smith\x.mp4",
        r"open \\.\UNC\nas\share\john smith\x.mp4",
        r"open \\server@SSL@443\DavWWWRoot\john\x.mp4",
        # A `WindowsPath` repr writes a share with forward slashes on every platform.
        f"saved {PureWindowsPath(r'\\nas\share\john smith\x.insv')!r}",
        f"files {[PureWindowsPath(r'\\nas\share\john smith\x.insv')]}",
        "open file://nas/share/john/x.insv",
    ],
)
def test_any_absolute_path_is_redacted(raw: str) -> None:
    """A workspace can live anywhere, so a list of personal roots always missed one."""
    assert "john" not in redact(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "POST http://192.168.42.1/osc/commands/execute",
        "POST /osc/commands/execute",
        "GET http://192.168.42.1:80/DCIM/Camera01/VID_001.insv",
        "GET https://example.com//double/slash",
        "saved to downloads/clip.insv",
        "camera state is idle, battery 0.85",
        "took 12/30 attempts",
    ],
)
def test_protocol_text_survives_redaction(raw: str) -> None:
    """Over-redaction is the accepted cost, but not of the text that diagnoses the camera."""
    assert redact(raw) == raw


def test_a_dict_argument_has_its_secrets_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole pipeline: a dict argument is rendered by `repr`, then redacted."""
    configure_logging(level="INFO", redaction=True)

    logging.getLogger("sphereloom.test").info(
        "cfg %s %r",
        {"password": "correct horse battery", "ssid": "My Home Net"},
        r"C:\Users\Mary O'Brien\My Videos\x.mp4",
    )

    line = capsys.readouterr().err
    for word in ("correct", "horse", "battery", "Home", "Net", "Mary", "Brien", "Videos"):
        assert word not in line


def test_the_filter_cleans_structured_context() -> None:
    record = logging.LogRecord(
        name="sphereloom.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="download finished",
        args=(),
        exc_info=None,
    )
    record.context = {"destination": "/home/alice/clip.mp4", "bytes": 1024}

    RedactionFilter().filter(record)

    cleaned = record.context  # type: ignore[attr-defined]
    assert cleaned["destination"] == REDACTED
    assert cleaned["bytes"] == 1024


def test_logging_goes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """stdout belongs to the MCP protocol; a stray write there corrupts the session."""
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("hello")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", redaction=True)
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("once")

    assert capsys.readouterr().err.count("once") == 1


def test_every_log_line_is_parseable_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("structured", extra={"context": {"bytes": 1024}})

    line = capsys.readouterr().err.strip()
    assert json.loads(line)["bytes"] == 1024


def test_a_non_finite_value_does_not_produce_invalid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`json.dumps` renders NaN bare by default, which no JSON parser accepts.

    Bounding the context replaces it rather than dropping the whole context, so the rest
    of the record still says what was happening when the bad value arrived.
    """
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "progress", extra={"context": {"completion": float("nan"), "command": "takePicture"}}
    )

    line = capsys.readouterr().err.strip()
    assert "NaN" not in line
    payload = json.loads(line)
    assert payload["message"] == "progress"
    assert payload["completion"] == MALFORMED
    assert payload["command"] == "takePicture"


def test_an_unserialisable_value_does_not_lose_the_log_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A log record must never fail; the message survives even when a value cannot."""

    class Unserialisable:
        def __repr__(self) -> str:
            raise RuntimeError("repr blows up")

    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "kept", extra={"context": {"value": Unserialisable(), "command": "listFiles"}}
    )

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["message"] == "kept"
    assert payload["value"] == MALFORMED
    assert payload["command"] == "listFiles"


def test_an_enormous_context_value_does_not_produce_an_unbounded_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`default=str` succeeds for an object whose `__str__` returns megabytes.

    Succeeding is the problem: the fallback never runs, so without a bound applied before
    serialisation the line is as large as the device cares to make it.
    """

    class Enormous:
        def __str__(self) -> str:
            return "x" * 5_000_000

    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("big", extra={"context": {"blob": Enormous()}})

    line = capsys.readouterr().err.strip()

    assert len(line) < 10_000
    assert json.loads(line)["message"] == "big"


def test_an_enormous_message_is_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    """Arguments interpolated into a message are as untrusted as any other value."""
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("camera said %s", "y" * 100_000)

    line = capsys.readouterr().err.strip()

    assert len(line) < 10_000
    assert json.loads(line)["message"].startswith("camera said y")


def test_arguments_are_bounded_before_the_message_is_interpolated(
    capsys: pytest.CaptureFixture[str], instrumented_str: Any
) -> None:
    """A bound applied to the rendered message limits the output, not the work.

    Redaction is off so nothing else touches the argument first: the instrumented string
    refuses to be rendered whole, so interpolating it before bounding it would turn this
    record into "<message could not be rendered>".
    """
    configure_logging(level="INFO", redaction=False)
    logging.getLogger("sphereloom.test").info("camera said %s", instrumented_str("y" * 100_000))

    message = json.loads(capsys.readouterr().err.strip())["message"]

    assert message.startswith("camera said yyy")


def test_redaction_only_ever_sees_bounded_text(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every redaction pattern runs over every string it is given.

    Bounding after redaction limits the output but not that work, so the order of the two
    filters is the guarantee. The spy records what redaction is actually handed.
    """
    seen: list[int] = []
    real_redact = sphereloom_logging.redact

    def spy(text: str) -> str:
        seen.append(len(text))
        return real_redact(text)

    monkeypatch.setattr(sphereloom_logging, "redact", spy)
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "camera said %s", "y" * 100_000, extra={"context": {"blob": "z" * 100_000}}
    )
    capsys.readouterr()

    assert seen
    assert max(seen) <= sphereloom_logging.MAX_MESSAGE_LENGTH + 1


@pytest.mark.parametrize("offset", range(1, 9))
def test_a_cut_never_leaves_a_fragment_of_a_secret(
    capsys: pytest.CaptureFixture[str], offset: int
) -> None:
    """Bounding runs before redaction, so where the cut lands could decide what is logged.

    A plain slice that falls a few characters into a bearer token leaves a prefix shorter
    than a pattern for the token itself recognises. Two guards now cover it -- the cut backs
    off to a word boundary, which `test_payloads` checks directly, and the `Bearer` label
    redacts to the end of the line -- and this checks the outcome end to end at every offset
    from one to eight characters.
    """
    token = "SECRETTOKENVALUE0123456789"
    filler = "x " * ((sphereloom_logging.MAX_MESSAGE_LENGTH - len("Bearer ") - offset) // 2)
    filler += "x" * ((sphereloom_logging.MAX_MESSAGE_LENGTH - len("Bearer ") - offset) % 2)
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("%s", filler + "Bearer " + token)

    message = json.loads(capsys.readouterr().err.strip())["message"]

    assert token[:offset] not in message.rsplit("Bearer", 1)[-1]


def test_a_large_mapping_argument_is_read_only_up_to_the_bound(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A lone dictionary argument becomes `record.args` itself, not a container argument.

    It was bounded entry by entry with no cap on the number of entries, so a large
    dictionary was still walked and rendered in full.
    """

    class CountingDict(dict[str, str]):
        read = 0

        def items(self) -> Any:
            for item in super().items():
                CountingDict.read += 1
                yield item

    payload = CountingDict({f"key{index}": "v" for index in range(10_000)})
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("payload %s", payload)

    line = capsys.readouterr().err.strip()

    assert CountingDict.read <= sphereloom_logging.MAX_CONTEXT_ITEMS + 1
    assert len(line) < 10_000


def test_a_container_argument_keeps_its_members(capsys: pytest.CaptureFixture[str]) -> None:
    """Bounding a container must limit its size, not change what it contains.

    Routing containers through the JSON envelope renderer turned every `Path` into
    "[malformed]" and collapsed whitespace inside strings.
    """
    configure_logging(level="INFO", redaction=False)
    logging.getLogger("sphereloom.test").info("files %s", [PurePosixPath("a.insv"), "b  c"])

    message = json.loads(capsys.readouterr().err.strip())["message"]

    assert "PurePosixPath('a.insv')" in message
    assert "'b  c'" in message


def test_numeric_and_mapping_formats_still_work(capsys: pytest.CaptureFixture[str]) -> None:
    """Bounding arguments must not change what kind of value they are."""
    configure_logging(level="INFO", redaction=True)
    logger = logging.getLogger("sphereloom.test")

    logger.info("%d files, %.1f%% done", 3, 42.0)
    logger.info("%(name)s is %(state)s", {"name": "capture", "state": "running"})

    first, second = capsys.readouterr().err.strip().splitlines()
    assert json.loads(first)["message"] == "3 files, 42.0% done"
    assert json.loads(second)["message"] == "capture is running"


def test_strings_nested_in_context_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    """Only top-level context strings used to be redacted.

    A path or a token one level down in a record's context was written out as it was.
    """
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "saved", extra={"context": {"file": {"path": "/home/alice/Videos/holiday.mp4"}}}
    )

    line = capsys.readouterr().err.strip()

    assert "alice" not in line
    assert REDACTED in json.loads(line)["file"]["path"]


def test_a_traceback_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    """Tracebacks were written out unredacted.

    Frames name source files by absolute path, which embeds the user's home directory, and
    an exception message can carry whatever the code that raised it put there.
    """
    configure_logging(level="INFO", redaction=True)
    try:
        raise RuntimeError("could not open /home/alice/Videos/holiday.mp4")
    except RuntimeError:
        logging.getLogger("sphereloom.test").exception("failed")

    payload = json.loads(capsys.readouterr().err.strip())

    assert "RuntimeError" in payload["exception"]
    assert "alice" not in payload["exception"]


def test_a_context_value_cut_by_the_scan_budget_leaves_no_fragment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whitespace costs scan budget but produces no output.

    Padding can therefore push the point where reading stops into the middle of a token
    while the output stays well under its limit. That stop is a cut like any other.
    """
    padded = "Bearer" + " " * (500 * 16 - 9) + "SECRETTOKENVALUE0123456789abcdef"
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("x", extra={"context": {"auth": padded}})

    line = capsys.readouterr().err.strip()

    assert "SEC" not in line


def test_a_container_member_is_cut_from_the_end_not_the_middle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`reprlib` keeps the head and the tail and drops the middle.

    The kept tail can begin part-way through a token with no `Bearer` in front of it.
    """
    member = " " * 1100 + "Bearer " + "T0KEN" * 60 + " " + "y " * 400
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("raw %s", [PurePosixPath(member)])

    line = capsys.readouterr().err.strip()

    assert "T0KEN" not in line


def test_a_dictionary_argument_keeps_its_order(capsys: pytest.CaptureFixture[str]) -> None:
    """`reprlib` sorts a dictionary before limiting it: full-size work, reordered output."""
    configure_logging(level="INFO", redaction=False)
    logging.getLogger("sphereloom.test").info("%s %s", "a", {"b": 1, "a": 2})

    assert json.loads(capsys.readouterr().err.strip())["message"] == "a {'b': 1, 'a': 2}"


def test_a_large_set_argument_is_not_sorted() -> None:
    """`reprlib` sorts a set before limiting it, which costs the input's full size.

    The members count the comparisons made on them: reading a bounded number of items in
    the set's own order compares nothing, while sorting ten thousand of them compares a
    great many.
    """

    class Counted:
        compared = 0

        def __init__(self, number: int) -> None:
            self.number = number

        def __hash__(self) -> int:
            return self.number

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Counted) and other.number == self.number

        def __lt__(self, other: Counted) -> bool:
            Counted.compared += 1
            return self.number < other.number

        def __repr__(self) -> str:
            return f"C{self.number}"

    big = {Counted(number) for number in range(10_000)}
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "%s", (big,), None)

    sphereloom_logging.BoundingFilter().filter(record)

    assert Counted.compared == 0


def test_a_container_subclass_is_bounded_like_its_base() -> None:
    """Dispatch by type name sent a `list` subclass to `repr_instance`, which rendered it whole.

    The members count how often they are rendered: a bounded list renders ten of them.
    """

    class Member:
        rendered = 0

        def __repr__(self) -> str:
            Member.rendered += 1
            return "m"

    class Members(list[Member]):
        pass

    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "%s", (Members(Member() for _ in range(10_000)),), None
    )

    sphereloom_logging.BoundingFilter().filter(record)

    assert Member.rendered <= 11


def test_a_frozenset_at_the_depth_limit_still_says_frozenset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", redaction=False)
    logging.getLogger("sphereloom.test").info("%s", [[[frozenset({1})]]])

    assert "frozenset({...})" in json.loads(capsys.readouterr().err.strip())["message"]


@pytest.mark.parametrize("separator", ["\n", "\t", "\r", " ", "\v", "\f", "\x1c", "\u2028", "\xa0"])
def test_a_secret_split_by_escaped_whitespace_is_still_redacted(
    capsys: pytest.CaptureFixture[str], separator: str
) -> None:
    """A container argument is rendered with `repr` before redaction runs.

    A newline between `Bearer` and its token then arrives as the two characters `\\n`, and
    a pattern that required real whitespace let the token through.
    """
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "%s", [f"Bearer{separator}SECRETTOKENVALUE", f"token:{separator}SECRETTOKENVALUE"]
    )

    assert "SECRETTOKENVALUE" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "context",
    [
        {"token": "SECRETTOKENVALUE"},
        {"auth": {"token": "SECRETTOKENVALUE"}},
        {"items": [{"api_key": "SECRETTOKENVALUE"}]},
        {"wifi": {"SSID": "SECRETTOKENVALUE"}},
        {"Authorization": "SECRETTOKENVALUE"},
    ],
)
def test_a_value_under_a_sensitive_key_is_redacted(
    capsys: pytest.CaptureFixture[str], context: dict[str, object]
) -> None:
    """In a structured record the label is the key, not part of the value.

    The text patterns find a secret by its label, so a value whose key names a secret was
    written out as it was, at the top level and at any depth.
    """
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("x", extra={"context": context})

    assert "SECRETTOKENVALUE" not in capsys.readouterr().err


def test_a_value_whose_key_was_truncated_is_withheld(capsys: pytest.CaptureFixture[str]) -> None:
    """Bounding runs before redaction, and cutting a long key can remove the word that
    marked it as sensitive, so the value of a truncated key is withheld."""
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "x", extra={"context": {"x" * 600 + "token": "SECRETTOKENVALUE"}}
    )

    assert "SECRETTOKENVALUE" not in capsys.readouterr().err


def test_a_cut_after_repr_leaves_no_fragment_of_a_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`repr` writes whitespace as escapes, so a cut found no whitespace to back off to.

    The padding is sized so the rendered list is cut a few characters into the token. The
    cut's handling of escapes is checked directly in `test_payloads`; this checks that the
    logged line carries no fragment either way.
    """
    member = "\n" * 992 + ".Bearer\nSECRETTOKENVALUE"
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("%s", [member])

    assert "SECRE" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "prefix",
    ["https://camera.local/x?", "/home/", "C:\\Users\\"],
    ids=["query", "posix-path", "windows-path"],
)
def test_a_long_query_or_path_is_redacted_to_its_end(prefix: str) -> None:
    """The patterns stopped after 512 characters and left the rest of the run in the log."""
    text = prefix + "a" * 600 + "SECRETTOKENVALUE"

    assert "SECRETTOKENVALUE" not in redact(text)
