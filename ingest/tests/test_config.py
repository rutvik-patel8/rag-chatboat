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
