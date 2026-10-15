import pytest

from config import ConfigError, load_settings

VALID = {
    "GEMINI_API_KEY": "test-key",
    "DATABASE_URL": "postgresql://rag:rag@localhost:5433/rag",
    "GEMINI_CHAT_MODEL": "gemini-3.8-flash",
    "GEMINI_EMBED_MODEL": "gemini-embedding-001",
    "EMBED_DIM": "768",
}


def test_loads_valid_settings():
    settings = load_settings(VALID)
    assert settings.embed_dim == 768
    assert settings.embed_model == "gemini-embedding-001"
    assert settings.database_url.endswith(":5433/rag")


def test_blank_and_missing_values_are_all_named():
    env = {**VALID, "GEMINI_API_KEY": "   "}
    del env["DATABASE_URL"]
    with pytest.raises(ConfigError, match="GEMINI_API_KEY, DATABASE_URL"):
        load_settings(env)


def test_non_integer_dim_is_rejected():
    with pytest.raises(ConfigError, match="EMBED_DIM must be an integer"):
        load_settings({**VALID, "EMBED_DIM": "768d"})


def test_out_of_range_dim_is_rejected():
    with pytest.raises(ConfigError, match="between 128 and 3072"):
        load_settings({**VALID, "EMBED_DIM": "4096"})


def test_embed_rate_limit_defaults_to_free_tier_and_can_be_raised():
    assert load_settings(VALID).embed_per_minute == 100
    assert load_settings({**VALID, "EMBED_REQUESTS_PER_MINUTE": "1500"}).embed_per_minute == 1500


def test_chat_rate_limit_defaults_to_ten_and_rejects_nonsense():
    assert load_settings(VALID).chat_per_minute == 10
    assert load_settings({**VALID, "GEMINI_CHAT_REQUESTS_PER_MINUTE": "60"}).chat_per_minute == 60
    with pytest.raises(ConfigError, match="GEMINI_CHAT_REQUESTS_PER_MINUTE"):
        load_settings({**VALID, "GEMINI_CHAT_REQUESTS_PER_MINUTE": "0"})


def test_summary_model_defaults_to_the_chat_model():
    assert load_settings(VALID).summary_model == "gemini-3.8-flash"
    assert load_settings({**VALID, "GEMINI_SUMMARY_MODEL": "gemini-3.5-flash-lite"}).summary_model == "gemini-3.5-flash-lite"


def test_embed_token_rate_limit_defaults_below_the_free_tier():
    assert load_settings(VALID).embed_tokens_per_minute == 20000
    assert load_settings({**VALID, "EMBED_TOKENS_PER_MINUTE": "1000000"}).embed_tokens_per_minute == 1000000


def test_vision_model_defaults_to_the_summary_model():
    assert load_settings(VALID).vision_model == "gemini-3.8-flash"
    lite = {**VALID, "GEMINI_SUMMARY_MODEL": "gemini-3.5-flash-lite"}
    assert load_settings(lite).vision_model == "gemini-3.5-flash-lite"
    assert load_settings({**lite, "GEMINI_VISION_MODEL": "gemini-3.8-flash"}).vision_model == "gemini-3.8-flash"
