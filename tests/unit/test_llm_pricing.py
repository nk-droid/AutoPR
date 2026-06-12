from pathlib import Path

import pytest

import infra.llm.pricing as pricing


def test_load_pricing_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "llm_models.yaml"
    config_path.write_text(
        """
providers:
  test-provider:
    models:
      test-alias:
        model: test-model
        pricing:
          input_per_1m_tokens_usd: 1000.0
          output_per_1m_tokens_usd: 2000.0
""".strip(),
        encoding="utf-8",
    )

    assert pricing.load_pricing(config_path) == {"test-model": (0.001, 0.002)}


def test_estimate_cost_usd_uses_loaded_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing, "PRICING", {"test-model": (0.001, 0.002)})

    assert pricing.estimate_cost_usd("test-model", 10, 20) == pytest.approx(0.05)


def test_estimate_cost_usd_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="Unknown model"):
        pricing.estimate_cost_usd("missing-model", 1, 1)
