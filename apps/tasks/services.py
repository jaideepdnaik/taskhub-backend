"""Board ordering: keep each (project, status) column numbered 0..n-1."""
from django.db import transaction
from django.db.models import Max

from .models import Task


def next_position(project_id, status):
    """Bottom of the column."""
    current = Task.objects.filter(project_id=project_id, status=status).aggregate(m=Max("position"))
    return 0 if current["m"] is None else current["m"] + 1


def _renumber(tasks):
    """Give `tasks` (already in the desired order) positions 0..n-1; save only changes."""
    changed = []
    for index, task in enumerate(tasks):
        if task.position != index:
            task.position = index
            changed.append(task)
    if changed:
        Task.objects.bulk_update(changed, ["position"])


@transaction.atomic
def move_task(task, status, position=None):
    """Move `task` to `status` at `position` (None or too large = bottom).

    Other cards in the target column shift down to make room, and the old
    column closes the gap. Rows are locked so concurrent drags don't collide.
    """
    old_status = task.status
    target = list(
        Task.objects.select_for_update()
        .filter(project_id=task.project_id, status=status)
        .exclude(pk=task.pk)
        .order_by("position", "id")
    )
    if position is None or position > len(target):
        position = len(target)

    target.insert(position, task)
    task.status = status
    task.position = position
    task.save(update_fields=["status", "position", "updated_at"])
    _renumber(target)  # whole column in its new order; `task` already has its index

    if old_status != status:
        old_column = list(
            Task.objects.select_for_update()
            .filter(project_id=task.project_id, status=old_status)
            .order_by("position", "id")
        )
        _renumber(old_column)
    return task
