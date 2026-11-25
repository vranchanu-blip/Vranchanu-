import json

import pytest

from bot.modules.dosar.vpn import VpnConfigError, XrayManager, parse_config, proxy_url

CFG = {
    "inbounds": [
        {"listen": "127.0.0.1", "port": 10808, "protocol": "socks"},
        {"listen": "127.0.0.1", "port": 10809, "protocol": "http",
         "settings": {"accounts": [{"user": "apovpn", "pass": "p@ss"}]}},
    ],
    "outbounds": [{"protocol": "vless", "tag": "proxy"}],
}


def test_parse_config_fixes_smart_quotes_and_finds_http_inbound():
    raw = "JSON Украина\n" + json.dumps(CFG).replace('"', "“", 3)
    cfg = parse_config(raw)
    assert proxy_url(cfg) == "http://apovpn:p%40ss@127.0.0.1:10809"


@pytest.mark.parametrize("raw", ["", "not json", '{"inbounds": []}', json.dumps({**CFG, "inbounds": CFG["inbounds"][:1]})])
def test_parse_config_rejects_bad(raw):
    with pytest.raises(VpnConfigError):
        parse_config(raw)


def test_manager_save_load_remove(tmp_path):
    m = XrayManager(str(tmp_path))
    assert not m.configured
    m.save(CFG)
    assert m.load() == CFG and (tmp_path / "xray.json").stat().st_mode & 0o777 == 0o600
    m.remove()
    assert not m.configured
