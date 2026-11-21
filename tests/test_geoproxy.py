import json

import pytest

from agentbrowse.geoproxy import GeoProxyUnavailable, load_pool, pick


def test_pick_by_country_is_case_insensitive():
    pool = [
        {"country": "US", "url": "http://us.example:8080"},
        {"country": "DE", "url": "http://de.example:8080"},
    ]
    assert pick(pool, "us") == "http://us.example:8080"
    assert pick(pool, "DE") == "http://de.example:8080"


def test_missing_country_raises_with_the_available_list():
    pool = [
        {"country": "US", "url": "http://us.example:8080"},
        {"country": "JP", "url": "http://jp.example:8080"},
    ]
    with pytest.raises(GeoProxyUnavailable) as caught:
        pick(pool, "BR")
    message = str(caught.value)
    assert "US" in message
    assert "JP" in message
    assert "BR" in message


def test_load_json_and_yaml_pools(tmp_path):
    json_pool = tmp_path / "pool.json"
    json_pool.write_text(
        json.dumps(
            [
                {"country": "US", "url": "http://127.0.0.1:8080"},
                {"country": "DE", "url": "http://127.0.0.1:8081"},
            ]
        ),
        encoding="utf-8",
    )
    assert pick(load_pool(json_pool), "de") == "http://127.0.0.1:8081"

    yaml_pool = tmp_path / "pool.yaml"
    yaml_pool.write_text(
        "- country: US\n  url: \"http://host:port\"\n- country: DE\n  url: http://de.host:9\n",
        encoding="utf-8",
    )
    loaded = load_pool(yaml_pool)
    assert pick(loaded, "US") == "http://host:port"
    with pytest.raises(GeoProxyUnavailable) as caught:
        pick(loaded, "FR")
    assert "US" in str(caught.value)
    assert "DE" in str(caught.value)
