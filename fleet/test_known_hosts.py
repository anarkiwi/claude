"""Tests for the fleet known_hosts builder."""

import io
import json
import socket
import subprocess
import sys

import pytest

import known_hosts

INVENTORY = """
all:
  children:
    claude:
      hosts:
        fogbank: {}
      children:
        gpu:
          hosts:
            hovercraft: {}
        loop:
          children:
            claude: {}
    other:
      hosts:
        printer: {}
    gpu:
      hosts:
        orinnx: {}
  hosts:
    fogbank:
      ansible_host: 192.168.5.2
"""

KEYSCAN = """# fogbank.finf:22 SSH-2.0-OpenSSH_9.6
fogbank.finf ssh-ed25519 AAAAfog
fogbank.finf ssh-rsa AAAArsa
fogbank.finf ssh-ed25519 AAAAfog
hovercraft.finf ecdsa-sha2-nistp256 AAAAhov
malformed line
"""


def fake_run(stdout, calls=None):
    def run(argv, **_):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return run


def fake_urlopen(payload):
    def urlopen(url, timeout):
        assert url == known_hosts.GITHUB_META and timeout == 5
        return io.BytesIO(json.dumps(payload).encode())

    return urlopen


@pytest.fixture(name="offline")
def offline_fixture(monkeypatch):
    calls = []
    monkeypatch.setattr(known_hosts.subprocess, "run", fake_run(KEYSCAN, calls))
    monkeypatch.setattr(
        known_hosts.urllib.request, "urlopen", fake_urlopen({"ssh_keys": ["ssh-ed25519 AAAAgh"]})
    )
    monkeypatch.setattr(
        known_hosts.socket,
        "getaddrinfo",
        lambda host, *_, **__: [(0, 0, 0, "", ("192.168.5.2", 22))] * 2,
    )
    return calls


def test_members_follow_children_and_merge_groups():
    inventory = known_hosts.yaml.safe_load(INVENTORY)
    assert known_hosts.members(inventory, "claude") == ["fogbank", "hovercraft", "orinnx"]
    assert known_hosts.members(inventory, "other") == ["printer"]
    assert not known_hosts.members(inventory, "missing")
    assert not known_hosts.members(None, "claude")


def test_keyscan_groups_and_dedupes(offline):
    keys = known_hosts.keyscan(["fogbank.finf", "hovercraft.finf"], 3)
    assert keys == {
        "fogbank.finf": ["ssh-ed25519 AAAAfog", "ssh-rsa AAAArsa"],
        "hovercraft.finf": ["ecdsa-sha2-nistp256 AAAAhov"],
    }
    assert offline[0][:5] == ["ssh-keyscan", "-T", "3", "-t", known_hosts.KEY_TYPES]
    assert known_hosts.keyscan([], 3) == {}
    assert len(offline) == 1


@pytest.mark.usefixtures("offline")
def test_addresses_dedupe_and_survive_resolution_failure(monkeypatch):
    assert known_hosts.addresses("fogbank.finf") == ["192.168.5.2"]

    def fail(*_, **__):
        raise socket.gaierror

    monkeypatch.setattr(known_hosts.socket, "getaddrinfo", fail)
    assert not known_hosts.addresses("nowhere.finf")


@pytest.mark.parametrize("bad", [OSError("down"), b"not json", b'{"no": "keys"}', b"[1]"])
def test_github_keys_failure_is_empty(bad, monkeypatch):
    def urlopen(*_, **__):
        if isinstance(bad, Exception):
            raise bad
        return io.BytesIO(bad)

    monkeypatch.setattr(known_hosts.urllib.request, "urlopen", urlopen)
    assert not known_hosts.github_keys(5)


def test_previous_entries(tmp_path):
    path = tmp_path / "previous"
    path.write_text("# comment\n\nfogbank,fogbank.finf k1 AAA\ngithub.com k2 BBB\nfogbank k3 C\n")
    assert known_hosts.previous_entries(path) == {
        "fogbank": ["fogbank,fogbank.finf k1 AAA", "fogbank k3 C"],
        "github.com": ["github.com k2 BBB"],
    }
    assert not known_hosts.previous_entries(tmp_path / "absent")


def test_entries_alias_only_answering_hosts(offline):
    found = known_hosts.entries(["fogbank", "orinnx"], "finf", 5)
    assert found == {
        "github.com": (["github.com"], ["ssh-ed25519 AAAAgh"]),
        "fogbank": (
            ["fogbank", "fogbank.finf", "192.168.5.2"],
            ["ssh-ed25519 AAAAfog", "ssh-rsa AAAArsa"],
        ),
        "orinnx": (["orinnx", "orinnx.finf"], []),
    }
    assert offline[0][5:] == ["fogbank.finf", "orinnx.finf"]


def test_entries_without_domain_scan_bare_names(offline):
    known_hosts.entries(["fogbank"], "", 5)
    assert offline[0][5:] == ["fogbank"]


def test_render_falls_back_to_previous():
    found = {"a": (["a", "a.d"], ["t k"]), "b": (["b"], []), "c": (["c"], [])}
    rendered = list(known_hosts.render(found, {"b": ["b old"], "a": ["a stale"]}))
    assert rendered == [
        ("a", ["a,a.d t k"], True),
        ("b", ["b old"], False),
        ("c", [], False),
    ]


@pytest.mark.usefixtures("offline")
def test_main_end_to_end(tmp_path, capsys):
    inventory = tmp_path / "hosts.yml"
    inventory.write_text(INVENTORY)
    previous = tmp_path / "previous"
    previous.write_text("orinnx,orinnx.finf ssh-ed25519 AAAAold\nhovercraft x STALE\n")
    argv = [str(inventory), "--domain", ".finf.", "--previous", str(previous)]
    assert known_hosts.main(argv) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "github.com ssh-ed25519 AAAAgh",
        "fogbank,fogbank.finf,192.168.5.2 ssh-ed25519 AAAAfog",
        "fogbank,fogbank.finf,192.168.5.2 ssh-rsa AAAArsa",
        "hovercraft,hovercraft.finf,192.168.5.2 ecdsa-sha2-nistp256 AAAAhov",
        "orinnx,orinnx.finf ssh-ed25519 AAAAold",
    ]
    assert "[4/4] orinnx: 1 keys, kept previous" in out.err
    assert "[2/4] fogbank: 2 keys, fresh" in out.err


@pytest.mark.usefixtures("offline")
def test_main_unreachable_without_previous(tmp_path, capsys):
    inventory = tmp_path / "hosts.yml"
    inventory.write_text(INVENTORY)
    assert known_hosts.main([str(inventory), "--group", "other", "--domain", "finf"]) == 0
    out = capsys.readouterr()
    assert "printer" not in out.out
    assert "printer: 0 keys, unreachable, no keys" in out.err


def test_script_entrypoint(tmp_path):
    inventory = tmp_path / "hosts.yml"
    inventory.write_text("all: {}\n")
    proc = subprocess.run(
        [sys.executable, known_hosts.__file__, str(inventory)],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "https_proxy": "http://127.0.0.1:9"},
        timeout=60,
    )
    assert "0 claude hosts" in proc.stderr
