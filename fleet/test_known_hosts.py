"""Tests for the fleet known_hosts builder."""

import io
import json
import socket
import subprocess
import sys

import pytest

import known_hosts
from known_hosts import Host

HOSTS = """
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
    switches:
      hosts:
        sw1: {}
    radios:
      hosts:
        ap1: {}
    gpu:
      hosts:
        orinnx: {}
  hosts:
    fogbank:
      ansible_host: 192.168.5.2
    hovercraft:
      ansible_host: "{{ templated }}"
    orinnx:
      ansible_host: 192.168.5.8
      ansible_host_fallbacks: [192.168.4.49]
    sw1:
      ansible_host: 10.0.0.2
    ap1:
      ansible_host: 10.0.0.9
    numbers:
      ansible_host: numbers.example.com
    videopi:
      ansible_host: videopi
"""

FILES = {
    "group_vars/all/vars.yml": "ansible_port: 22\nsecret: !vault |\n  $ANSIBLE_VAULT;1.1\n",
    "group_vars/all/vault.yml": "$ANSIBLE_VAULT;1.1;AES256\n6162\n",
    "group_vars/switches.yml": "ansible_connection: local\n",
    "group_vars/radios.yml": "ansible_connection: ansible.netcommon.network_cli\n"
    'ansible_ssh_common_args: "-o HostKeyAlgorithms=+ssh-rsa -oCiphers=+aes128-cbc"\n',
    "group_vars/claude/vars.yml": "ansible_port: 2200\n",
    "group_vars/gpu.yaml": "ansible_port: 2201\n",
    "group_vars/gpu.txt": "ansible_port: 9999\n",
    "host_vars/numbers/vars.yml": "ansible_port: 2222\nansible_host: !unsafe numbers.example.com\n",
    "host_vars/numbers/vault.yml": "  $ANSIBLE_VAULT;1.1;AES256\n6162\n",
    "host_vars/fogbank.yml": "ansible_host: 192.168.5.2\nansible_port: null\n"
    "ansible_ssh_extra_args: '-o ProxyJump=\"jump host\"'\n",
    "host_vars/videopi.yml": "- not a mapping\n",
}

KEYSCAN = {
    "22": "# banner\n192.168.5.2 ssh-ed25519 AAAAfog\n192.168.5.2 ssh-rsa AAAArsa\n"
    "192.168.5.2 ssh-ed25519 AAAAfog\n192.168.4.49 ssh-ed25519 AAAAorin\nbad line\n",
    "2222": "[numbers.example.com]:2222 ssh-ed25519 AAAAnum\n",
}

RESOLVE = {
    "192.168.5.2": ["192.168.5.2"],
    "fogbank.finf": ["192.168.5.2"],
    "192.168.4.49": ["192.168.4.49"],
    "192.168.5.8": ["192.168.5.8"],
    "orinnx.finf": ["10.9.9.9"],
    "numbers.example.com": ["203.0.113.7", "2001:db8::7"],
}


@pytest.fixture(name="inventory")
def inventory_fixture(tmp_path):
    root = tmp_path / "inventory"
    for name, text in {"hosts.yml": HOSTS, **FILES}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root / "hosts.yml"


