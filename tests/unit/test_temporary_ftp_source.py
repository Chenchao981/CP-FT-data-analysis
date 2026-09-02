from __future__ import annotations

from app.infrastructure import temporary_ftp_source


class FakeFtp:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout
        self.connected: tuple[str, int] | None = None
        self.login_value: tuple[str, str] | None = None
        self.current = "/"
        self.closed = False

    def connect(self, host: str, port: int) -> None:
        self.connected = (host, port)

    def login(self, username: str, password: str) -> None:
        self.login_value = (username, password)

    def cwd(self, path: str) -> None:
        self.current = path

    def pwd(self) -> str:
        return self.current

    def mlsd(self, path: str, facts: list[str]):
        assert facts == ["type", "size", "modify"]
        if path == "/root":
            return iter(
                [
                    ("lot-a", {"type": "dir"}),
                    ("readme.txt", {"type": "file", "size": "5"}),
                ]
            )
        assert path == "/root/lot-a"
        return iter(
            [
                ("one.csv", {"type": "file", "size": "10", "modify": "1"}),
                ("two.CSV", {"type": "file", "size": "20", "modify": "2"}),
            ]
        )

    def quit(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


def test_temporary_ftp_preview_lists_csv_without_returning_credentials(monkeypatch) -> None:
    instance = FakeFtp(timeout=15.0)
    monkeypatch.setattr(
        temporary_ftp_source.ftplib,
        "FTP",
        lambda *, timeout: instance,
    )

    preview = temporary_ftp_source.preview_ftp_directory(
        protocol="FTP",
        server="ftp://example.test",
        port=None,
        username="engineer",
        password="temporary-secret",
        remote_path="/root",
    )

    assert instance.connected == ("example.test", 21)
    assert instance.login_value == ("engineer", "temporary-secret")
    assert instance.closed is True
    assert preview.file_count == 2
    assert preview.total_bytes == 30
    assert [item.relative_path for item in preview.files] == [
        "lot-a/one.csv",
        "lot-a/two.CSV",
    ]
    assert "temporary-secret" not in repr(preview)
