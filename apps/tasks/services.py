"""Task services: board ordering (columns numbered 0..n-1) and the activity log."""
from django.db import transaction
from django.db.models import Max

from .models import ActivityLog, Task


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


# --- activity log ------------------------------------------------------------

def _user_ref(user):
    # Keep the email too, so history stays readable if the user later leaves.
    return None if user is None else {"id": user.pk, "email": user.email}


def snapshot(task):
    """The user-editable fields of a task, in JSON-friendly form."""
    return {
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "assignee": _user_ref(task.assignee),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "labels": list(task.labels),
    }


def diff(before, after):
    """{field: {"from": old, "to": new}} for every field that changed."""
    return {
        field: {"from": before[field], "to": after[field]}
        for field in after
        if before.get(field) != after[field]
    }


def log_activity(task, actor, action, changes=None):
    return ActivityLog.objects.create(task=task, actor=actor, action=action, changes=changes or {})