@pytest.fixture(name="offline")
def offline_fixture(monkeypatch):
    calls = []

    def run(argv, **_):
        calls.append(argv)
        port = argv[argv.index("-p") + 1]
        return subprocess.CompletedProcess(argv, 0, stdout=KEYSCAN.get(port, ""), stderr="")

    def getaddrinfo(host, *_, **__):
        if host not in RESOLVE:
            raise socket.gaierror
        return [(0, 0, 0, "", (ip, 22)) for ip in RESOLVE[host] * 2]

    def urlopen(url, timeout):
        assert url == known_hosts.GITHUB_META and timeout == 5
        return io.BytesIO(json.dumps({"ssh_keys": ["ssh-ed25519 AAAAgh"]}).encode())

    monkeypatch.setattr(known_hosts.subprocess, "run", run)
    monkeypatch.setattr(known_hosts.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(known_hosts.urllib.request, "urlopen", urlopen)
    return calls


def test_ssh_hosts_resolve_like_ansible(inventory):
    assert known_hosts.ssh_hosts(inventory, "all") == [
        Host(
            "ap1",
            "10.0.0.9",
            [],
            22,
            (("HostKeyAlgorithms", "+ssh-rsa"), ("Ciphers", "+aes128-cbc")),
        ),
        Host("fogbank", "192.168.5.2", [], 2200, (("ProxyJump", "jump host"),)),
        Host("hovercraft", None, [], 2201),
        Host("numbers", "numbers.example.com", [], 2222),
        Host("orinnx", "192.168.5.8", ["192.168.4.49"], 2201),
        Host("videopi", "videopi", [], 22),
    ]


def test_ssh_hosts_follow_child_groups(inventory):
    names = [host.name for host in known_hosts.ssh_hosts(inventory, "claude")]
    assert names == ["fogbank", "hovercraft", "orinnx"]
    assert not known_hosts.ssh_hosts(inventory, "switches")
    assert not known_hosts.ssh_hosts(inventory, "missing")


def test_empty_inventory(tmp_path):
    path = tmp_path / "hosts.yml"
    path.write_text("")
    assert not known_hosts.ssh_hosts(path, "all")


def test_keyscan_per_port(offline):
    keys = known_hosts.keyscan(
        [("192.168.5.2", 22), ("numbers.example.com", 2222), ("192.168.5.2", 22)], 3
    )
    assert keys == {
        ("192.168.5.2", 22): ["ssh-ed25519 AAAAfog", "ssh-rsa AAAArsa"],
        ("192.168.4.49", 22): ["ssh-ed25519 AAAAorin"],
        ("numbers.example.com", 2222): ["ssh-ed25519 AAAAnum"],
    }
    assert [call[:7] for call in offline] == [
        ["ssh-keyscan", "-T", "3", "-p", "22", "-t", known_hosts.KEY_TYPES],
        ["ssh-keyscan", "-T", "3", "-p", "2222", "-t", known_hosts.KEY_TYPES],
    ]
    assert offline[0][7:] == ["192.168.5.2"]
    assert not known_hosts.keyscan([], 3)


@pytest.mark.usefixtures("offline")
def test_addresses():
    assert known_hosts.addresses("numbers.example.com") == ["2001:db8::7", "203.0.113.7"]
    assert not known_hosts.addresses("nowhere")


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
    path.write_text("# c\n\na,a.d k1 A\n[n]:2222,[n.e]:2222 k2 B\ngithub.com k3 C\na k4 D\n")
    assert known_hosts.previous_entries(path) == {
        "a": ["a,a.d k1 A", "a k4 D"],
        "n": ["[n]:2222,[n.e]:2222 k2 B"],
        "github.com": ["github.com k3 C"],
    }
    assert not known_hosts.previous_entries(tmp_path / "absent")


def test_entries_aliases(offline):
    hosts = [
        Host("fogbank", "192.168.5.2", [], 22),
        Host("orinnx", "192.168.5.8", ["192.168.4.49"], 22),
        Host("numbers", "numbers.example.com", [], 2222),
        Host("ghost", None, [], 22),
    ]
    found = known_hosts.entries(hosts, "finf", 5)
    assert found == {
        "github.com": (["github.com"], ["ssh-ed25519 AAAAgh"]),
        "fogbank": (
            ["fogbank", "fogbank.finf", "192.168.5.2"],
            ["ssh-ed25519 AAAAfog", "ssh-rsa AAAArsa"],
        ),
        "orinnx": (["orinnx", "192.168.5.8", "192.168.4.49"], ["ssh-ed25519 AAAAorin"]),
        "numbers": (
            [
                "[numbers]:2222",
                "[numbers.example.com]:2222",
                "[2001:db8::7]:2222",
                "[203.0.113.7]:2222",
            ],
            ["ssh-ed25519 AAAAnum"],
        ),
        "ghost": (["ghost", "ghost.finf"], []),
    }
    assert "ghost.finf" in offline[0]


def test_entries_without_domain(offline):
    known_hosts.entries([Host("fogbank", None, [], 22)], "", 5)
    assert offline[0][7:] == ["fogbank"]


def test_render_falls_back_to_previous():
    found = {"a": (["a", "a.d"], ["t k"]), "b": (["b"], []), "c": (["c"], [])}
    rendered = list(known_hosts.render(found, {"b": ["b old"], "a": ["a stale"]}))
    assert rendered == [
        ("a", ["a,a.d t k"], True),
        ("b", ["b old"], False),
        ("c", [], False),
    ]


@pytest.mark.parametrize(
    "args, options",
    [
        ("", ()),
        ("-C -o StrictHostKeyChecking=no", (("StrictHostKeyChecking", "no"),)),
        ("-oUser=x -o 'Port 2' -o", (("User", "x"), ("Port", "2"))),
        ("-o Bare= -o =x -v", ()),
    ],
)
def test_ssh_options(args, options):
    assert known_hosts.ssh_options(args) == options


def test_ssh_config():
    hosts = [
        Host("fogbank", "192.168.5.2", [], 22),
        Host("numbers", "numbers.example.com", [], 2222),
        Host("videopi", "videopi", [], 22),
        Host("bare", None, [], 2200),
        Host("plain", None, [], 22),
        Host("ap", None, [], 22, (("HostKeyAlgorithms", "+ssh-rsa"),)),
    ]
    assert known_hosts.ssh_config(hosts) == (
        "Host fogbank 192.168.5.2\n    HostName 192.168.5.2\n\n"
        "Host numbers numbers.example.com\n    HostName numbers.example.com\n    Port 2222\n\n"
        "Host bare\n    Port 2200\n\n"
        "Host ap\n    HostKeyAlgorithms +ssh-rsa\n"
    )
    assert known_hosts.ssh_config([]) == ""


@pytest.mark.usefixtures("offline")
def test_main_end_to_end(inventory, tmp_path, capsys):
    previous = tmp_path / "previous"
    previous.write_text("[hovercraft]:2201 ssh-ed25519 AAAAold\nghost x STALE\n")
    config = tmp_path / "ssh_config"
    argv = [str(inventory), "--group", "claude", "--domain", ".finf.", "--previous", str(previous)]
    assert known_hosts.main([*argv, "--ssh-config", str(config)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "github.com ssh-ed25519 AAAAgh",
        "[hovercraft]:2201 ssh-ed25519 AAAAold",
    ]
    assert "[3/4] hovercraft: 1 keys, kept previous" in out.err
    assert "[2/4] fogbank: 0 keys, unreachable, no keys" in out.err
    assert (
        "Host fogbank 192.168.5.2\n    HostName 192.168.5.2\n    Port 2200\n"
        "    ProxyJump jump host\n" in config.read_text()
    )


@pytest.mark.usefixtures("offline")
def test_main_default_group_without_ssh_config(inventory, capsys):
    assert known_hosts.main([str(inventory), "--domain", "finf"]) == 0
    out = capsys.readouterr()
    assert "github.com and 6 all hosts" in out.err
    assert "[numbers]:2222,[numbers.example.com]:2222" in out.out
    assert "fogbank,fogbank.finf,192.168.5.2 ssh-ed25519 AAAAfog" not in out.out


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
    assert "0 all hosts" in proc.stderr
