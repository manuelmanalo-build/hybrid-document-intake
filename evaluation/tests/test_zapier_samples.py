import json

from scripts.generate_zapier_samples import OUT, samples


def test_committed_samples_match_the_payload_builders() -> None:
    expected = samples()

    assert sorted(path.name for path in OUT.glob("*.json")) == sorted(expected)
    for name, payload in expected.items():
        assert json.loads((OUT / name).read_text()) == json.loads(
            json.dumps(payload, ensure_ascii=False)
        ), name
