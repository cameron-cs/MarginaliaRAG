import os
import re
from pathlib import Path

from marginalia.config import ROOT, Settings, load_dotenv


def test_dotenv_sets_missing_keys_only(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text('# comment\n\nCP_A=1\nCP_B = "two"\nCP_C=from-file\nnot a setting\n', encoding='utf-8')
    for key in ('CP_A', 'CP_B'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CP_C', 'from-shell')
    load_dotenv(env)
    assert (os.environ['CP_A'], os.environ['CP_B'], os.environ['CP_C']) == ('1', 'two', 'from-shell')
    for key in ('CP_A', 'CP_B'):
        monkeypatch.delenv(key)


def test_missing_dotenv_is_fine(tmp_path):
    load_dotenv(tmp_path / 'absent.env')


def test_ollama_url_follows_port_unless_overridden(monkeypatch):
    monkeypatch.delenv('CP_OLLAMA_URL', raising=False)
    monkeypatch.setenv('CP_OLLAMA_PORT', '12000')
    assert Settings().ollama_url == 'http://127.0.0.1:12000'
    monkeypatch.setenv('CP_OLLAMA_URL', 'http://10.0.0.5:11434')
    assert Settings().ollama_url == 'http://10.0.0.5:11434'


def test_parallelism_setting(monkeypatch):
    monkeypatch.setenv('CP_OLLAMA_NUM_PARALLEL', '3')
    assert Settings().ollama_parallel == 3


def test_global_ollama_variables_do_not_leak_in(monkeypatch):
    # Variables meant for another Ollama install must not configure this project.
    monkeypatch.delenv('CP_OLLAMA_NUM_PARALLEL', raising=False)
    monkeypatch.setenv('OLLAMA_NUM_PARALLEL', '8')
    assert Settings().ollama_parallel == 2


def test_every_compose_variable_is_documented():
    compose = (ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
    example = (ROOT / '.env.example').read_text(encoding='utf-8')
    used = set(re.findall(r'\$\{(\w+):-', compose))
    documented = set(re.findall(r'^#?\s*(\w+)=', example, re.M))
    assert used and used <= documented, used - documented
    assert all(v.startswith('CP_') for v in used)
