"""The a4s CLI drives the same core as the app: projects, chat, loop runs, memory, skills, MCP."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent4sci.cli import app

runner = CliRunner()


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A4S_FAKE_LLM", "1")


def invoke(*args: str, input: str | None = None):
    result = runner.invoke(app, list(args), input=input)
    assert result.exit_code == 0, result.output + repr(result.exception)
    return result.output


def test_init_and_projects(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    out = invoke("init", str(folder), "--name", "海马研究", "--domain", "neuro")
    assert "海马研究" in out
    assert (folder / ".a4s" / "project.json").exists()
    assert "海马研究" in invoke("projects", "list")


def test_chat_one_shot_streams_answer_and_tools(tmp_path: Path, demo: None) -> None:
    folder = tmp_path / "study"
    invoke("init", str(folder), "--name", "p1")
    out = invoke("chat", "列出工作区里的文件", "--project", "p1", "-v")
    assert "list_files" in out and "演示模式回答" in out
    assert (folder / "notes" / "demo-notes.md").exists()
    temp = invoke("chat", "你好", "--temporary")
    assert "临时对话已丢弃" in temp


def test_run_auto_and_status(tmp_path: Path, demo: None) -> None:
    folder = tmp_path / "study"
    invoke("init", str(folder), "--name", "p1")
    out = invoke("run", "reflect-refine", "--project", "p1", "--auto", "-i", "task=写一段简介")
    assert "运行结束：done" in out
    assert (folder / "outputs" / "draft.md").exists()
    assert "done" in invoke("status")


def test_run_checkpoint_answered_in_terminal(tmp_path: Path, demo: None) -> None:
    folder = tmp_path / "study"
    invoke("init", str(folder), "--name", "p1")
    out = invoke("run", "idea-tournament", "--project", "p1", "-i", "topic=theta", input="approve\n")
    assert "人工检查点" in out and "运行结束：done" in out


def test_run_without_input_stops_instead_of_approving(tmp_path: Path, demo: None) -> None:
    invoke("init", str(tmp_path / "study"), "--name", "p1")
    result = runner.invoke(app, ["run", "idea-tournament", "--project", "p1", "-i", "topic=theta"], input="")
    assert result.exit_code == 1
    assert "终止" in result.output


def test_memory_and_models(tmp_path: Path) -> None:
    invoke("memory", "add", "global", "统计偏好", "报告效应量与 95% CI", "--description", "统计习惯")
    assert "统计偏好.md" in invoke("memory", "list")
    invoke("models", "add", "--preset", "ollama", "--model", "llama3.2", "--no-test")
    assert "llama3.2" in invoke("models", "list")


def test_skill_install_dry_run_and_mcp(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "tiny-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: tiny-skill\ndescription: A tiny test skill.\nlicense: MIT\n---\n\nDo it.\n")
    (skill / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge, to any person\n")
    out = invoke("skills", "install", str(tmp_path / "skills"), "--dry-run")
    assert "tiny-skill" in out and "ok" in out
    server = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    import sys

    out = invoke("mcp", "add", "echo", "-c", sys.executable, "-a", str(server))
    assert "shout" in out
    assert "echo" in invoke("mcp", "list")


def test_doctor_reports_missing_models() -> None:
    result = runner.invoke(app, ["doctor"])
    assert "模型档案" in result.output
    assert result.exit_code == 1  # no model configured is a failing check
