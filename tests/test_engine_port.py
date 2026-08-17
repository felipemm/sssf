import sssf.adw_modules as m  # noqa: F401  (import surface must resolve)
from sssf.adw_modules import agents, data_types
from sssf.adw_modules import tracer as tracer_mod


def _roster(data_dir) -> str:
    agents_block = "\n".join(
        f"""  - name: {a}
    model: openai/gpt-4o-mini
    purpose: {a}
    prompt_engineering:
      system: {data_dir}/prompt_engineering/{a}/system.md
      user: {data_dir}/prompt_engineering/{a}/user.md"""
        for a in ("planner", "builder", "reviewer", "scout", "documenter")
    )
    return f"""defaults:
  coding_agent: pi
  model: openai/gpt-4o-mini
  thinking: medium
  data_dir: adws/adw_data
agents:
{agents_block}
"""


def test_validate_accepts_starter_roster(tmp_path, monkeypatch):
    # agents.validate checks prompt files exist AND resolves the model against
    # pi's live catalog — neither is available in unit tests (ruling R1).
    monkeypatch.setattr(agents.agent_pi, "resolve_model", lambda pattern: ("openai", "gpt-4o-mini"))
    data_dir = tmp_path / "adws" / "adw_data"
    for agent in ("planner", "builder", "reviewer", "scout", "documenter"):
        d = data_dir / "prompt_engineering" / agent
        d.mkdir(parents=True)
        (d / "system.md").write_text("system")
        (d / "user.md").write_text("user")
    cfg_path = tmp_path / "sssf.config.yaml"
    cfg_path.write_text(_roster(data_dir))
    cfg = agents.load_config(cfg_path)
    agents.validate(cfg, ["planner", "builder", "reviewer", "scout", "documenter"])


def test_pi_request_carries_skill_path():
    req = data_types.PiRequest(
        prompt="p",
        system_prompt="s",
        model="openai/gpt-4o-mini",
        session_id="x",
        session_dir="/tmp",
        raw_output_path="/tmp/o.jsonl",
        skill_path="/pkg/SKILL.md",
    )
    assert req.skill_path == "/pkg/SKILL.md"


def test_tracer_creates_tickets_table(tmp_path):
    t = tracer_mod.Tracer(db_path=tmp_path / "sssf.db", events_jsonl=tmp_path / "e.jsonl")
    cols = {row[1] for row in t.conn.execute("PRAGMA table_info(tickets)")}
    assert {
        "id",
        "provider",
        "external_id",
        "title",
        "description",
        "status",
        "prompt_file",
        "adw_id",
        "source_url",
        "created_at",
        "updated_at",
    } <= cols
    t.conn.close()
