from agentic_primitives_gateway.models.tasks import Task
from agentic_primitives_gateway.primitives.tasks.redis import _parse_task


def test_parse_task_accepts_bytes() -> None:
    task = Task(id="task-1", team_run_id="run-1", title="Test task")

    parsed = _parse_task(task.model_dump_json().encode("utf-8"))

    assert parsed == task
